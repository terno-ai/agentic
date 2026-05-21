"""Bash execution tool with a persistent shell — cwd, env, and state persist between calls."""

from __future__ import annotations

import asyncio
import os
import re
import random
from pathlib import Path
from typing import Any

from agentic.tools.base import Tool, ToolResult

MAX_OUTPUT_CHARS = 50_000
DEFAULT_TIMEOUT = 120
# Unique sentinel unlikely to appear in real command output
_SENTINEL = "AGENTIC_DONE_7f3a9b2c"


def _tail_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"[...{omitted} chars omitted from start...]\n" + text[-max_chars:]


class PersistentShell:
    """A single long-lived bash process. cwd, env vars, and shell functions persist."""

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "--norc", "--noprofile",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
            env={**os.environ},
        )

    async def run(self, command: str, timeout_s: float) -> tuple[str, int]:
        async with self._lock:
            await self._ensure_started()
            assert self._proc and self._proc.stdin and self._proc.stdout

            # Wrap in a subshell so set/pipefail don't bleed; capture exit code with sentinel
            script = (
                f"({command})\n"
                f"__ec=$?\n"
                f"printf '\\n{_SENTINEL}:%d\\n' $__ec\n"
            )
            self._proc.stdin.write(script.encode())
            await self._proc.stdin.drain()

            lines: list[str] = []
            try:
                async with asyncio.timeout(timeout_s):
                    while True:
                        line = await self._proc.stdout.readline()
                        text = line.decode(errors="replace")
                        if text.startswith(_SENTINEL + ":"):
                            exit_code = int(text.split(":", 1)[1].strip())
                            break
                        lines.append(text)
            except (asyncio.TimeoutError, TimeoutError):
                self._proc.kill()
                self._proc = None
                raise asyncio.TimeoutError()

            return "".join(lines), exit_code

    async def terminate(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None


class BashTool(Tool):
    name = "Bash"
    description = (
        "Execute a shell command. The shell is persistent — working directory, environment "
        "variables, and shell functions survive between calls. "
        "Use run_in_background=true for fire-and-forget commands."
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

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = str(cwd or Path.cwd())
        self._shell: PersistentShell | None = None
        self._background_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]

    def _get_shell(self) -> PersistentShell:
        if self._shell is None:
            self._shell = PersistentShell(self._cwd)
        return self._shell

    async def execute(
        self,
        command: str,
        description: str = "",
        timeout: int | None = None,
        run_in_background: bool = False,
    ) -> ToolResult:
        timeout_s = min((timeout or DEFAULT_TIMEOUT * 1000), 600_000) / 1000

        if run_in_background:
            task = asyncio.create_task(self._run_oneshot(command, timeout_s))
            self._background_tasks.append(task)
            return ToolResult.ok(f"Started in background: {command}")

        return await self._run_persistent(command, timeout_s)

    async def _run_persistent(self, command: str, timeout_s: float) -> ToolResult:
        try:
            output, exit_code = await self._get_shell().run(command, timeout_s)
        except asyncio.CancelledError:
            # Agent turn was cancelled — kill the shell so it doesn't linger
            if self._shell:
                await self._shell.terminate()
                self._shell = None
            raise
        except asyncio.TimeoutError:
            return ToolResult.error(f"Command timed out after {timeout_s:.0f}s: {command}")
        except Exception as e:
            return ToolResult.error(f"Shell error: {e}")

        # Strip the leading blank line that printf '\n...' adds before the sentinel
        output = output.lstrip("\n")
        output = _tail_truncate(output, MAX_OUTPUT_CHARS)

        if exit_code != 0:
            return ToolResult(
                content=output or f"(exit code {exit_code})",
                is_error=True,
                metadata={"exit_code": exit_code},
            )
        return ToolResult.ok(output or "(no output)", exit_code=0)

    async def _run_oneshot(self, command: str, timeout_s: float) -> ToolResult:
        """Isolated subprocess for background tasks."""
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
                return ToolResult.error(f"Background command timed out: {command}")

            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")
            combined = out + (f"\n--- stderr ---\n{err}" if err and out else err)
            return ToolResult.ok(_tail_truncate(combined, MAX_OUTPUT_CHARS) or "(no output)")
        except Exception as e:
            return ToolResult.error(f"Background command failed: {e}")

    async def terminate(self) -> None:
        if self._shell:
            await self._shell.terminate()
            self._shell = None


class MonitorTool(Tool):
    """Watch a command's output line by line; stop on pattern match, line limit, or timeout."""

    name = "Monitor"
    description = (
        "Run a command and watch its output line by line as an event stream. "
        "Stops when: (1) trigger_pattern matches a line — returns immediately with that match; "
        "(2) lines_limit is reached; (3) timeout expires; or (4) the process exits. "
        "Useful for: waiting until a server prints 'ready', following test output for "
        "pass/fail, or tailing a log file until a condition appears."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to run and watch",
            },
            "trigger_pattern": {
                "type": "string",
                "description": "Regex — stop immediately when any output line matches",
            },
            "timeout": {
                "type": "integer",
                "description": "Max milliseconds to wait (default 30000, max 300000)",
            },
            "lines_limit": {
                "type": "integer",
                "description": "Stop after this many lines (default 500)",
            },
        },
        "required": ["command"],
    }

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = str(cwd or Path.cwd())

    async def execute(
        self,
        command: str,
        trigger_pattern: str | None = None,
        timeout: int | None = None,
        lines_limit: int = 500,
    ) -> ToolResult:
        timeout_s = min((timeout or 30_000), 300_000) / 1000
        pattern = re.compile(trigger_pattern) if trigger_pattern else None
        lines: list[str] = []
        stop_reason = "process exited"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
                env={**os.environ},
            )

            async def _collect() -> None:
                nonlocal stop_reason
                assert proc.stdout
                async for raw in proc.stdout:
                    line = raw.decode(errors="replace").rstrip("\n")
                    lines.append(line)
                    if pattern and pattern.search(line):
                        stop_reason = f"trigger matched: {line!r}"
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        return
                    if len(lines) >= lines_limit:
                        stop_reason = f"lines_limit ({lines_limit}) reached"
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        return

            try:
                await asyncio.wait_for(_collect(), timeout=timeout_s)
            except asyncio.TimeoutError:
                stop_reason = f"timeout ({timeout_s:.0f}s)"
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

            await proc.wait()

        except Exception as e:
            return ToolResult.error(f"Monitor error: {e}")

        output = "\n".join(lines)
        output = _tail_truncate(output, MAX_OUTPUT_CHARS)
        return ToolResult.ok(f"[stopped: {stop_reason}]\n{output}")
