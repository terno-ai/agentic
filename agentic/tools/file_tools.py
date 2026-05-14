"""Read, Write, and Edit file tools."""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from agentic.tools.base import Tool, ToolResult


class ReadTool(Tool):
    name = "Read"
    description = (
        "Read a file from the filesystem. "
        "Returns file contents with line numbers. "
        "Use offset and limit to read specific sections of large files."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
            "limit": {"type": "integer", "description": "Maximum number of lines to read"},
        },
        "required": ["file_path"],
    }

    async def execute(self, file_path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult.error(f"File not found: {file_path}")
        if not path.is_file():
            return ToolResult.error(f"Not a file: {file_path}")

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult.error(f"Cannot read {file_path}: {e}")

        lines = text.splitlines(keepends=True)
        start = max(0, offset - 1) if offset else 0
        end = start + limit if limit else len(lines)
        selected = lines[start:end]

        numbered = "".join(
            f"{i + start + 1}\t{line}" for i, line in enumerate(selected)
        )
        return ToolResult.ok(numbered, lines_read=len(selected), total_lines=len(lines))


class WriteTool(Tool):
    name = "Write"
    description = (
        "Create a new file or completely overwrite an existing one. "
        "Use this whenever the user asks you to create, generate, or write a file. "
        "Never output file contents in a markdown block instead of calling this tool. "
        "Use Edit instead when you only need to change part of an existing file."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to write"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, file_path: str, content: str) -> ToolResult:
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            lines = content.count("\n") + 1
            return ToolResult.ok(f"Written {len(content)} bytes ({lines} lines) to {file_path}")
        except Exception as e:
            return ToolResult.error(f"Cannot write {file_path}: {e}")


class EditTool(Tool):
    name = "Edit"
    description = (
        "Replace an exact string in a file. The old_string must match exactly (including whitespace). "
        "Set replace_all=true to replace every occurrence."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "old_string": {"type": "string", "description": "Exact text to find and replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult.error(f"File not found: {file_path}")

        try:
            original = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"Cannot read {file_path}: {e}")

        if old_string not in original:
            return ToolResult.error(
                f"old_string not found in {file_path}. "
                "Check whitespace and indentation match exactly."
            )

        count = original.count(old_string)
        if count > 1 and not replace_all:
            return ToolResult.error(
                f"old_string appears {count} times. Use replace_all=true or provide more context."
            )

        if replace_all:
            updated = original.replace(old_string, new_string)
            n = count
        else:
            updated = original.replace(old_string, new_string, 1)
            n = 1

        try:
            path.write_text(updated, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"Cannot write {file_path}: {e}")

        diff = list(difflib.unified_diff(
            original.splitlines(), updated.splitlines(),
            fromfile=f"a/{path.name}", tofile=f"b/{path.name}",
            lineterm="",
        ))
        diff_text = "\n".join(diff[:50])
        return ToolResult.ok(f"Replaced {n} occurrence(s) in {file_path}\n{diff_text}")
