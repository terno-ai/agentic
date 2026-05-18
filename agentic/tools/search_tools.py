"""Grep, Glob, and LS tools — structured file search without raw bash.

All three tools accept an optional `sandbox` parameter (DockerSandbox instance).
When present, Grep and LS route through `docker exec` so they search the
container filesystem instead of the host. Glob always works on the host via
the path-remapping the sandbox uses for file tools.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.sandbox.docker_sandbox import DockerSandbox

MAX_GREP_RESULTS = 100
MAX_LS_ENTRIES = 500


class GrepTool(Tool):
    name = "Grep"
    description = (
        "Search for a pattern in files. Uses ripgrep (rg) if available, falls back to grep. "
        "Returns file:line:match lines. Prefer this over Bash(rg ...) for code search."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {
                "type": "string",
                "description": "File or directory to search (default: current working dir)",
            },
            "glob": {
                "type": "string",
                "description": "Restrict search to files matching this glob (e.g. '*.py')",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive match (default true)",
                "default": True,
            },
            "context_lines": {
                "type": "integer",
                "description": "Lines of context before and after each match (default 0)",
                "default": 0,
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, sandbox: "DockerSandbox | None" = None) -> None:
        self._sandbox = sandbox

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> ToolResult:
        if self._sandbox is not None:
            return await self._exec_in_sandbox(pattern, path, glob, case_sensitive, context_lines)
        return await self._exec_host(pattern, path, glob, case_sensitive, context_lines)

    async def _exec_host(self, pattern: str, path: str, glob: str | None,
                         case_sensitive: bool, context_lines: int) -> ToolResult:
        use_rg = shutil.which("rg") is not None
        cmd = self._build_cmd(pattern, path, glob, case_sensitive, context_lines, use_rg)
        return await _run_search_cmd(cmd)

    async def _exec_in_sandbox(self, pattern: str, path: str, glob: str | None,
                                case_sensitive: bool, context_lines: int) -> ToolResult:
        # Build command string for the container (rg is pre-installed in the sandbox image)
        parts = ["rg", "--line-number", "--no-heading", "--color=never"]
        if not case_sensitive:
            parts.append("--ignore-case")
        if context_lines:
            parts += ["--context", str(context_lines)]
        if glob:
            parts += ["--glob", glob]
        parts += [pattern, path]
        cmd_str = " ".join(shlex_quote(p) for p in parts)
        try:
            output, exit_code = await self._sandbox.run(cmd_str, timeout_s=30)
        except Exception as e:
            return ToolResult.error(f"Sandbox grep failed: {e}")
        if exit_code not in (0, 1):
            return ToolResult.error(output or f"rg exited {exit_code}")
        return _format_grep_output(output)

    @staticmethod
    def _build_cmd(pattern: str, path: str, glob: str | None,
                   case_sensitive: bool, context_lines: int, use_rg: bool) -> list[str]:
        if use_rg:
            cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
            if not case_sensitive:
                cmd.append("--ignore-case")
            if context_lines:
                cmd += ["--context", str(context_lines)]
            if glob:
                cmd += ["--glob", glob]
            cmd += [pattern, path]
        else:
            cmd = ["grep", "-rn", "--color=never"]
            if not case_sensitive:
                cmd.append("-i")
            if context_lines:
                cmd += [f"-C{context_lines}"]
            if glob:
                cmd += ["--include", glob]
            cmd += [pattern, path]
        return cmd


class GlobTool(Tool):
    name = "Glob"
    description = (
        "List files matching a glob pattern. "
        "Use this to discover files before reading them. "
        "Patterns like '**/*.py' work. Returns sorted list of matching paths."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern relative to path (e.g. '**/*.py', 'src/*.ts')",
            },
            "path": {
                "type": "string",
                "description": "Base directory (default: current working dir)",
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, sandbox: "DockerSandbox | None" = None) -> None:
        self._sandbox = sandbox

    async def execute(self, pattern: str, path: str = ".") -> ToolResult:
        # For sandbox, remap /workspace → host workspace so glob works on mounted files
        if self._sandbox is not None:
            if path == "/workspace" or path.startswith("/workspace/"):
                rel = path[len("/workspace"):].lstrip("/")
                path = str(self._sandbox._workspace / rel) if rel else str(self._sandbox._workspace)

        base = Path(path).resolve()
        if not base.exists():
            return ToolResult.error(f"Path not found: {path}")
        try:
            matches = sorted(base.glob(pattern))
        except Exception as e:
            return ToolResult.error(f"Glob error: {e}")

        if not matches:
            return ToolResult.ok("No files matched.")

        lines = [str(m.relative_to(base)) for m in matches]
        return ToolResult.ok("\n".join(lines), file_count=len(lines))


class LSTool(Tool):
    name = "LS"
    description = (
        "List files and directories. Returns a structured listing with sizes and types. "
        "Use this instead of Bash(ls) for directory exploration."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list (default: current working dir)",
            },
            "all": {
                "type": "boolean",
                "description": "Include hidden files (default false)",
                "default": False,
            },
        },
    }

    def __init__(self, sandbox: "DockerSandbox | None" = None) -> None:
        self._sandbox = sandbox

    async def execute(self, path: str = ".", all: bool = False) -> ToolResult:
        if self._sandbox is not None:
            return await self._exec_in_sandbox(path, all)
        return self._exec_host(path, all)

    def _exec_host(self, path: str, show_hidden: bool) -> ToolResult:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            return ToolResult.error(f"Path not found: {path}")
        if not target.is_dir():
            return ToolResult.error(f"Not a directory: {path}")

        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")

        lines = []
        total = 0
        for entry in entries:
            total += 1
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
                size_str = _human_size(stat.st_size) if entry.is_file() else ""
                kind = "/" if entry.is_dir() else ("*" if os.access(entry, os.X_OK) else " ")
                lines.append(f"{kind} {entry.name:<40} {size_str}")
            except Exception:
                lines.append(f"  {entry.name}")

            if len(lines) >= MAX_LS_ENTRIES:
                # Count remaining without re-iterating
                remaining = sum(1 for _ in entries)  # consume the rest of the iterator
                if remaining:
                    lines.append(f"... ({remaining} more entries)")
                break

        if not lines:
            return ToolResult.ok("(empty directory)")
        return ToolResult.ok("\n".join(lines))

    async def _exec_in_sandbox(self, path: str, show_hidden: bool) -> ToolResult:
        flags = "-la" if show_hidden else "-l"
        cmd = f"ls {flags} --color=never {path} 2>&1"
        try:
            output, exit_code = await self._sandbox.run(cmd, timeout_s=10)
        except Exception as e:
            return ToolResult.error(f"Sandbox ls failed: {e}")
        if exit_code != 0:
            return ToolResult.error(output or f"ls exited {exit_code}")
        return ToolResult.ok(output or "(empty)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def shlex_quote(s: str) -> str:
    """Minimal shell quoting for single arguments."""
    import shlex
    return shlex.quote(s)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n //= 1024
    return f"{n:.0f}TB"


async def _run_search_cmd(cmd: list[str]) -> ToolResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return ToolResult.error("Search timed out after 30s")
    except Exception as e:
        return ToolResult.error(f"Search failed: {e}")

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()

    if proc.returncode not in (0, 1):
        return ToolResult.error(err or f"Search exited with code {proc.returncode}")

    return _format_grep_output(out)


def _format_grep_output(out: str) -> ToolResult:
    if not out:
        return ToolResult.ok("No matches found.")
    lines = out.splitlines()
    truncated = ""
    if len(lines) > MAX_GREP_RESULTS:
        truncated = f"\n[...{len(lines) - MAX_GREP_RESULTS} more matches omitted]"
        lines = lines[:MAX_GREP_RESULTS]
    return ToolResult.ok("\n".join(lines) + truncated, match_count=len(lines))
