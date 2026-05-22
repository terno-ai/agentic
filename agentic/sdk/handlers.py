"""Default event handlers for the Agentic SDK."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic.sdk.events import Event


def print_events(event: "Event") -> None:
    """Default stdout event handler.

    Prints a human-readable representation of every SDK event:

    * Text streams inline as it arrives (no newline between chunks).
    * Tool calls show the tool name and key arguments.
    * Tool results show a one-line preview with timing.
    * Done prints a token/cost summary line.
    * Errors go to stderr.
    * System messages (warnings, context summaries) are printed dimmed.

    Usage::

        from agentic import Agent, print_events

        agent = Agent()
        async for event in agent.stream("Hello"):
            print_events(event)

        # Or use the built-in shorthand:
        agent.stream_sync("Hello")
    """
    from agentic.sdk.events import (
        DoneEvent, ErrorEvent, SystemEvent,
        TextEvent, ThinkingEvent, ToolResultEvent, ToolStartEvent,
    )

    if isinstance(event, TextEvent):
        print(event.text, end="", flush=True)

    elif isinstance(event, ThinkingEvent):
        print(f"\033[2m{event.text}\033[0m", end="", flush=True)

    elif isinstance(event, ToolStartEvent):
        args = ", ".join(
            f"{k}={repr(v)[:40]}" for k, v in list(event.tool_input.items())[:2]
        )
        print(f"\n\033[36m⚙  {event.tool_name}\033[0m({args})", flush=True)

    elif isinstance(event, ToolResultEvent):
        mark = "\033[31m✗\033[0m" if event.is_error else "\033[32m✓\033[0m"
        time_str = f" \033[2m({event.elapsed_seconds:.1f}s)\033[0m" if event.elapsed_seconds >= 0.5 else ""
        lines = event.content.strip().splitlines() if event.content else []
        if event.is_error:
            preview = (lines[0] if lines else event.content)[:120]
            print(f"   {mark} {preview}{time_str}", flush=True)
        else:
            preview = (lines[0] if lines else "")[:80]
            more = f" \033[2m+{len(lines) - 1} lines\033[0m" if len(lines) > 1 else ""
            print(f"   {mark} {preview}{more}{time_str}", flush=True)

    elif isinstance(event, DoneEvent):
        print()  # newline after streamed text
        parts = [f"{event.input_tokens:,}in", f"{event.output_tokens:,}out"]
        if event.cache_read_tokens:
            parts.append(f"{event.cache_read_tokens:,}cache_hit")
        if event.cost_usd:
            parts.append(f"${event.cost_usd:.4f}")
        print(f"\033[2m[{' · '.join(parts)}]\033[0m", flush=True)

    elif isinstance(event, ErrorEvent):
        print(f"\n\033[31mError:\033[0m {event.message}", file=sys.stderr, flush=True)

    elif isinstance(event, SystemEvent):
        print(f"\033[33m{event.text}\033[0m", flush=True)
