"""Tool registry — registration, lookup, and dispatch."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.permissions.manager import PermissionManager


class ToolRegistry:
    def __init__(self, permission_manager: "PermissionManager | None" = None):
        self._tools: dict[str, Tool] = {}
        self._permissions = permission_manager

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_many(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_anthropic_schema() for t in self._tools.values()]

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult.error(f"Unknown tool: {tool_name}")

        if self._permissions:
            allowed, reason = await self._permissions.check(tool_name, tool_input)
            if not allowed:
                return ToolResult.error(f"Permission denied: {reason}")

        try:
            return await tool.execute(**tool_input)
        except TypeError as e:
            return ToolResult.error(f"Invalid tool input for {tool_name}: {e}")
        except Exception as e:
            return ToolResult.error(f"Tool {tool_name} failed: {e}")

    def names(self) -> list[str]:
        return list(self._tools.keys())
