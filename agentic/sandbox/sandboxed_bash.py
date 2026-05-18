"""SandboxedBashTool — BashTool that routes commands through DockerSandbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from agentic.tools.base import ToolResult
from agentic.tools.bash import BashTool

if TYPE_CHECKING:
    from agentic.sandbox.docker_sandbox import DockerSandbox


class SandboxedBashTool(BashTool):
    """
    Drop-in replacement for BashTool that executes commands inside a
    Docker sandbox instead of the host shell.

    Working directory changes persist across calls (tracked in DockerSandbox).
    The tool falls back to the regular BashTool interface so callers don't
    need to change.
    """

    name = "Bash"
    description = (
        "Execute a shell command inside a sandboxed Docker container and return its output. "
        "The sandbox has internet access, Python, Node.js, curl, wget, git, and ffmpeg. "
        "Working directory changes (cd) persist between calls. "
        "Supports background execution with run_in_background=true."
    )

    def __init__(self, sandbox: "DockerSandbox"):
        # Don't call super().__init__() — we override execution entirely
        self._sandbox = sandbox
        self._background_tasks: list = []

    async def execute(
        self,
        command: str,
        description: str = "",
        timeout: int | None = None,
        run_in_background: bool = False,
    ) -> ToolResult:
        import asyncio

        timeout_s = min((timeout or 120_000), 600_000) / 1000

        if run_in_background:
            task = asyncio.create_task(self._run(command, timeout_s))
            self._background_tasks.append(task)
            return ToolResult.ok(f"Started in background (sandbox): {command}")

        return await self._run(command, timeout_s)

    async def _run(self, command: str, timeout_s: float) -> ToolResult:
        try:
            output, exit_code = await self._sandbox.run(command, timeout_s)
            if exit_code != 0:
                return ToolResult(
                    content=output or f"(exit code {exit_code})",
                    is_error=True,
                    metadata={"exit_code": exit_code, "sandbox": True},
                )
            return ToolResult.ok(
                output or "(no output)",
                exit_code=0,
                sandbox=True,
                cwd=self._sandbox.current_dir,
            )
        except Exception as e:
            return ToolResult.error(f"Sandbox error: {e}")

    async def terminate(self) -> None:
        # No persistent shell to clean up — execution routes through docker exec
        pass
