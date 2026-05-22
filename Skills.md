# Agentic SDK — Skills Reference

This file teaches the agent how to help developers build applications with the Agentic SDK.
Place it in `.agentic/prompt.md` or `AGENT.md` in any project that uses `agentic.sdk`.

---

## Mental model

The SDK has three layers:

```
Agent          — stateless configuration object (model, tools, system prompt)
  └── Session  — stateful conversation (holds history, one per user/chat)
        └── stream() / run()  — one turn; emits Events or returns a string
```

`Agent` is a factory. Create it once at startup, call `agent.session()` per user or conversation. Sessions are isolated — they don't share history. `Agent.run()` and `Agent.stream()` are convenience wrappers that create a throw-away session each time.

---

## Installation

```bash
pip install -e .                 # core
pip install -e ".[fastapi]"      # + FastAPI/uvicorn for web apps
```

---

## Core API

### Creating an Agent

```python
from agentic import Agent

# Coding agent — full built-in tool suite
agent = Agent(model="claude-sonnet-4-6")

# Custom assistant — specific tools only
agent = Agent(
    model="claude-sonnet-4-6",
    system_prompt="You are a support agent for Acme Corp.",
    tools=["WebSearch"],          # only WebSearch from built-ins
    memory=True,                  # persist memories across sessions (default)
    user_id="default",            # isolate memory per end-user
    max_turns=50,                 # tool-call iterations per turn (default 50)
    thinking_budget=0,            # extended thinking tokens, 0=off
    working_dir=None,             # base dir for file tools; default Path.cwd()
    api_key=None,                 # Anthropic key; falls back to ANTHROPIC_API_KEY
    openai_api_key=None,          # OpenAI key; falls back to OPENAI_API_KEY
)

# No built-in tools at all — bring your own
agent = Agent(system_prompt="...", tools=[])
```

**`tools` parameter:**
| Value | Effect |
|-------|--------|
| `None` (default) | All built-in tools enabled |
| `[]` | No built-in tools |
| `["Bash", "Read", "Write"]` | Only these built-in tools |

**Built-in tool names:** `Bash`, `Monitor`, `Read`, `Write`, `Edit`, `MultiEdit`, `Grep`, `Glob`, `LS`, `WebFetch`, `WebSearch`, `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`, `TaskStop`, `TaskOutput`, `Agent`, `AskUserQuestion`, `PushNotification`, `MemoryWrite`, `MemoryRead`, `MemoryDelete`

### One-shot

```python
# Sync — works in any script or the standard Python REPL
response: str = agent.run_sync("Summarise the repo in one paragraph")

# Async
response: str = await agent.run("Summarise the repo in one paragraph")
```

### Multi-turn session

```python
session = agent.session()               # fresh isolated conversation

# Sync
session.stream_sync("I'm Alice, a data scientist")  # prints as tokens arrive
session.stream_sync("What is my job?")              # context preserved

# Async
r1 = await session.run("I'm Alice, a data scientist")
r2 = await session.run("What is my job?")
session.reset()                         # wipe history, keep same session object
```

### Streaming with default handler

```python
from agentic import print_events

# Sync — simplest, prints everything to stdout
agent.stream_sync("Build a REST API")

# Async — same output
async for event in agent.stream("Build a REST API"):
    print_events(event)

# Custom handler
def my_handler(event):
    if event.type == "text":
        print(event.text, end="", flush=True)

agent.stream_sync("Build a REST API", on_event=my_handler)
```

### Streaming with manual event handling

```python
from agentic import TextEvent, ToolStartEvent, ToolResultEvent, DoneEvent, ErrorEvent, SystemEvent

async for event in agent.stream("Build a REST API"):
    match event.type:
        case "text":
            print(event.text, end="", flush=True)
        case "thinking":
            print(f"[thinking: {event.text[:40]}…]")
        case "tool_start":
            print(f"\n⚙ {event.tool_name}({event.tool_input})")
        case "tool_result":
            status = "✗" if event.is_error else "✓"
            print(f"{status} {event.tool_name} ({event.elapsed_seconds:.1f}s)")
        case "done":
            print(f"\nTokens: {event.input_tokens}in / {event.output_tokens}out  Cost: ${event.cost_usd:.4f}")
        case "error":
            print(f"\nError: {event.message}")
        case "system":
            print(f"[{event.text}]")
```

---

## Custom tools

### @tool decorator (quickest)

