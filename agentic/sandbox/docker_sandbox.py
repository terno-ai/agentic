"""
DockerSandbox — runs commands inside a persistent Docker container.

Design:
  - One container is started per agent session (docker run -d).
  - Each command is executed via `docker exec`.
  - Working directory is tracked in Python and injected as --workdir on
    every exec call. A PWD sentinel at the end of each command captures
    `cd` changes so they persist across calls.
  - Resource limits (memory, CPU) and network mode are enforced by Docker.
  - The container is stopped/removed when the session ends.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

from agentic.core.config import SandboxConfig

# Sentinel used to extract the final working directory from command output.
_PWD_SENTINEL = "__AGENTIC_PWD__"

MAX_OUTPUT_CHARS = 50_000


class DockerNotAvailable(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, config: SandboxConfig, workspace: Path):
        self._config = config
        self._workspace = workspace.resolve()
        self._container_id: str | None = None
        self._current_dir = "/workspace"  # tracks cwd inside container
        self._name = f"agentic-sandbox-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Build image if needed, then start the sandbox container."""
        _require_docker()

        if self._config.auto_build:
            await self._ensure_image()

        # Run as root inside the container. Root bypasses Unix DAC checks, so
        # /workspace is always writable regardless of the host directory's uid.
        # Security is provided by Docker's namespace/cgroup isolation, not by
        # running as a non-root uid inside the container.
        cmd = [
            "docker", "run",
            "--detach",
            "--rm",                                   # auto-remove on stop
            "--name", self._name,
            "-v", f"{self._workspace}:/workspace",   # read-write by default
            "-w", "/workspace",
            f"--memory={self._config.memory_limit}",
            f"--cpus={self._config.cpu_limit}",
            f"--network={self._config.network}",
            self._config.image,
            "tail", "-f", "/dev/null",               # keep container alive
        ]
        result = await _run_host(cmd)
        self._container_id = result.stdout.strip()

    async def stop(self) -> None:
        if self._container_id:
            await _run_host(["docker", "stop", self._container_id], check=False)
            self._container_id = None

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def run(self, command: str, timeout_s: float = 120) -> tuple[str, int]:
        """
        Execute a shell command inside the container.

        Returns (output, exit_code).
        Working directory changes made via `cd` persist across calls.
        """
        if not self._container_id:
            raise RuntimeError("Sandbox not started. Call start() first.")

        # Wrap the command so we can capture the final working directory.
        # The sentinel line is always printed last, even if the command fails.
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

        # Extract and strip the sentinel line to update tracked cwd
        stdout, new_cwd = _extract_sentinel(stdout)
        if new_cwd:
            self._current_dir = new_cwd

        combined = stdout
        if stderr:
            combined += f"\n--- stderr ---\n{stderr}" if stdout.strip() else stderr

        if len(combined) > MAX_OUTPUT_CHARS:
            combined = combined[:MAX_OUTPUT_CHARS] + f"\n... (truncated)"

        return combined, proc.returncode or 0

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    async def _ensure_image(self) -> None:
        """Build the sandbox image if it doesn't exist or the Dockerfile is newer."""
        # Find the Dockerfile
        dockerfile = Path(self._config.dockerfile)
        if not dockerfile.is_absolute():
            candidates = [
                self._workspace / dockerfile,
                Path(__file__).parent.parent.parent / dockerfile,
            ]
            dockerfile = next((p for p in candidates if p.exists()), dockerfile)

        check = await _run_host(
            ["docker", "image", "inspect", self._config.image],
            check=False,
        )
        if check.returncode == 0:
            # Image exists — check if Dockerfile is newer than the image
            if dockerfile.exists():
                import json as _json
                try:
                    info = _json.loads(check.stdout)
                    created_str = info[0].get("Created", "")
                    from datetime import datetime, timezone
                    image_time = datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    )
                    dockerfile_time = datetime.fromtimestamp(
                        dockerfile.stat().st_mtime, tz=timezone.utc
                    )
                    if dockerfile_time <= image_time:
                        return  # image is up to date
                    print(f"Dockerfile is newer than image — rebuilding {self._config.image} ...")
                except Exception:
                    return  # can't compare — keep existing image
            else:
                return  # no dockerfile to compare against

        if not dockerfile.exists():
            raise FileNotFoundError(
                f"Sandbox Dockerfile not found: {self._config.dockerfile}\n"
                f"Searched: {[str(c) for c in candidates]}"
            )

        print(f"Building sandbox image {self._config.image} from {dockerfile} ...")
        build_cmd = [
            "docker", "build",
            "-f", str(dockerfile),
            "-t", self._config.image,
            str(dockerfile.parent),
        ]
        result = subprocess.run(build_cmd, capture_output=False)
        if result.returncode != 0:
            raise RuntimeError(f"docker build failed (exit {result.returncode})")

    @property
    def current_dir(self) -> str:
        return self._current_dir


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _require_docker() -> None:
    result = subprocess.run(
        ["docker", "info"], capture_output=True, timeout=10
    )
    if result.returncode != 0:
        raise DockerNotAvailable(
            "Docker is not running or not installed. "
            "Start Docker Desktop (macOS/Windows) or the Docker daemon (Linux) "
            "and try again."
        )


async def _run_host(
    cmd: list[str], check: bool = True
) -> subprocess.CompletedProcess:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: subprocess.run(cmd, capture_output=True, text=True),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n{result.stderr}"
        )
    return result


def _extract_sentinel(output: str) -> tuple[str, str | None]:
    """Strip the PWD sentinel line from output and return (clean_output, new_cwd)."""
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
    """Shell-quote a path (simple single-quote wrapping)."""
    return "'" + path.replace("'", "'\\''") + "'"
