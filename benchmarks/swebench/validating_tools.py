"""
Validating wrappers around Edit and Write tools.

After every Python file edit/write, automatically runs `py_compile` and
appends the result to the ToolResult. If compilation fails the result is
marked as an error so the agent sees it immediately and self-corrects.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from agentic.tools.base import ToolResult
from agentic.tools.file_tools import EditTool, WriteTool


def _check_syntax(file_path: str) -> tuple[bool, str]:
    """
    Run py_compile on file_path.
    Returns (ok, message) where ok=True means no syntax errors.
    """
    try:
        result = subprocess.run(
            ["python", "-m", "py_compile", file_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, "py_compile: OK"
        err = (result.stderr or result.stdout).strip()
        return False, f"py_compile FAILED:\n{err}"
    except subprocess.TimeoutExpired:
        return True, "py_compile: skipped (timeout)"
    except Exception as e:
        return True, f"py_compile: skipped ({e})"


def _is_python(file_path: str) -> bool:
    return Path(file_path).suffix == ".py"


class ValidatingEditTool(EditTool):
    """EditTool that appends a py_compile check to every Python file edit."""

    async def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        result = await super().execute(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
        if result.is_error or not _is_python(file_path):
            return result

        ok, msg = _check_syntax(file_path)
        result.content += f"\n\n{msg}"
        if not ok:
            result.is_error = True
        return result


class ValidatingWriteTool(WriteTool):
    """WriteTool that appends a py_compile check after writing a Python file."""

    async def execute(self, file_path: str, content: str) -> ToolResult:
        result = await super().execute(file_path=file_path, content=content)
        if result.is_error or not _is_python(file_path):
            return result

        ok, msg = _check_syntax(file_path)
        result.content += f"\n\n{msg}"
        if not ok:
            result.is_error = True
        return result