```python
from agentic import tool, Agent

@tool
async def lookup_order(order_id: str) -> str:
    """Return current status for an order by its ID."""
    row = await db.fetchone("SELECT status FROM orders WHERE id = ?", order_id)
    return row["status"] if row else "Order not found"

agent = Agent(tools=[], system_prompt="You are a support agent.")
agent.add_tool(lookup_order)
```

### @agent.tool decorator (inline)

```python
agent = Agent(tools=[], system_prompt="You are a weather assistant.")

@agent.tool
async def get_weather(city: str, units: str = "metric") -> str:
    """Return current weather for a city. units is 'metric' or 'imperial'."""
    data = await weather_api.get(city, units)
    return f"{data['temp']}°, {data['condition']}"
```

### Tool subclass (full control)

```python
from agentic.tools.base import Tool, ToolResult

class DatabaseQueryTool(Tool):
    name = "DatabaseQuery"
    description = "Run a read-only SQL query against the application database."
    input_schema = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "SELECT statement to run"},
            "limit": {"type": "integer", "description": "Max rows (default 100)"},
        },
        "required": ["sql"],
    }

    def __init__(self, db_conn):
        self._db = db_conn

    async def execute(self, sql: str, limit: int = 100) -> ToolResult:
        if not sql.strip().upper().startswith("SELECT"):
            return ToolResult.error("Only SELECT statements are allowed")
        try:
            rows = await self._db.fetch(sql + f" LIMIT {limit}")
            return ToolResult.ok(str(rows))
        except Exception as e:
            return ToolResult.error(str(e))

agent.add_tool(DatabaseQueryTool(db))
```

### Type mapping for @tool

| Python annotation | JSON Schema type |
|------------------|-----------------|
| `str` (or no annotation) | `"string"` |
| `int` | `"integer"` |
| `float` | `"number"` |
| `bool` | `"boolean"` |
| `list` | `"array"` |
| `dict` | `"object"` |

Parameters with defaults are optional; those without defaults are `required`.

---

## Event reference

All events have `.type: str` and `.to_dict() -> dict`.

| Class | `.type` | Fields |
|-------|---------|--------|
| `TextEvent` | `"text"` | `text: str` |
| `ThinkingEvent` | `"thinking"` | `text: str` |
| `ToolStartEvent` | `"tool_start"` | `tool_name: str`, `tool_input: dict` |
| `ToolResultEvent` | `"tool_result"` | `tool_name: str`, `content: str`, `is_error: bool`, `elapsed_seconds: float` |
| `DoneEvent` | `"done"` | `text: str`, `input_tokens: int`, `output_tokens: int`, `cache_read_tokens: int`, `cache_write_tokens: int`, `cost_usd: float` |
| `ErrorEvent` | `"error"` | `message: str` |
| `SystemEvent` | `"system"` | `text: str` — context summaries, skill starts, warnings |

### Default handler: `print_events`

`print_events(event)` handles all event types and prints to stdout with ANSI colours. Use it directly or pass it as `on_event`:

```python
from agentic import print_events
agent.stream_sync("Hello", on_event=print_events)  # explicit (same as default)
```

Output format:

| Event | Printed as |
|-------|-----------|
| `TextEvent` | raw text inline |
| `ThinkingEvent` | dimmed text |
| `ToolStartEvent` | `⚙  ToolName(arg=value)` |
| `ToolResultEvent` | `✓ first line +N lines (0.3s)` or `✗ error` |
| `DoneEvent` | `[120in · 45out · $0.0003]` |
| `ErrorEvent` | `Error: message` on stderr |
| `SystemEvent` | dimmed yellow text |

---

## FastAPI integration

```python
pip install "agentic[fastapi]"
```

```python
from fastapi import FastAPI
from agentic import Agent
from agentic.sdk.integrations.fastapi import AgentRouter

agent = Agent(system_prompt="You are a helpful assistant.")

app = FastAPI()
router = AgentRouter(
    agent,
    session_ttl=3600,   # seconds before idle session is evicted
    prefix="",          # added to all route paths (before the router prefix)
    tags=["agent"],     # FastAPI tags
)
app.include_router(router(), prefix="/api/agent")
```

**Endpoints created:**

| Method | Path | Body / Response |
|--------|------|-----------------|
| `POST` | `/api/agent/chat` | `{message, session_id?}` → `{text, session_id, input_tokens, output_tokens, cost_usd}` |
| `POST` | `/api/agent/chat/stream` | `{message, session_id?}` → SSE stream |
| `GET` | `/api/agent/sessions` | `{sessions: [...], count: N}` |
| `DELETE` | `/api/agent/sessions/{id}` | `{status, session_id}` |

