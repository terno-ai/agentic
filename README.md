# Agentic

An autonomous coding agent with memory, skills, MCP integration, context summarization, Docker sandboxing, and a persistent Python kernel — inspired by Claude Code. Supports both **Anthropic** (Claude) and **OpenAI** (GPT / o-series) models.

## Features

- **Multi-provider** — Anthropic Claude and OpenAI GPT/o-series (including reasoning models o1/o3/o4-mini), switchable mid-session
- **Persistent bash shell** — `cd`, env vars, and shell state survive between tool calls; no fresh subprocess per command
- **Parallel tool execution** — independent tool calls in one LLM turn run concurrently via `asyncio.gather`
- **Grep + Glob tools** — structured file search (ripgrep-backed) and pattern-based file listing, separate from raw bash
- **Stream interruption** — Ctrl+C cancels the current agent turn cleanly mid-stream
- **API retry** — exponential backoff with jitter on rate limits and transient errors (both providers)
- **Diff rendering** — coloured unified diff shown inline whenever `Edit` modifies a file
- **Tail truncation** — all tools keep the most recent output on overflow (`[N chars omitted from start]`)
- **Persistent Python Kernel** — IPython-style kernel retains variables between calls; ideal for data science; captures stdout/stderr separately; handles stdin, OOM, timeouts, and hangs
- **Multi-user Docker Sandbox** — each user gets a dedicated, isolated container and workspace; containers persist between sessions preserving installed packages and files
- **Interactive REPL** — readline history, tab completion, slash commands including `/btw` for instant memory notes
- **Persistent Memory** — full memory bodies loaded into context every turn; relevance-ranked search; timestamps and staleness detection; MemoryWrite / MemoryRead / MemoryDelete tools
- **Skills** — slash commands (`/review`, `/init`, `/simplify`, `/security-review`, `/test`) with YAML-defined custom skills
- **MCP Integration** — connect any MCP server via stdio; its tools appear automatically in the agent
- **Context Summarization** — auto-summarizes conversation while preserving critical project facts (platform, language, entry point); prompt cache warming for Anthropic
- **Extended thinking** — `/think [N|off]` enables Claude's extended thinking mode (Claude 3.7+); thinking shown inline as dimmed `[thinking]…[/thinking]` blocks
- **File/URL auto-detection** — file paths and URLs mentioned in a message are pre-read/fetched and attached as context before the LLM turn — no extra tool call needed
- **Multi-line input** — `Esc+Enter` inserts a newline; `Enter` submits
- **LS tool** — structured directory listing (sandbox-aware); replaces `Bash(ls)`
- **Image reading** — `Read` detects PNG/JPG/GIF/WEBP and passes them as vision content blocks to the model
- **Sensitive file warnings** — `Read` warns before opening `.env`, `id_rsa`, `credentials.json`, etc.
- **Session cost estimate** — cumulative `~$N.NNNN` displayed alongside token counts after each turn
- **Tool spinner + timing** — animated spinner while a tool runs; elapsed time shown on completion
- **MemoryWrite tool** — model saves memories via a direct tool call; no fragile XML tag parsing
- **`/compact`** — manually trigger context compression mid-session
- **`.agentic/prompt.md`** — drop project-specific instructions here; loaded into every session automatically
- **Max-iteration recovery** — when the tool loop limit is hit, one final LLM turn summarises progress instead of stopping silently
- **Planning + task tracking** — agent creates a task list before non-trivial work and marks each step in_progress / completed live
- **Permissions** — glob-based allow/deny rules; auto-bypassed inside the sandbox (container is the boundary)
- **MultiEdit tool** — apply multiple find-and-replace edits to one file atomically in a single call
- **Git awareness** — current branch and dirty-file status injected into the system prompt every turn
- **Hook output feedback** — `PostToolCall` hook stdout is injected back into the conversation (enables linters/formatters as hooks)
- **Task-list preservation** — active tasks are included in context summarization so the TODO list survives compaction
- **Hooks** — shell commands triggered on agent events (PreToolCall, PostToolCall, AgentStart, …)
- **Scheduling** — cron / interval autonomous agent runs via APScheduler
- **Sub-agents** — spawn specialized child agents for focused subtasks
- **Token usage display** — input / output / cache token counts shown after each assistant turn
- **SWE-bench harness** — evaluate on SWE-bench Lite with two-layer feedback loop (syntax check + test execution)

