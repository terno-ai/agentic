"""AskUserQuestion and PushNotification tools."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Awaitable

from agentic.tools.base import Tool, ToolResult


class AskUserQuestionTool(Tool):
    name = "AskUserQuestion"
    description = (
        "Ask the user a clarifying question and wait for their response. "
        "Use when you need additional information to proceed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask the user"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of suggested answers",
            },
        },
        "required": ["question"],
    }

    def __init__(self, ask_fn: Callable[[str, list[str]], Awaitable[str]] | None = None):
        self._ask_fn = ask_fn

    async def execute(self, question: str, options: list[str] | None = None) -> ToolResult:
        if self._ask_fn:
            answer = await self._ask_fn(question, options or [])
            return ToolResult.ok(answer)
        return ToolResult.ok(f"(Question asked: {question})")


class PushNotificationTool(Tool):
    name = "PushNotification"
    description = "Send a notification to the user (desktop notification or terminal bell)."
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Notification title"},
            "message": {"type": "string", "description": "Notification message"},
        },
        "required": ["title", "message"],
    }

    async def execute(self, title: str, message: str) -> ToolResult:
        import platform
        import subprocess

        system = platform.system()
        try:
            if system == "Darwin":
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
            elif system == "Linux":
                subprocess.run(["notify-send", title, message], check=False, capture_output=True)
        except Exception:
            pass

        print(f"\a", end="", flush=True)  # Terminal bell
        return ToolResult.ok(f"Notification sent: {title}")
