"""Streaming event types emitted by Agent.stream() / Session.stream()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class TextEvent:
    """A chunk of assistant text (mirrors the LLM stream)."""
    text: str
    type: Literal["text"] = field(default="text", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass
class ThinkingEvent:
    """A chunk of extended-thinking text (Claude 3.7+ only)."""
    text: str
    type: Literal["thinking"] = field(default="thinking", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass
class ToolStartEvent:
    """The agent is about to call a tool."""
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_start"] = field(default="tool_start", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "tool_name": self.tool_name, "tool_input": self.tool_input}


@dataclass
class ToolResultEvent:
    """A tool has finished executing."""
    tool_name: str
    content: str
    is_error: bool = False
    elapsed_seconds: float = 0.0
    type: Literal["tool_result"] = field(default="tool_result", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "tool_name": self.tool_name,
            "content": self.content,
            "is_error": self.is_error,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass
class ErrorEvent:
    """An error occurred during the turn."""
    message: str
    type: Literal["error"] = field(default="error", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "message": self.message}


@dataclass
class DoneEvent:
    """The turn is complete. Contains the full accumulated text and token usage."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    type: Literal["done"] = field(default="done", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class SystemEvent:
    """An informational system message (context summarization, skill start, warnings, etc.)."""
    text: str
    type: Literal["system"] = field(default="system", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}


# Union type for type-checking
Event = TextEvent | ThinkingEvent | ToolStartEvent | ToolResultEvent | ErrorEvent | DoneEvent | SystemEvent
