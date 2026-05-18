"""Read, Write, and Edit file tools."""

from __future__ import annotations

import base64
import difflib
import re
from pathlib import Path

from agentic.tools.base import Tool, ToolResult

MAX_READ_CHARS = 50_000

# Files that commonly contain secrets — warn before reading
_SENSITIVE_PATTERNS = re.compile(
    r"(^|/)("
    r"\.env(\.\w+)?|"
    r"\.secrets?|"
    r"id_rsa|id_ed25519|id_ecdsa|id_dsa|"
    r".*\.pem|.*\.key|.*\.p12|.*\.pfx|"
    r"credentials(\.json)?|"
    r"service.account\.json|"
    r".*\.netrc|"
    r"\.aws/credentials|"
    r"\.ssh/.*"
    r")$",
    re.IGNORECASE,
)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _tail_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"[...{omitted:,} chars omitted from start...]\n" + text[-max_chars:]


def _norm_lines(text: str) -> str:
    """Strip trailing whitespace from every line — for fuzzy matching."""
    return "\n".join(line.rstrip() for line in text.splitlines())


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
        path = Path(file_path).expanduser()
        if not path.exists():
            return ToolResult.error(f"File not found: {file_path}")
        if not path.is_file():
            return ToolResult.error(f"Not a file: {file_path}")

        # Warn on sensitive files (don't block — agent may have a legitimate reason)
        if _SENSITIVE_PATTERNS.search(file_path.replace("\\", "/")):
            warning = f"⚠️  Reading potentially sensitive file: {file_path}\n\n"
        else:
            warning = ""

        # Images: return as base64 vision content block
        suffix = path.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            try:
                raw = path.read_bytes()
                b64 = base64.standard_b64encode(raw).decode()
                media_type = _IMAGE_MEDIA_TYPES[suffix]
                # Return a special marker the agent loop can detect and convert to
                # an image content block for vision-capable models.
                return ToolResult.ok(
                    f"[image:{media_type}:{b64}]",
                    image=True, media_type=media_type,
                )
            except Exception as e:
                return ToolResult.error(f"Cannot read image {file_path}: {e}")

        # Detect binary
        try:
            raw = path.read_bytes()
            if b"\x00" in raw[:8192]:
                return ToolResult.error(f"Binary file (not readable as text): {file_path}")
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return ToolResult.error(f"Cannot read {file_path}: {e}")

        lines = text.splitlines(keepends=True)
        start = max(0, offset - 1) if offset else 0
        end = start + limit if limit else len(lines)
        selected = lines[start:end]

        numbered = "".join(
            f"{i + start + 1}\t{line}" for i, line in enumerate(selected)
        )
        numbered = _tail_truncate(numbered, MAX_READ_CHARS)
        return ToolResult.ok(warning + numbered, lines_read=len(selected), total_lines=len(lines))


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
            return ToolResult.ok(f"Written {len(content):,} bytes ({lines:,} lines) to {file_path}")
        except Exception as e:
            return ToolResult.error(f"Cannot write {file_path}: {e}")


class EditTool(Tool):
    name = "Edit"
    description = (
        "Replace an exact string in a file. The old_string must match the file content "
        "(trailing whitespace per line is normalized automatically). "
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

        # Try exact match first; fall back to trailing-whitespace-normalized match
        if old_string in original:
            working, working_old = original, old_string
        else:
            norm_orig = _norm_lines(original)
            norm_old = _norm_lines(old_string)
            if norm_old in norm_orig:
                working, working_old = norm_orig, norm_old
            else:
                return ToolResult.error(
                    f"old_string not found in {file_path}.\n"
                    "Tip: check indentation and that the text matches the file exactly."
                )

        count = working.count(working_old)
        if count > 1 and not replace_all:
            return ToolResult.error(
                f"old_string appears {count} times in {file_path}. "
                "Use replace_all=true or provide more surrounding context to make it unique."
            )

        n = count if replace_all else 1
        updated = working.replace(working_old, new_string) if replace_all else working.replace(working_old, new_string, 1)

        try:
            path.write_text(updated, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"Cannot write {file_path}: {e}")

        diff = list(difflib.unified_diff(
            original.splitlines(), updated.splitlines(),
            fromfile=f"a/{path.name}", tofile=f"b/{path.name}",
            lineterm="",
        ))
        diff_text = "\n".join(diff[:80])
        summary = f"Replaced {n} occurrence(s) in {path.name}"
        return ToolResult.ok(f"{summary}\n{diff_text}" if diff_text else summary)


class MultiEditTool(Tool):
    """Apply multiple edits to the same file atomically in one call.

    Each edit is `{old_string, new_string, replace_all?}` applied in sequence.
    If any edit fails, the file is left unchanged (all-or-nothing).
    """
    name = "MultiEdit"
    description = (
        "Apply multiple find-and-replace edits to one file in a single call. "
        "Edits are applied in order; if any fails the file is not modified. "
        "Use this instead of multiple Edit calls on the same file."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "edits": {
                "type": "array",
                "description": "List of edits to apply in order",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False},
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["file_path", "edits"],
    }

    async def execute(self, file_path: str, edits: list[dict]) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult.error(f"File not found: {file_path}")
        try:
            original = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"Cannot read {file_path}: {e}")

        current = original
        applied = 0
        for i, edit in enumerate(edits):
            old = edit.get("old_string", "")
            new = edit.get("new_string", "")
            replace_all = edit.get("replace_all", False)

            if old not in current:
                norm_cur = _norm_lines(current)
                norm_old = _norm_lines(old)
                if norm_old in norm_cur:
                    current, old = norm_cur, norm_old
                else:
                    return ToolResult.error(
                        f"Edit {i + 1}/{len(edits)}: old_string not found in {file_path}. "
                        "File was not modified."
                    )

            count = current.count(old)
            if count > 1 and not replace_all:
                return ToolResult.error(
                    f"Edit {i + 1}/{len(edits)}: old_string appears {count} times. "
                    "Use replace_all=true or provide more context. File was not modified."
                )

            current = current.replace(old, new) if replace_all else current.replace(old, new, 1)
            applied += 1

        try:
            path.write_text(current, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"Cannot write {file_path}: {e}")

        diff = list(difflib.unified_diff(
            original.splitlines(), current.splitlines(),
            fromfile=f"a/{path.name}", tofile=f"b/{path.name}",
            lineterm="",
        ))
        diff_text = "\n".join(diff[:120])
        summary = f"Applied {applied}/{len(edits)} edits to {path.name}"
        return ToolResult.ok(f"{summary}\n{diff_text}" if diff_text else summary)
