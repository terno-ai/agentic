"""Hook event definitions."""

from __future__ import annotations

from enum import Enum


class HookEvent(str, Enum):
    PRE_TOOL_CALL = "PreToolCall"
    POST_TOOL_CALL = "PostToolCall"
    AGENT_START = "AgentStart"
    AGENT_STOP = "AgentStop"
    USER_MESSAGE = "UserMessage"
    ASSISTANT_MESSAGE = "AssistantMessage"
    CONTEXT_SUMMARIZED = "ContextSummarized"
