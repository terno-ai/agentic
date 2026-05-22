"""Agentic — autonomous coding agent and developer SDK.

SDK quick start::

    from agentic import Agent, tool

    agent = Agent(model="claude-sonnet-4-6")
    response = await agent.run("Explain this repo")

See ``agentic.sdk`` for the full SDK API.
"""

__version__ = "0.2.0"

# Re-export SDK surface at the top level for convenience
from agentic.sdk import Agent, Session, tool
from agentic.sdk.events import (
    DoneEvent, ErrorEvent, Event,
    TextEvent, ThinkingEvent, ToolResultEvent, ToolStartEvent,
)

__all__ = [
    "Agent", "Session", "tool",
    "Event", "TextEvent", "ThinkingEvent",
    "ToolStartEvent", "ToolResultEvent", "ErrorEvent", "DoneEvent",
]
