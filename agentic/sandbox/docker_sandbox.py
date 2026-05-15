"""
DockerSandbox — runs commands inside a per-user persistent Docker container.

Multi-user design:
  - Each user gets their own container named  agentic-user-<user_id>
  - Each user's workspace lives at  <users_workspace_root>/<user_id>/workspace/
    and is mounted as /workspace inside their container.
  - start() reuses an already-running container for the same user, so
    reconnecting to an existing session is instant.
  - Working directory changes (cd) persist across calls via a PWD sentinel.
  - Resource limits (memory, CPU) and network mode are enforced by Docker.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

from agentic.core.config import SandboxConfig

_PWD_SENTINEL = "__AGENTIC_PWD__"
MAX_OUTPUT_CHARS = 50_000


class DockerNotAvailable(RuntimeError):
    pass


def _sanitize_user_id(user_id: str) -> str:
    """Make user_id safe for a Docker container name (lowercase alphanumeric + dash)."""
    sanitized = re.sub(r"[^a-z0-9-]", "-", user_id.lower())
    return sanitized.strip("-") or "default"


class DockerSandbox:
    def __init__(
        self,
        config: SandboxConfig,
        user_id: str = "default",
        workspace: Path | None = None,
    ):
        self._config = config
        self._user_id = _sanitize_user_id(user_id)

        # Per-user workspace: <users_workspace_root>/<user_id>/workspace/
        # Callers may pass an explicit workspace to override (e.g. in tests).
        if workspace is not None:
            self._workspace = workspace.resolve()
        else:
            self._workspace = (
                Path(config.users_workspace_root).expanduser()
                / self._user_id
                / "workspace"
            )

        # Deterministic container name — same user always gets the same container,
        # so reconnecting reuses the existing session.
        self._name = f"agentic-user-{self._user_id}"

        self._container_id: str | None = None
        self._current_dir = "/workspace"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Ensure the user's sandbox container is running.

        Reuses an existing running container for this user (fast path).
        Restarts a stopped container if one exists.
        Creates a fresh container otherwise.
        """
        _require_docker()

        if self._config.auto_build:
            await self._ensure_image()

        # Create workspace directory on the host if it doesn't exist yet
        self._workspace.mkdir(parents=True, exist_ok=True)

        # Check for an existing container
        inspect = await _run_host(
            ["docker", "inspect", "--format", "{{.State.Status}}", self._name],
            check=False,
        )

        if inspect.returncode == 0:
            status = inspect.stdout.strip()
            if status == "running":
                # Reuse the already-running container
                self._container_id = self._name
                return
            elif status in ("exited", "created", "paused"):
                # Restart the stopped container
                await _run_host(["docker", "start", self._name], check=False)
                self._container_id = self._name
                return
            else:
                # Dead / unknown state — remove and recreate
                await _run_host(["docker", "rm", "-f", self._name], check=False)

        # No usable container found — create a new one
        cmd = [
            "docker", "run",
            "--detach",
            "--name", self._name,
            "-v", f"{self._workspace}:/workspace",
            "-w", "/workspace",
            f"--memory={self._config.memory_limit}",
            f"--cpus={self._config.cpu_limit}",
            f"--network={self._config.network}",
            self._config.image,
            "tail", "-f", "/dev/null",
        ]
        result = await _run_host(cmd)
        self._container_id = result.stdout.strip()

    async def stop(self) -> None:
        """
        Stop the container. Does NOT remove it — the same container can be
        restarted next time the user connects (their filesystem state is kept).
        """
        if self._container_id:
            await _run_host(["docker", "stop", self._container_id], check=False)
            self._container_id = None

    async def destroy(self) -> None:
        """Permanently remove the container and workspace (data loss!)."""
        await _run_host(["docker", "rm", "-f", self._name], check=False)
        self._container_id = None

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def run(self, command: str, timeout_s: float = 120) -> tuple[str, int]:
        """Execute a command in the user's container. cd changes persist."""
        if not self._container_id:
            raise RuntimeError("Sandbox not started. Call start() first.")

        wrapped = (
            f"cd {_quote(self._current_dir)} 2>/dev/null || true\n"
            f"{command}\n"
            f"echo '{_PWD_SENTINEL}'\"$(pwd)\""
        )

        exec_cmd = [
            "docker", "exec",
            "--workdir", self._current_dir,
            self._container_id,
            "bash", "--noprofile", "--norc", "-c", wrapped,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            await _run_host(
                ["docker", "exec", self._container_id, "kill", "-9", "-1"],
                check=False,
            )
            return f"Command timed out after {timeout_s:.0f}s", 1

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")

        stdout, new_cwd = _extract_sentinel(stdout)
        if new_cwd:
            self._current_dir = new_cwd

        combined = stdout
        if stderr:
            combined += f"\n--- stderr ---\n{stderr}" if stdout.strip() else stderr

        if len(combined) > MAX_OUTPUT_CHARS:
            combined = combined[:MAX_OUTPUT_CHARS] + "\n... (truncated)"

        return combined, proc.returncode or 0

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    async def _ensure_image(self) -> None:
        """Build the sandbox image if missing or Dockerfile is newer."""
        dockerfile = Path(self._config.dockerfile)
        if not dockerfile.is_absolute():
            candidates = [
                self._workspace / dockerfile,
                Path(__file__).parent.parent.parent / dockerfile,
            ]
            dockerfile = next((p for p in candidates if p.exists()), dockerfile)

        check = await _run_host(
            ["docker", "image", "inspect", self._config.image], check=False
        )
        if check.returncode == 0:
            if dockerfile.exists():
                import json as _json
                from datetime import datetime, timezone
                try:
                    info = _json.loads(check.stdout)
                    image_time = datetime.fromisoformat(
                        info[0]["Created"].replace("Z", "+00:00")
                    )
                    dockerfile_time = datetime.fromtimestamp(
                        dockerfile.stat().st_mtime, tz=timezone.utc
                    )
                    if dockerfile_time <= image_time:
                        return
                    print(f"Dockerfile newer than image — rebuilding {self._config.image} ...")
                except Exception:
                    return
            else:
                return

        if not dockerfile.exists():
            raise FileNotFoundError(
                f"Sandbox Dockerfile not found: {self._config.dockerfile}"
            )

        print(f"Building sandbox image {self._config.image} from {dockerfile} ...")
        result = subprocess.run(
            ["docker", "build", "-f", str(dockerfile), "-t", self._config.image,
             str(dockerfile.parent)],
            capture_output=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker build failed (exit {result.returncode})")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_dir(self) -> str:
        return self._current_dir

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def container_name(self) -> str:
        return self._name


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _require_docker() -> None:
    result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    if result.returncode != 0:
        raise DockerNotAvailable(
            "Docker is not running or not installed. "
            "Start Docker Desktop (macOS/Windows) or the Docker daemon (Linux)."
        )


async def _run_host(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: subprocess.run(cmd, capture_output=True, text=True)
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result


def _extract_sentinel(output: str) -> tuple[str, str | None]:
    lines = output.splitlines(keepends=True)
    new_cwd: str | None = None
    clean: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith(_PWD_SENTINEL):
            new_cwd = stripped[len(_PWD_SENTINEL):]
        else:
            clean.append(line)
    return "".join(clean), new_cwd


def _quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"
