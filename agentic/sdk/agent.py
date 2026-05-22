"""SDK Agent and Session — the primary developer-facing API."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

from agentic.sdk.events import (
    DoneEvent, ErrorEvent, Event,
    SystemEvent, TextEvent, ThinkingEvent, ToolResultEvent, ToolStartEvent,
)
from agentic.tools.base import Tool


# ---------------------------------------------------------------------------
# Internal renderer that queues events instead of printing to a terminal
# ---------------------------------------------------------------------------

class _StreamingRenderer:
    """Drop-in renderer for AgentLoop that queues structured events."""

    def __init__(self, queue: "asyncio.Queue[Event | None]") -> None:
        self._q = queue

    # --- Text streaming ---
    def print_assistant_start(self) -> None:
        pass

    def stream_text(self, text: str) -> None:
        self._q.put_nowait(TextEvent(text=text))

    def stream_thinking(self, text: str) -> None:
        self._q.put_nowait(ThinkingEvent(text=text))

    def finish_streaming(self) -> None:
        pass

    # --- Tool calls ---
    def print_tool_call(self, name: str, input: dict[str, Any]) -> None:
        self._q.put_nowait(ToolStartEvent(tool_name=name, tool_input=input))

    def start_spinner(self, name: str) -> None:
        return None

    def stop_spinner(self, spinner: Any) -> None:
        pass

    def print_tool_result(self, name: str, content: str, is_error: bool, elapsed: float) -> None:
        self._q.put_nowait(ToolResultEvent(
            tool_name=name, content=content, is_error=is_error, elapsed_seconds=elapsed,
        ))

    # --- Status / system messages ---
    def print_usage(self, *args: Any, **kwargs: Any) -> None:
        pass  # captured in DoneEvent

    def print_context_status(self, *args: Any) -> None:
        pass  # REPL reads this directly from context_mgr after DoneEvent

    def print_context_summarized(self, count: int) -> None:
        self._q.put_nowait(SystemEvent(text=f"📝 Context summarized ({count} messages compressed)"))

    def print_system(self, text: str) -> None:
        self._q.put_nowait(SystemEvent(text=text))

    def print_error(self, text: str) -> None:
        self._q.put_nowait(ErrorEvent(message=text))

    def print_memory_saved(self, name: str, memory_type: str) -> None:
        self._q.put_nowait(SystemEvent(text=f"💾 Memory saved: {name} ({memory_type})"))

    def print_skill(self, name: str) -> None:
        self._q.put_nowait(SystemEvent(text=f"⚡ Running skill: /{name}"))

    def print_markdown(self, *args: Any) -> None:
        pass

    def print_diff(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# AgentLoop subclass with a custom system prompt for SDK use
# ---------------------------------------------------------------------------

class _SDKAgentLoop:
    """Wraps AgentLoop with an optional custom system prompt and event-streaming interface."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        openai_api_key: str | None,
        system_prompt: str | None,
        allowed_tools: list[str] | None,
        extra_tools: list[Tool],
        user_id: str,
        max_turns: int,
        thinking_budget: int,
        working_dir: Path | None,
    ) -> None:
        from agentic.core.config import ConfigManager, detect_provider
        from agentic.core.agent import AgentLoop

        config = ConfigManager(
            project_dir=working_dir or Path.cwd(),
            user_id=user_id,
        )
        s = config.settings
        if model:
            s.model = model
            if not s.provider:
                s.provider = detect_provider(model)
        if api_key:
            s.api_key = api_key
        if openai_api_key:
            s.openai_api_key = openai_api_key
        s.max_tool_iterations = max_turns
        s.thinking_budget = thinking_budget
        config._settings = s

        self._loop = AgentLoop(
            config=config,
            model=model,
            allowed_tools=allowed_tools,
            is_subagent=False,
        )

        # Register custom tools after built-in tool setup
        for t in extra_tools:
            self._loop._tool_registry.register(t)

        # Patch system-prompt builder once if a custom prompt was given
        if system_prompt is not None:
            loop = self._loop

            def _custom_build() -> "str | list[dict]":
                memories = loop._memory.load_for_context()
                prompt = system_prompt
                if memories:
                    prompt += f"\n\n## Memory from past sessions\n{memories}"
                from agentic.core.llm import OpenAIClient
                if isinstance(loop._llm, OpenAIClient):
                    return prompt
                return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]

            self._loop._build_system_prompt = _custom_build  # type: ignore[method-assign]

    async def run_turn(self, message: str, renderer: _StreamingRenderer) -> str:
        self._loop._renderer = renderer
        return await self._loop.run_turn(message)

    def reset(self) -> None:
        self._loop._conversation.clear()

    @property
    def _context_mgr(self):  # noqa: ANN202
        return self._loop._context_mgr