## Quick Start

```bash
pip install -e .

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
agentic

# OpenAI
export OPENAI_API_KEY=sk-...
agentic --provider openai --model gpt-4o

# With persistent Python kernel (great for data science)
agentic --kernel

# With Docker sandbox (isolated, safe execution)
agentic --sandbox

# Kernel inside sandbox (recommended for production)
agentic --sandbox --kernel
```

## Usage

```bash
# Interactive REPL (Anthropic, default)
agentic

# OpenAI — provider auto-detected from model name
agentic --model gpt-4o-mini          # → OpenAI
agentic --model o4-mini              # → OpenAI (reasoning model)
agentic --model claude-opus-4-7      # → Anthropic

# Python kernel — variables persist between executions
agentic --kernel
agentic --kernel --model gpt-4o

# Docker sandbox — all commands run inside an isolated container
agentic --sandbox
agentic --sandbox --model gpt-4o

# Kernel + sandbox (kernel runs inside the container)
agentic --sandbox --kernel

# Multi-user sandbox — each user gets an isolated container and workspace
agentic --sandbox --user alice
agentic --sandbox --user bob
AGENTIC_USER=alice agentic --sandbox   # via env var

# Run a single prompt non-interactively
agentic run "explain this codebase"
agentic run --provider openai --model gpt-4o "review the auth module"
agentic run --sandbox --user alice "build the dashboard"
agentic run --kernel "load sales.csv and plot monthly revenue"

# Switch provider or model mid-session (REPL commands)
/model gpt-4o
/provider openai
/model claude-sonnet-4-6
/provider anthropic

# List built-in skills
agentic skills

# Memory management
agentic memory list
agentic memory search "python"
agentic memory delete <name>

# Scheduled jobs
agentic schedule list
agentic schedule add --name="daily-review" --prompt="Review today's changes" --cron="0 9 * * *"
agentic schedule delete --job-id=<id>

# View / edit config
agentic config
agentic config model gpt-4o
agentic config model claude-sonnet-4-6 --global
```

## REPL Commands

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/skills` | List available skills |
| `/model <name>` | Switch model (e.g. `gpt-4o`, `claude-opus-4-7`, `o4-mini`) |
| `/provider <anthropic\|openai>` | Switch provider |
| `/memory` | Show memory index with age badges |
| `/memory search <query>` | Relevance-ranked search across all memories |
| `/memory delete <name>` | Delete a memory |
| `/memory stale` | List project memories not updated in 30+ days |
| `/history` | Show condensed conversation transcript |
| `/btw <note>` | Save a note instantly; `/btw [project] uses postgres` sets the type |
| `/think [N\|off]` | Enable extended thinking with N token budget (Claude 3.7+ only) |
| `/compact` | Manually compress conversation context |
| `/plan` | Toggle plan mode (read-only, no edits) |
| `/clear` | Clear conversation history |
| `/! <cmd>` or `!<cmd>` | Run a shell command directly |
| `/exit` or Ctrl+D | Exit |

**Ctrl+C** during an agent turn interrupts and cancels the current response immediately.

The REPL prompt shows the active model name (e.g. `(s4-6)❯`) so you always know which model is running.

**Multi-line input** — `Esc+Enter` (or `Alt+Enter`) inserts a newline; `Enter` submits.

**File/URL auto-detection** — mention a file path (e.g. `look at /src/app.py`) or a URL and the agent pre-reads/fetches it before responding — no extra tool call needed.

## Extended Thinking

When using Claude 3.7+ models, the agent can reason step-by-step before responding. Enable it with the `/think` REPL command:

```
/think          # enable with default 8 000-token budget
/think 16000    # larger budget for harder problems
/think off      # disable
```

Thinking content is streamed in real-time as dimmed `[thinking]…[/thinking]` blocks before the assistant's response. The budget counts against your token usage but is not billed at the same rate as output tokens.

> **Note:** Extended thinking requires `claude-3-7-sonnet` or later. It is silently ignored on other models.

## Python Kernel

When started with `--kernel`, the agent has access to a persistent Python interpreter that retains all variables between calls — no need to write `.py` files or reload data on every step.

```bash
agentic --kernel                    # local kernel
agentic --sandbox --kernel          # kernel inside Docker sandbox (recommended)
agentic --kernel --model gpt-4o
```

### What it gives you

- **Variable persistence** — define a DataFrame once, use it across many cells
- **Separate stdout/stderr** — each captured and returned independently
- **Return value capture** — the last expression's value is shown (like a Jupyter cell)
- **stdin support** — pass expected `input()` answers via the `stdin` parameter
- **Memory monitoring** — warns at 80% of limit; structured OOM error with remediation steps
- **Timeout enforcement** — execution interrupted cleanly; agent can retry or restart
- **Hang recovery** — watchdog detects no-heartbeat for 30s and auto-restarts
- **inspect action** — list all in-scope variables with types, shapes, and sizes

### PythonKernel tool actions

| Action | Description |
|---|---|
| `execute` | Run Python code; returns stdout, stderr, result, and memory usage |
| `restart` | Clear all variables and reset the interpreter |
| `inspect` | List variables in scope with type, repr, and size |
| `interrupt` | Send KeyboardInterrupt to a running or hung execution |

### Providing stdin

If your code calls `input()`, always pass expected answers via `stdin`:

```
PythonKernel(action="execute", code="name = input('Name: ')\nprint(name)", stdin=["Alice"])
```

### Handling errors

| Situation | Agent action |
|---|---|
| OOM error | `del large_df` or process in chunks, then retry; or `restart` |
| Timeout | `interrupt` to cancel; `restart` if still hung |
| Unresponsive | Kernel auto-restarts; all variables lost |

### Configuration

```json
{
  "kernel": {
    "enabled": false,
    "memory_limit_mb": 512,
    "default_timeout_s": 60,
    "watchdog_timeout_s": 30,
    "max_output_chars": 10000,
    "startup_code": "import pandas as pd; import numpy as np"
  }
}
```

## Docker Sandbox

When started with `--sandbox`, all shell commands and file operations run inside an isolated Docker container. Each user gets their own container and workspace — fully isolated from other users.

```bash
# Single user
agentic --sandbox