**SSE stream format:**

```
data: {"type": "session", "session_id": "..."}

data: {"type": "text", "text": "Hello"}

data: {"type": "tool_start", "tool_name": "WebSearch", "tool_input": {...}}

data: {"type": "tool_result", "tool_name": "WebSearch", "content": "...", "is_error": false, "elapsed_seconds": 1.2}

data: {"type": "done", "text": "...", "input_tokens": 120, "output_tokens": 45, "cost_usd": 0.0003}

data: [DONE]
```

---

## Use-case recipes

### 1. Customer support bot

```python
from agentic import Agent, tool

SYSTEM = """You are Aria, a friendly support agent for ShopFast.
- Help customers track orders and request refunds
- Escalate complex issues to a human agent via the escalate tool
- Never promise specific timelines you can't guarantee"""

agent = Agent(system_prompt=SYSTEM, tools=[], memory=True)

@agent.tool
async def get_order(order_id: str) -> str:
    """Look up current status of an order."""
    return await orders_db.get_status(order_id)

@agent.tool
async def submit_refund(order_id: str, reason: str) -> str:
    """Submit a refund request for an order."""
    ref_id = await refunds_db.create(order_id, reason)
    return f"Refund {ref_id} submitted. Processing in 3–5 business days."

@agent.tool
async def escalate(summary: str, urgency: str) -> str:
    """Escalate the issue to a human agent. urgency: low | medium | high"""
    ticket = await tickets_db.create(summary, urgency)
    return f"Ticket {ticket} created. A human will reach out within {'1h' if urgency=='high' else '24h'}."

# Per-customer session (use customer_id as user_id for memory isolation)
def get_session(customer_id: str):
    a = Agent(system_prompt=SYSTEM, tools=[], memory=True, user_id=customer_id)
    a.add_tool(get_order).add_tool(submit_refund).add_tool(escalate)
    return a.session()
```

### 2. Console / CLI tool

```python
from agentic import Agent

agent = Agent(model="claude-sonnet-4-6")
session = agent.session()

print("Agent ready. Type 'exit' to quit.\n")
while True:
    user_input = input("You: ").strip()
    if user_input.lower() in ("exit", "quit"):
        break
    session.stream_sync(user_input)   # prints as tokens arrive, context preserved
    print()
```

### 3. Background task processor

```python
import asyncio
from agentic import Agent

agent = Agent(
    model="claude-sonnet-4-6",
    tools=["Read", "Write", "Bash"],   # only what's needed
    system_prompt="You process files. For each task, read the input, transform it, write the output.",
)

async def process_file(input_path: str, output_path: str) -> str:
    return await agent.run(
        f"Read {input_path}, summarise the content in 3 bullet points, write to {output_path}"
    )

# Process many files concurrently
tasks = [process_file(f"in/{i}.txt", f"out/{i}.txt") for i in range(10)]
results = await asyncio.gather(*tasks)
```

### 4. Multi-agent pipeline

```python
from agentic import Agent

researcher = Agent(
    model="claude-sonnet-4-6",
    system_prompt="You are a research agent. Given a topic, search the web and return a structured report.",
    tools=["WebSearch", "WebFetch"],
)

writer = Agent(
    model="claude-sonnet-4-6",
    system_prompt="You are a technical writer. Turn research reports into polished blog posts.",
    tools=["Read", "Write"],
)

async def research_and_write(topic: str, output_file: str) -> str:
    report = await researcher.run(f"Research: {topic}")
    return await writer.run(f"Write a blog post based on this research. Save to {output_file}.\n\n{report}")
```

### 5. Multi-user web app (FastAPI)

```python
from fastapi import FastAPI, Header
from agentic import Agent
from agentic.sdk.integrations.fastapi import AgentRouter

app = FastAPI()

def build_agent(user_id: str) -> Agent:
    """Each user gets an agent with their own memory namespace."""
    return Agent(
        system_prompt="You are a personal coding assistant.",
        user_id=user_id,
        memory=True,
    )

# Per-request agent with user isolation
@app.post("/chat/stream")
async def chat(message: str, x_user_id: str = Header(...)):
    agent = build_agent(x_user_id)
    session = agent.session()
    from fastapi.responses import StreamingResponse
    import json

    async def sse():
        async for event in session.stream(message):
            yield f"data: {json.dumps(event.to_dict())}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
```

