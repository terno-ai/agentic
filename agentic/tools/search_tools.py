"""Grep and Glob tools — structured file search without raw bash."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from agentic.tools.base import Tool, ToolResult

MAX_GREP_RESULTS = 100


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

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        case_sensitive: bool = True,
        context_lines: int = 0,
    ) -> ToolResult:
        use_rg = shutil.which("rg") is not None
        cmd = self._build_cmd(pattern, path, glob, case_sensitive, context_lines, use_rg)
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

        # rg / grep exit 1 when no matches (not an error)
        if proc.returncode not in (0, 1):
            return ToolResult.error(err or f"Search exited with code {proc.returncode}")

        if not out:
            return ToolResult.ok("No matches found.")

        lines = out.splitlines()
        truncated = ""
        if len(lines) > MAX_GREP_RESULTS:
            truncated = f"\n[...{len(lines) - MAX_GREP_RESULTS} more matches omitted]"
            lines = lines[:MAX_GREP_RESULTS]

        return ToolResult.ok("\n".join(lines) + truncated, match_count=len(lines))

    @staticmethod
    def _build_cmd(
        pattern: str,
        path: str,
        glob: str | None,
        case_sensitive: bool,
        context_lines: int,
        use_rg: bool,
    ) -> list[str]:
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

    async def execute(self, pattern: str, path: str = ".") -> ToolResult:
        base = Path(path).resolve()
        if not base.exists():
            return ToolResult.error(f"Path not found: {path}")

        try:
            matches = sorted(base.glob(pattern))
        except Exception as e:
            return ToolResult.error(f"Glob error: {e}")

        if not matches:
            return ToolResult.ok("No files matched.")

        # Show relative paths for readability
        lines = [str(m.relative_to(base)) for m in matches]
        return ToolResult.ok("\n".join(lines), file_count=len(lines))
