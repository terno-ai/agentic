"""Conversation history management."""

from __future__ import annotations

import json
from typing import Any


class ConversationHistory:
    """Manages the list of messages sent to the LLM."""

    def __init__(self):
        self._messages: list[dict[str, Any]] = []
        self._summarized_count = 0

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    def add_user(self, content: str | list[dict[str, Any]]) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str | list[dict[str, Any]]) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_use_id: str, result: str, is_error: bool = False) -> None:
        """Append tool result as a user message with tool_result content block."""
        tool_result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": result,
        }
        if is_error:
            tool_result["is_error"] = True

        # Merge into the last user message if it already contains tool results
        if (
            self._messages
            and self._messages[-1]["role"] == "user"
            and isinstance(self._messages[-1]["content"], list)
        ):
            self._messages[-1]["content"].append(tool_result)
        else:
            self._messages.append({"role": "user", "content": [tool_result]})

    def replace_with_summary(self, summary: str, keep_last: int = 10) -> int:
        """Replace old messages with a summary, keeping the most recent ones."""
        if len(self._messages) <= keep_last:
            return 0

        keep_messages = self._messages[-keep_last:]
        replaced = len(self._messages) - keep_last

        summary_messages = [
            {
                "role": "user",
                "content": (
                    f"<conversation_summary>\n{summary}\n</conversation_summary>\n\n"
                    "The above is a summary of the earlier conversation. "
                    "Continue from where we left off."
                ),
            },
            {
                "role": "assistant",
                "content": "Understood. I'll continue with full context from the summary.",
            },
        ]

        self._messages = summary_messages + keep_messages
        self._summarized_count += replaced
        return replaced

    def last_assistant_text(self) -> str:
        """Extract plain text from the last assistant message."""
        for msg in reversed(self._messages):
            if msg["role"] == "assistant":
                content = msg["content"]
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "".join(
                        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                    )
        return ""

    def to_json(self) -> str:
        return json.dumps(self._messages, indent=2)

    def __len__(self) -> int:
        return len(self._messages)
