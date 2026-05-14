"""AgentTool — spawn sub-agents with isolated contexts."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.core.config import ConfigManager


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Spawn a sub-agent to handle a complex, focused task. "
        "The sub-agent runs with its own conversation history and returns a single result. "
        "Use for tasks that are clearly separable and benefit from a clean context."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short description of what the agent will do"},
            "prompt": {"type": "string", "description": "Detailed task prompt for the sub-agent"},
            "model": {
                "type": "string",
                "description": "Model override (e.g. claude-haiku-4-5-20251001 for fast tasks)",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool names to allow (defaults to all)",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Start sub-agent in background",
                "default": False,
            },
        },
        "required": ["description", "prompt"],
    }

    def __init__(self, config_manager: "ConfigManager"):
        self._config = config_manager
        self._background_results: dict[str, str] = {}

    async def execute(
        self,
        description: str,
        prompt: str,
        model: str | None = None,
        tools: list[str] | None = None,
        run_in_background: bool = False,
    ) -> ToolResult:
        # Import here to avoid circular imports
        from agentic.core.agent import AgentLoop

        settings = self._config.settings
        sub_model = model or settings.model

        loop = AgentLoop(
            config=self._config,
            model=sub_model,
            allowed_tools=tools,
            is_subagent=True,
        )

        if run_in_background:
            import asyncio
            task = asyncio.create_task(loop.run_once(prompt))
            return ToolResult.ok(f"Sub-agent started in background: {description}")

        try:
            result = await loop.run_once(prompt)
            return ToolResult.ok(f"Sub-agent result ({description}):\n\n{result}")
        except Exception as e:
            return ToolResult.error(f"Sub-agent failed: {e}")
