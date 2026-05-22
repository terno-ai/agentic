"""Agentic SDK — build agents for any application.

Quick start::

    from agentic.sdk import Agent, tool

    agent = Agent(model="claude-sonnet-4-6")

    @agent.tool
    async def lookup_order(order_id: str) -> str:
        \"\"\"Return order status.\"\"\"
        return f"Order {order_id}: shipped"

    # One-shot
    response = await agent.run("What is the status of order #42?")

    # Multi-turn session
    session = agent.session()
    r1 = await session.run("Hi, I need help with my order")
    r2 = await session.run("It's order #42")

    # Streaming
    async for event in agent.stream("Build me a REST API"):
        if event.type == "text":
            print(event.text, end="", flush=True)
"""

from agentic.sdk.agent import Agent, Session
from agentic.sdk.events import (
    DoneEvent,
    ErrorEvent,
    Event,
    SystemEvent,
    TextEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from agentic.sdk.tool_decorator import tool

__all__ = [
    "Agent",
    "Session",
    "tool",
    "Event",
    "TextEvent",
    "ThinkingEvent",
    "ToolStartEvent",
    "ToolResultEvent",
    "ErrorEvent",
    "DoneEvent",
    "SystemEvent",
]
