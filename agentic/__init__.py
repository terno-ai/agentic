"""Agentic — autonomous coding agent and developer SDK.

Sync (plain scripts / standard Python REPL)::

    from agentic import Agent
    agent = Agent(model="claude-sonnet-4-6")
    print(agent.run_sync("Explain this repo"))

Async (FastAPI, asyncio scripts)::

    from agentic import Agent
    agent = Agent(model="claude-sonnet-4-6")
    response = await agent.run("Explain this repo")

Asyncio REPL (python3 -m asyncio)::

    from agentic import Agent
    agent = Agent()
    await agent.run("Hello")

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
