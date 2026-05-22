"""Basic SDK usage — one-shot, multi-turn, and streaming.

Run:
    python examples/sdk_basic.py
"""

from __future__ import annotations

import asyncio
from agentic import Agent, TextEvent, ToolStartEvent, ToolResultEvent, DoneEvent


async def one_shot() -> None:
    """One-shot query — no session persistence."""
    print("=== One-shot ===")
    agent = Agent(model="claude-sonnet-4-6")
    response = await agent.run("What is 12 * 34? Show your working.")
    print(response)


async def multi_turn() -> None:
    """Multi-turn session — context is preserved between messages."""
    print("\n=== Multi-turn ===")
    agent = Agent(model="claude-sonnet-4-6")
    session = agent.session()

    r1 = await session.run("My name is Alice and I'm a data scientist.")
    print(f"Turn 1: {r1[:100]}...")

    r2 = await session.run("What is my name and job?")
    print(f"Turn 2: {r2}")

    session.reset()
    r3 = await session.run("What is my name?")
    print(f"Turn 3 (after reset): {r3[:80]}...")


async def streaming() -> None:
    """Stream events as they arrive."""
    print("\n=== Streaming ===")
    agent = Agent(model="claude-sonnet-4-6")

    print("Response: ", end="", flush=True)
    async for event in agent.stream("Write a one-sentence Python tip."):
        if isinstance(event, TextEvent):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolStartEvent):
            print(f"\n[tool: {event.tool_name}]", end="", flush=True)
        elif isinstance(event, ToolResultEvent) and event.is_error:
            print(f"\n[tool error: {event.content[:60]}]", end="", flush=True)
        elif isinstance(event, DoneEvent):
            print(f"\n\nTokens: {event.input_tokens} in / {event.output_tokens} out")
            print(f"Cost: ${event.cost_usd:.4f}")


async def custom_tools() -> None:
    """Register custom tools via the @agent.tool decorator."""
    print("\n=== Custom tools ===")
    agent = Agent(
        model="claude-sonnet-4-6",
        tools=[],  # no built-in tools — only what we add
        system_prompt="You are a weather assistant. Use the get_weather tool to answer questions.",
    )

    @agent.tool
    async def get_weather(city: str) -> str:
        """Get current weather for a city."""
        # Simulated — replace with a real API call
        data = {"London": "15°C, cloudy", "Tokyo": "22°C, sunny", "NYC": "18°C, partly cloudy"}
        return data.get(city, f"No data for {city}")

    response = await agent.run("What's the weather like in London and Tokyo?")
    print(response)


if __name__ == "__main__":
    asyncio.run(one_shot())
    asyncio.run(multi_turn())
    asyncio.run(streaming())
    asyncio.run(custom_tools())
