"""AgentTool — spawn sub-agents with isolated contexts."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, TYPE_CHECKING

from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.core.config import ConfigManager


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Spawn a sub-agent to handle a complex, focused task. "
        "The sub-agent runs with its own conversation history and returns a single result. "
        "For background tasks, use run_in_background=true and poll with the returned task_id "
        "via Agent(action='result', task_id='...')."
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
                "description": "Start sub-agent in background; returns a task_id immediately",
                "default": False,
            },
            "task_id": {
                "type": "string",
                "description": "Background task ID to retrieve result for (use with no prompt)",
            },
        },
        "required": ["description"],
    }

    def __init__(self, config_manager: "ConfigManager"):
        self._config = config_manager
        self._background_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
        self._background_results: dict[str, str] = {}
        self._background_errors: dict[str, str] = {}

    async def execute(
        self,
        description: str,
        prompt: str = "",
        model: str | None = None,
        tools: list[str] | None = None,
        run_in_background: bool = False,
        task_id: str | None = None,
    ) -> ToolResult:
        # Poll an existing background task
        if task_id:
            return self._get_result(task_id)

        if not prompt:
            return ToolResult.error("prompt is required unless task_id is provided")

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
            tid = str(uuid.uuid4())[:8]
            task = asyncio.create_task(self._run_and_store(tid, loop, prompt))
            self._background_tasks[tid] = task
            return ToolResult.ok(
                f"Sub-agent started in background (task_id={tid}): {description}\n"
                f"Poll with: Agent(description='check', task_id='{tid}')"
            )

        try:
            result = await loop.run_once(prompt)
            return ToolResult.ok(f"Sub-agent result ({description}):\n\n{result}")
        except Exception as e:
            return ToolResult.error(f"Sub-agent failed: {e}")

    async def _run_and_store(self, tid: str, loop: Any, prompt: str) -> None:
        try:
            result = await loop.run_once(prompt)
            self._background_results[tid] = result
        except Exception as e:
            self._background_errors[tid] = str(e)
        finally:
            self._background_tasks.pop(tid, None)

    def _get_result(self, task_id: str) -> ToolResult:
        if task_id in self._background_errors:
            return ToolResult.error(f"Sub-agent failed: {self._background_errors.pop(task_id)}")
        if task_id in self._background_results:
            return ToolResult.ok(self._background_results.pop(task_id))
        if task_id in self._background_tasks:
            task = self._background_tasks[task_id]
            status = "running" if not task.done() else "finishing"
            return ToolResult.ok(f"Sub-agent {task_id} is still {status}. Try again shortly.")
        return ToolResult.error(f"No background task found with task_id={task_id}")