# ---------------------------------------------------------------------------
# Session — a stateful multi-turn conversation
# ---------------------------------------------------------------------------

class Session:
    """A stateful multi-turn conversation.

    Obtain one via ``agent.session()``; reuse it across multiple ``run()`` /
    ``stream()`` calls to maintain conversation context.

    Example::

        session = agent.session()
        r1 = await session.run("Hello, I placed order #1234")
        r2 = await session.run("What is the status of that order?")  # has context
    """

    def __init__(self, loop: _SDKAgentLoop, session_id: str) -> None:
        self.id = session_id
        self._loop = loop

    async def run(self, message: str) -> str:
        """Send *message* and return the complete text response."""
        full_text = ""
        async for event in self.stream(message):
            if isinstance(event, TextEvent):
                full_text += event.text
        return full_text

    async def stream(self, message: str) -> AsyncIterator[Event]:
        """Send *message* and yield :class:`Event` objects as they arrive.

        Events arrive in order:

        * :class:`TextEvent` — assistant text chunks
        * :class:`ThinkingEvent` — extended-thinking chunks (Claude 3.7+)
        * :class:`ToolStartEvent` — a tool call is about to execute
        * :class:`ToolResultEvent` — a tool call has completed
        * :class:`DoneEvent` — turn finished; includes full text + token counts
        * :class:`ErrorEvent` — something went wrong
        """
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        renderer = _StreamingRenderer(queue)

        async def _run_in_bg() -> None:
            try:
                text = await self._loop.run_turn(message, renderer)
                u = self._loop._context_mgr._last_usage or {}
                queue.put_nowait(DoneEvent(
                    text=text,
                    input_tokens=u.get("input", 0),
                    output_tokens=u.get("output", 0),
                    cache_read_tokens=u.get("cache_read", 0),
                    cache_write_tokens=u.get("cache_write", 0),
                    cost_usd=self._loop._context_mgr.session_cost,
                ))
            except Exception as e:
                queue.put_nowait(ErrorEvent(message=str(e)))
            finally:
                queue.put_nowait(None)  # sentinel

        task = asyncio.create_task(_run_in_bg())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()

    def reset(self) -> None:
        """Clear conversation history (start fresh in the same session)."""
        self._loop.reset()

    @property
    def _inner(self) -> "Any":
        """Direct access to the underlying AgentLoop.

        Used by the REPL for slash commands that need internal state
        (memory manager, skill manager, config, etc.). Not part of the
        public SDK API — do not use in application code.
        """
        return self._loop._loop

    @classmethod
    def from_loop(cls, agent_loop: "Any") -> "Session":
        """Create a Session that wraps an existing AgentLoop.

        Used by the CLI REPL so it can drive its own AgentLoop (with
        sandbox, kernel, renderer already wired up) through the SDK's
        event-streaming interface.
        """
        session = cls.__new__(cls)
        session.id = str(uuid.uuid4())
        session._loop = _DirectAdapter(agent_loop)
        return session


class _DirectAdapter:
    """Minimal adapter that makes an AgentLoop look like _SDKAgentLoop.

    Lets Session.from_loop() work without going through _SDKAgentLoop's
    constructor (which would create a second AgentLoop).
    """

    def __init__(self, loop: "Any") -> None:
        self._loop = loop

    async def run_turn(self, message: str, renderer: _StreamingRenderer) -> str:
        self._loop._renderer = renderer
        return await self._loop.run_turn(message)

    def reset(self) -> None:
        self._loop._conversation.clear()

    @property
    def _context_mgr(self) -> "Any":
        return self._loop._context_mgr


# ---------------------------------------------------------------------------
# Agent — the developer-facing entry point
# ---------------------------------------------------------------------------

