"""MCP client — connects to MCP servers via stdio."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MCPError(Exception):
    pass


class MCPClient:
    """Communicates with an MCP server process via stdio JSON-RPC 2.0."""

    def __init__(self, server_name: str, command: str, args: list[str], env: dict[str, str]):
        self.server_name = server_name
        self.command = command
        self.args = args
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}  # type: ignore[type-arg]
        self._reader_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._tools_cache: list[dict[str, Any]] | None = None

    async def start(self) -> None:
        import os
        merged_env = {**os.environ, **self.env}

        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._initialize()

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()

    async def _initialize(self) -> None:
        await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"roots": {}, "sampling": {}},
            "clientInfo": {"name": "agentic", "version": "0.1.0"},
        })
        await self._notify("notifications/initialized", {})

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache
        result = await self._call("tools/list", {})
        self._tools_cache = result.get("tools", [])
        return self._tools_cache

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        result = await self._call("tools/call", {"name": tool_name, "arguments": arguments})
        content = result.get("content", [])
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts) if parts else str(result)

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        req_id = self._request_id
        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send(request)

        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            del self._pending[req_id]
            raise MCPError(f"Timeout calling {method}")

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._send(notification)

    async def _send(self, data: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("MCP server not running")
        line = json.dumps(data) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        while True:
            try:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                data = json.loads(line.decode())
                req_id = data.get("id")
                if req_id and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if "error" in data:
                        future.set_exception(MCPError(str(data["error"])))
                    else:
                        future.set_result(data.get("result", {}))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"MCP read error: {e}")
