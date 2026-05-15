"""
Sandboxed file tools — remap /workspace paths to the real host workspace.

When the sandbox is active the agent sees /workspace as its working
directory (inside the container). The Read/Write/Edit tools run on the
host, so /workspace doesn't exist there. These wrappers transparently
translate any /workspace/... path to the equivalent host path before
calling the underlying tool, so the agent can use /workspace paths
consistently and writes always land in the right place.

  Agent calls:  Write(file_path="/workspace/game.py", content="...")
  Tool rewrites path to:  /Users/me/project/game.py  (host path)
  Container reads:        /workspace/game.py          (same file via mount)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic.tools.base import ToolResult
from agentic.tools.file_tools import ReadTool, WriteTool, EditTool

_WORKSPACE_PREFIX = "/workspace"


def _remap(file_path: str, workspace: Path) -> str:
    """Translate /workspace/... → <host_workspace>/... leaving other paths unchanged."""
    if file_path == _WORKSPACE_PREFIX or file_path.startswith(_WORKSPACE_PREFIX + "/"):
        relative = file_path[len(_WORKSPACE_PREFIX):].lstrip("/")
        return str(workspace / relative) if relative else str(workspace)
    return file_path


class SandboxedReadTool(ReadTool):
    def __init__(self, workspace: Path):
        self._workspace = workspace

    async def execute(self, file_path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        return await super().execute(
            file_path=_remap(file_path, self._workspace),
            offset=offset,
            limit=limit,
        )


class SandboxedWriteTool(WriteTool):
    def __init__(self, workspace: Path):
        self._workspace = workspace

    async def execute(self, file_path: str, content: str) -> ToolResult:
        return await super().execute(
            file_path=_remap(file_path, self._workspace),
            content=content,
        )


class SandboxedEditTool(EditTool):
    def __init__(self, workspace: Path):
        self._workspace = workspace

    async def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        return await super().execute(
            file_path=_remap(file_path, self._workspace),
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
