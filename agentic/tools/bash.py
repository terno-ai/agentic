"""Bash execution tool with timeout and output capture."""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from typing import Any

from agentic.tools.base import Tool, ToolResult

MAX_OUTPUT_CHARS = 50_000
DEFAULT_TIMEOUT = 120


class BashTool(Tool):
    name = "Bash"
    description = (
        "Execute a shell command and return its output. "
        "Supports background execution with run_in_background=true. "
        "Working directory persists between calls within a session."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "description": {"type": "string", "description": "Brief description of what this command does"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in milliseconds (default 120000, max 600000)",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Run in background; returns immediately",
                "default": False,
            },
        },
        "required": ["command"],
    }

    def __init__(self, cwd: Path | None = None):
        self._cwd = str(cwd or Path.cwd())
        self._background_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]

    async def execute(
        self,
        command: str,
        description: str = "",
        timeout: int | None = None,
        run_in_background: bool = False,
    ) -> ToolResult:
        timeout_s = min((timeout or DEFAULT_TIMEOUT * 1000), 600_000) / 1000

        if run_in_background:
            task = asyncio.create_task(self._run(command, timeout_s))
            self._background_tasks.append(task)
            return ToolResult.ok(f"Started in background: {command}")

        return await self._run(command, timeout_s)

    async def _run(self, command: str, timeout_s: float) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env={**os.environ},
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult.error(f"Command timed out after {timeout_s:.0f}s: {command}")

            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")
            combined = out
            if err:
                combined += f"\n--- stderr ---\n{err}" if out else err

            if len(combined) > MAX_OUTPUT_CHARS:
                combined = combined[:MAX_OUTPUT_CHARS] + f"\n... (truncated at {MAX_OUTPUT_CHARS} chars)"

            rc = proc.returncode or 0
            if rc != 0:
                return ToolResult(
                    content=combined or f"(exit code {rc})",
                    is_error=True,
                    metadata={"exit_code": rc},
                )
            return ToolResult.ok(combined or "(no output)", exit_code=0)

        except Exception as e:
            return ToolResult.error(f"Failed to run command: {e}")