# Multi-user — each gets a separate container and workspace
agentic --sandbox --user alice
agentic --sandbox --user bob
AGENTIC_USER=charlie agentic --sandbox

# Build the image manually (auto-built on first run)
docker build -f Dockerfile.sandbox -t agentic-sandbox:latest .
```

### Per-user isolation

Each user's workspace lives at `~/.agentic/users/<user_id>/workspace/` and is mounted as `/workspace` inside their container. Containers are named `agentic-user-<user_id>`.

```
~/.agentic/users/
├── alice/workspace/    ← /workspace in alice's container
├── bob/workspace/      ← /workspace in bob's container
└── charlie/workspace/
```

Containers **persist between sessions** — reconnecting reuses the existing container instantly, preserving all installed packages and filesystem state. Use `docker stop agentic-user-alice` to pause, `docker rm -f agentic-user-alice` to destroy.

### What's pre-installed

Python 3, pip, Node.js 20, npm, curl, wget, git, ffmpeg, ripgrep, build tools, Cairo/Pango/LaTeX C libraries (so `pip install manim` works out of the box), and passwordless sudo for anything else.

### Configuration

```json
{
  "sandbox": {
    "enabled": true,
    "image": "agentic-sandbox:latest",
    "memory_limit": "512m",
    "cpu_limit": 1.0,
    "network": "bridge",
    "auto_build": true,
    "users_workspace_root": "~/.agentic/users"
  }
}
```

Set `"network": "none"` to block internet access. Set `"memory_limit": "2g"` for heavier workloads.

**`cd` persists between commands** — the shell process is persistent; working directory, environment variables, and shell functions survive between calls.

**File tools (Read/Write/Edit) are sandbox-aware** — `/workspace/...` paths are automatically remapped to the user's host workspace, so the agent works with consistent paths regardless of tool used.

**No permission prompts inside the sandbox** — the container is the isolation boundary, so tool calls are auto-approved.

## Built-in Skills

| Skill | Description |
|---|---|
| `/init` | Generate `AGENT.md` for this codebase |
| `/review [PR#\|branch]` | Structured code review |
| `/simplify [file]` | Simplify, deduplicate, and clean up code |
| `/security-review` | OWASP-focused security audit of branch changes |
| `/test <file>` | Write comprehensive tests |

Add project-specific skills by placing YAML files in `.agentic/skills/`:

```yaml
# .agentic/skills/deploy.yaml
name: deploy
description: Run the deployment checklist
prompt: |
  Walk through the deployment checklist for {{args}}:
  1. Run tests and confirm green
  2. Check for uncommitted changes
  3. Verify environment variables are set
  4. Run database migrations if needed
  5. Deploy and tail the logs for errors
```

## Configuration

Settings are layered: `~/.agentic/settings.json` (global) → `.agentic/settings.json` (project) → env vars.

```json
{
  "model": "claude-sonnet-4-6",
  "provider": "anthropic",
  "openai_api_key": "",
  "kernel": {
    "enabled": false,
    "memory_limit_mb": 512,
    "default_timeout_s": 60
  },
  "sandbox": {
    "enabled": false,
    "memory_limit": "512m",
    "network": "bridge"
  },
  "permissions": {
    "allow": ["Read", "Bash(git *)", "Bash(ls *)"],
    "deny": ["Bash(rm -rf *)"]
  },
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    },
    "github": {
      "command": "uvx",
      "args": ["mcp-server-github"],
      "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    }
  },
  "hooks": {
    "PostToolCall": [
      {"matcher": "Bash", "command": "echo \"$TOOL_NAME: $TOOL_INPUT\" >> ~/agent.log"}
    ],
    "AgentStop": [
      {"matcher": "*", "command": "notify-send 'Agentic' 'Session ended'"}
    ]
  },
  "context_summarize_threshold": 80000,
  "max_tool_iterations": 50,
  "auto_memory": true
}
```

### Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `AGENTIC_MODEL` | Override the active model |
| `AGENTIC_PROVIDER` | Override the active provider (`anthropic` or `openai`) |
| `AGENTIC_USER` | User ID for memory, history, and sandbox isolation (default: system username) |

## Memory System

The agent automatically saves important context across sessions using three memory tools:

| Tool | What it does |
|---|---|
| `MemoryWrite` | Save or update a memory (upsert by name) |
| `MemoryRead` | Fetch the full body of a specific memory before updating |
| `MemoryDelete` | Remove stale or wrong memories |

Four memory types — full bodies are loaded into the system prompt every turn, prioritised by type:

| Type | Priority | When used |
|---|---|---|
| `feedback` | 1st (6 k chars) | Behavioral corrections and confirmed approaches |
| `user` | 2nd (3 k chars) | User's role, preferences, expertise |
| `project` | 3rd (4 k chars) | Platform, language, entry point, constraints, ongoing work |
| `reference` | 4th (2 k chars) | Pointers to external systems and docs |

**What's new vs a simple memory store:**
- **Full bodies in context** — the model reads actual memory content, not just names
- **Timestamps** — every record has `created_at` / `updated_at`; index shows age badges
- **Relevance-ranked search** — word-overlap scoring with phrase bonus and recency boost
- **Staleness detection** — project memories not updated in 30+ days are flagged at session start
- **`/btw [type]` prefix** — e.g. `/btw [project] entry point is main.py` saves as the right type

Project facts (platform, language, framework, entry point) are saved immediately when learned so they survive context summarization.

### Per-user isolation

Memory is fully isolated per user. Two users working on the same project never see each other's memories, preferences, or REPL history.

```
~/.agentic/users/
├── alice/
│   ├── history                              ← alice's REPL command history
│   └── projects/<hash>/memory/             ← alice's memories for this project
│       ├── MEMORY.md
│       ├── user_alice_prefs.md
│       └── project_goals.md
└── bob/
    ├── history                              ← bob's REPL command history
    └── projects/<hash>/memory/             ← bob's memories (different hash)
        ├── MEMORY.md
        └── feedback_correction.md
```

The hash is derived from `user_id + project_dir`, so Alice and Bob working on the exact same directory get different hashes and never share memory files. The active user is resolved from `--user` flag → `AGENTIC_USER` env var → system username.

## MCP Servers

Any MCP-compatible server defined in `mcp_servers` is started automatically and its tools are injected into the agent. Tool names are namespaced as `mcp__<server>__<tool>`.

## SWE-bench Evaluation

Run the agent against [SWE-bench Lite](https://github.com/princeton-nlp/SWE-bench) (300 real GitHub bug fixes):

```bash
pip install -e ".[benchmark]"

# Smoke test — 3 instances
python benchmarks/run_swebench.py --limit 3 --model gpt-4o-mini

# Full run (300 instances, ~$15–20 with gpt-4o-mini)
python benchmarks/run_swebench.py --model gpt-4o-mini --workers 4

# With reasoning model (better results, longer timeout needed)
python benchmarks/run_swebench.py --model o4-mini --timeout 1200

# Specific instances
python benchmarks/run_swebench.py --ids "django__django-11099,sympy__sympy-20049" --model gpt-4o

# Disable test-execution feedback (faster, Layer 1 syntax check still runs)
python benchmarks/run_swebench.py --model gpt-4o --no-test-feedback
```

The harness has a two-layer feedback loop:
- **Layer 1 (always on):** after every Python file edit, `py_compile` runs automatically — syntax errors are caught in the same turn
- **Layer 2 (opt-out):** after each agent iteration, the failing tests are run and the output is injected back so the agent can retry (up to `--feedback-rounds`, default 3)

Score the predictions with the official evaluator (requires Docker):

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path benchmarks/results/<run>/predictions.jsonl \
  --max_workers 4 \
  --run_id my-run
```

## Project Structure

### Project-specific instructions

Create `.agentic/prompt.md` in your project root to inject extra instructions into every session:

```markdown
# My project rules
- Always use `ruff` to lint after editing Python files
- Prefer `httpx` over `requests`
- Tests live in `tests/` and use pytest
```

The file is loaded automatically — no config change needed.

```
agentic/
├── core/          # Agent loop, LLM clients (Anthropic + OpenAI), context, config
├── tools/         # Read, Write, Edit, Bash, Grep, Glob, LS, WebFetch, WebSearch, Task*, Agent
├── memory/        # Persistent memory with MEMORY.md index + MemoryWrite tool
├── skills/        # Slash-command skills (YAML) + built-ins
├── kernel/        # Persistent Python kernel (worker, manager, tool, config)
├── mcp/           # MCP stdio client + tool bridge
├── permissions/   # Allow/deny rule engine
├── hooks/         # Event-driven shell hooks
├── scheduling/    # APScheduler cron/interval jobs
├── sandbox/       # Docker sandbox (DockerSandbox, SandboxedBashTool, sandboxed file tools)
└── ui/            # prompt_toolkit REPL + Rich renderer

benchmarks/
├── run_swebench.py          # CLI entry point
└── swebench/
    ├── loader.py            # HuggingFace dataset loading
    ├── runner.py            # Per-instance agent runner
    ├── benchmark_agent.py   # BenchmarkAgentLoop with feedback loop
    ├── validating_tools.py  # py_compile-checking Edit/Write tools
    └── report.py            # Results summary

Dockerfile.sandbox           # Sandbox container image
specs/                       # Feature specifications
```

## Development

```bash
pip install -e ".[dev]"
pytest                  # run all tests (160 passing)
ruff check agentic/     # lint
```

## Tools reference

| Tool | Description |
|---|---|
| `Read` | Read a file with line numbers; detects images (vision) and binary; warns on secrets |
| `Write` | Create or overwrite a file |
| `Edit` | Replace a string in a file; whitespace-tolerant; shows coloured diff |
| `MultiEdit` | Apply N edits to one file atomically — all succeed or none apply |
| `Bash` | Persistent shell — cwd, env, and functions survive between calls |
| `Grep` | Regex search across files (ripgrep-backed; sandbox-aware) |
| `Glob` | List files matching a pattern, e.g. `**/*.py` (sandbox-aware) |
| `LS` | Structured directory listing with sizes and types (sandbox-aware) |
| `WebFetch` | Fetch a URL and return readable text |
| `WebSearch` | DuckDuckGo search, no API key required |
| `MemoryWrite` | Save or update a memory (upsert by name) |
| `MemoryRead` | Fetch the full body of a specific memory |
| `MemoryDelete` | Remove a stale or wrong memory |
| `TaskCreate/Update/List` | Track in-session TODO items; scoped per agent instance |
| `Agent` | Spawn a sub-agent; background tasks return a `task_id` you can poll |
| `PythonKernel` | Persistent Python interpreter (requires `--kernel`) |
| `AskUserQuestion` | Prompt the user for input mid-task |
