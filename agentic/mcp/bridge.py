"""Bridge MCP tools to agentic Tool objects."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.mcp.client import MCPClient


class MCPTool(Tool):
    """Wraps an MCP tool as an agentic Tool."""

    def __init__(self, mcp_client: "MCPClient", tool_def: dict[str, Any], server_name: str):
        self._client = mcp_client
        self._tool_def = tool_def
        self._server_name = server_name
        self._name = f"mcp__{server_name}__{tool_def['name']}"
        self._description = (
            f"[MCP:{server_name}] {tool_def.get('description', tool_def['name'])}"
        )
        self._schema = tool_def.get("inputSchema", {"type": "object", "properties": {}})

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = await self._client.call_tool(self._tool_def["name"], kwargs)
            return ToolResult.ok(result)
        except Exception as e:
            return ToolResult.error(f"MCP tool {self._tool_def['name']} failed: {e}")


class MCPServerManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self._clients: dict[str, "MCPClient"] = {}

    async def connect(self, name: str, command: str, args: list[str], env: dict[str, str]) -> "MCPClient":
        from agentic.mcp.client import MCPClient
        client = MCPClient(name, command, args, env)
        await client.start()
        self._clients[name] = client
        return client

    async def disconnect_all(self) -> None:
        for client in self._clients.values():
            await client.stop()
        self._clients.clear()

    async def get_all_tools(self) -> list[MCPTool]:
        tools = []
        for name, client in self._clients.items():
            try:
                tool_defs = await client.list_tools()
                for td in tool_defs:
                    tools.append(MCPTool(client, td, name))
            except Exception as e:
                import sys
                print(f"[MCP:{name}] failed to list tools: {e}", file=sys.stderr)
        return tools

    def is_connected(self, name: str) -> bool:
        return name in self._clients