class Agent:
    """Agentic SDK entry point.

    Configure once, create as many :class:`Session` objects as needed.

    Args:
        model: Model identifier, e.g. ``"claude-sonnet-4-6"`` or ``"gpt-4o"``.
            Provider is auto-detected from the name.
        api_key: Anthropic API key. Falls back to ``ANTHROPIC_API_KEY`` env var.
        openai_api_key: OpenAI API key. Falls back to ``OPENAI_API_KEY`` env var.
        system_prompt: Override the built-in coding-agent system prompt.
            Memories are still injected automatically.  Set to ``""`` to
            disable everything including memories.
        tools: Built-in tools to enable.
            ``None`` (default) enables the full coding-agent tool suite.
            ``[]`` disables all built-in tools (use with ``add_tool``).
            A list of tool names enables only those tools, e.g.
            ``["WebSearch", "WebFetch"]``.
        memory: Whether to persist memories across sessions (default True).
        user_id: Identifier for memory isolation. Use a unique ID per end-user
            in multi-user deployments.
        max_turns: Maximum tool-call iterations per turn (default 50).
        thinking_budget: Extended-thinking token budget for Claude 3.7+ models.
            0 disables thinking (default).
        working_dir: Working directory for file tools. Defaults to ``Path.cwd()``.

    Examples::

        # Minimal usage
        agent = Agent()
        response = await agent.run("Explain this codebase")

        # Customer support bot with custom tools
        agent = Agent(
            system_prompt="You are a friendly support agent for Acme Corp.",
            tools=[],   # no built-in tools
        )

        @agent.tool
        async def lookup_order(order_id: str) -> str:
            \"\"\"Return status for an order.\"\"\"
            return orders_db.get(order_id, "not found")

        session = agent.session()
        await session.run("What is the status of order #42?")

        # Streaming
        async for event in agent.stream("Build a todo app"):
            if event.type == "text":
                print(event.text, end="", flush=True)
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        openai_api_key: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        memory: bool = True,
        user_id: str = "default",
        max_turns: int = 50,
        thinking_budget: int = 0,
        working_dir: Path | None = None,
    ) -> None:
        from agentic.core.config import DEFAULT_MODEL
        self._model = model or DEFAULT_MODEL
        self._api_key = api_key
        self._openai_api_key = openai_api_key
        self._system_prompt = system_prompt
        self._user_id = user_id
        self._max_turns = max_turns
        self._thinking_budget = thinking_budget
        self._working_dir = working_dir
        self._custom_tools: list[Tool] = []

        # Build allowed_tools list from the `tools` parameter
        if tools is None:
            if memory:
                self._allowed_tools = None  # all tools
            else:
                self._allowed_tools = _ALL_BUILTIN_TOOLS_EXCEPT_MEMORY
        else:
            allowed = list(tools)
            if not memory:
                for m in ("MemoryWrite", "MemoryRead", "MemoryDelete"):
                    allowed = [t for t in allowed if t != m]
            self._allowed_tools = allowed  # [] means "no built-in tools"; None means "all"

    def _make_loop(self) -> _SDKAgentLoop:
        return _SDKAgentLoop(
            model=self._model,
            api_key=self._api_key,
            openai_api_key=self._openai_api_key,
            system_prompt=self._system_prompt,
            allowed_tools=self._allowed_tools,
            extra_tools=list(self._custom_tools),
            user_id=self._user_id,
            max_turns=self._max_turns,
            thinking_budget=self._thinking_budget,
            working_dir=self._working_dir,
        )

    def session(self) -> Session:
        """Create a new :class:`Session` with an isolated conversation history."""
        return Session(self._make_loop(), str(uuid.uuid4()))

    async def run(self, message: str) -> str:
        """Send *message* in a fresh session and return the complete text response."""
        return await self.session().run(message)

    async def stream(self, message: str) -> AsyncIterator[Event]:
        """Send *message* in a fresh session and stream :class:`Event` objects."""
        async for event in self.session().stream(message):
            yield event

    def add_tool(self, tool_or_fn: Tool | Callable) -> "Agent":
        """Register a custom tool.

        Accepts a :class:`~agentic.tools.base.Tool` instance *or* a plain
        async function (which is wrapped automatically via :func:`tool`).

        Returns ``self`` so calls can be chained::

            agent.add_tool(lookup_order).add_tool(submit_refund)
        """
        if isinstance(tool_or_fn, Tool):
            self._custom_tools.append(tool_or_fn)
        else:
            from agentic.sdk.tool_decorator import tool as make_tool
            self._custom_tools.append(make_tool(tool_or_fn))
        return self

    def tool(self, fn: Callable) -> Tool:
        """Decorator that registers *fn* as a tool on this agent.

        Example::

            @agent.tool
            async def send_email(to: str, subject: str, body: str) -> str:
                \"\"\"Send an email message.\"\"\"
                mailer.send(to, subject, body)
                return "sent"
        """
        from agentic.sdk.tool_decorator import tool as make_tool
        t = make_tool(fn)
        self._custom_tools.append(t)
        return t


# Built-in tool names excluding memory tools — used when memory=False
_ALL_BUILTIN_TOOLS_EXCEPT_MEMORY = [
    "Bash", "Monitor", "Read", "Write", "Edit", "MultiEdit",
    "WebFetch", "WebSearch", "Grep", "Glob", "LS",
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskStop", "TaskOutput",
    "Agent", "AskUserQuestion", "PushNotification",
]