### 6. Workflow orchestrator (sequential steps)

```python
from agentic import Agent

class CodeReviewWorkflow:
    def __init__(self):
        self.analyst = Agent(
            system_prompt="Analyse the diff and list issues by severity.",
            tools=["Bash", "Read"],
        )
        self.reviewer = Agent(
            system_prompt="Given a list of issues, write actionable review comments.",
            tools=[],
        )
        self.summariser = Agent(
            system_prompt="Summarise review comments into a one-paragraph executive summary.",
            tools=[],
        )

    async def run(self, pr_branch: str) -> dict:
        diff = await self.analyst.run(f"Run `git diff main...{pr_branch}` and list all issues.")
        comments = await self.reviewer.run(f"Write review comments for these issues:\n\n{diff}")
        summary = await self.summariser.run(f"Summarise:\n\n{comments}")
        return {"diff_analysis": diff, "comments": comments, "summary": summary}
```

---

## Memory in SDK agents

When `memory=True` (default), the agent automatically loads saved memories into every system prompt and can save new ones via `MemoryWrite`. Memory is keyed on `user_id + working_dir`, so each user's memories are isolated.

**Custom system prompts and memory:** when `system_prompt` is provided, the SDK appends a `## Memory from past sessions` section automatically — you don't need to handle this yourself.

**Disable memory entirely:**

```python
agent = Agent(memory=False)  # no MemoryWrite/Read/Delete tools, no injection
```

**Per-user isolation in multi-user apps:**

```python
# Always pass the real user identifier as user_id
agent = Agent(user_id=request.user.id, memory=True)
```

---

## Error handling

`session.run()` and `session.run_sync()` raise `RuntimeError` on failure — wrap them:

```python
try:
    response = session.run_sync(message)
except RuntimeError as e:
    print(f"Agent error: {e}")
```

`session.stream()` emits `ErrorEvent` instead of raising — handle it in the loop:

```python
from agentic import ErrorEvent

async for event in session.stream(message):
    if isinstance(event, ErrorEvent):
        logger.error("Agent error: %s", event.message)
        return
    # handle other events…
```

`stream_sync()` re-raises `ErrorEvent` as `RuntimeError`, so the same try/except works.

---

## Configuration reference

`Agent` parameters map directly to `agentic.core.config.Settings`:

| Agent param | Settings field | Default |
|-------------|---------------|---------|
| `model` | `model` | `"claude-sonnet-4-6"` |
| `max_turns` | `max_tool_iterations` | `50` |
| `thinking_budget` | `thinking_budget` | `0` |
| `memory=False` | removes memory tools | — |

To pass MCP servers, hooks, or sandbox config, create the `ConfigManager` directly and pass it to `AgentLoop` (internal API). For most use cases `Agent` parameters are sufficient.

---

## Common mistakes to avoid

| Mistake | Fix |
|---------|-----|
| `response = await agent.run(...)` at the top level of a script | Use `agent.run_sync(...)` — `await` only works inside `async def` |
| `agent.run()` inside a loop expecting shared history | Use `session = agent.session()` then `session.run()` inside the loop |
| `tools=[]` but forgetting to `add_tool(...)` | The agent has no tools at all — add your custom ones |
| Not passing `user_id` in a multi-user app | All users share the same memory namespace |
| Calling `session.stream()` concurrently on the same session | Sessions are not thread-safe; create one session per concurrent request |
| Ignoring `ErrorEvent` in stream loops | Always handle it; `stream_sync` raises, but raw `stream()` emits `ErrorEvent` |
| Passing a mutable dict as `tool_input` default in a `Tool` subclass | Use `field(default_factory=dict)` or define `input_schema` as a class variable |

---

## Quickstart checklist for a new SDK project

1. `pip install -e ".[fastapi]"` (or just `pip install -e .` for non-web use)
2. Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in environment
3. Create `Agent` with the right `system_prompt` and `tools`
4. Register custom tools with `@agent.tool` or `agent.add_tool()`
5. Use `agent.session()` per user/conversation for state
6. Call `session.stream_sync(msg)` for scripts; `session.stream(msg)` for async/web
7. Handle `ErrorEvent` (stream) or catch `RuntimeError` (run_sync/stream_sync)
8. For web: `AgentRouter(agent)()` and `app.include_router(...)` — done
