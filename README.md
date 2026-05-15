# Agentic

An autonomous coding agent with memory, skills, MCP integration, context summarization, and Docker sandboxing — inspired by Claude Code. Supports both **Anthropic** (Claude) and **OpenAI** (GPT / o-series) models.

## Features

- **Multi-provider** — Anthropic Claude and OpenAI GPT/o-series (including reasoning models o1/o3/o4-mini), switchable mid-session
- **Docker Sandbox** — run all shell commands inside an isolated container with memory/CPU limits and network control
- **Interactive REPL** — readline history, tab completion, slash commands
- **Persistent Memory** — file-based memory across sessions (user / feedback / project / reference types) with smart context retention
- **Skills** — slash commands (`/review`, `/init`, `/simplify`, `/security-review`, `/test`) with YAML-defined custom skills
- **MCP Integration** — connect any MCP server via stdio; its tools appear automatically in the agent
- **Context Summarization** — auto-summarizes conversation while preserving critical project facts (platform, language, entry point)
- **Permissions** — glob-based allow/deny rules for tool execution with interactive prompting
- **Hooks** — shell commands triggered on agent events (PreToolCall, PostToolCall, AgentStart, …)
- **Scheduling** — cron / interval autonomous agent runs via APScheduler
- **Sub-agents** — spawn specialized child agents for focused subtasks
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

# With Docker sandbox (isolated, safe execution)
agentic --sandbox
```

## Usage

```bash
# Interactive REPL (Anthropic, default)
agentic

# OpenAI — provider auto-detected from model name
agentic --model gpt-4o-mini          # → OpenAI
agentic --model o4-mini              # → OpenAI (reasoning model)
agentic --model claude-opus-4-7      # → Anthropic

# Docker sandbox — all Bash commands run inside an isolated container
agentic --sandbox
agentic --sandbox --model gpt-4o

# Multi-user sandbox — each user gets an isolated container and workspace
agentic --sandbox --user alice
agentic --sandbox --user bob
AGENTIC_USER=alice agentic --sandbox   # via env var

# Run a single prompt non-interactively
agentic run "explain this codebase"
agentic run --provider openai --model gpt-4o "review the auth module"
agentic run --sandbox --user alice "build the dashboard"

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
| `/memory` | Show memory index |
| `/memory search <query>` | Search memories |
| `/plan` | Toggle plan mode (read-only, no edits) |
| `/clear` | Clear conversation history |
| `/! <cmd>` or `!<cmd>` | Run a shell command directly |
| `/exit` or Ctrl+D | Exit |

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

**`cd` persists between commands** — working directory is tracked across calls so multi-step workflows behave naturally.

**File tools (Read/Write/Edit) are sandbox-aware** — `/workspace/...` paths are automatically remapped to the user's host workspace, so the agent works with consistent paths regardless of tool used.

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

## Memory System

The agent automatically saves important context across sessions. Four memory types:

| Type | When saved |
|---|---|
| `user` | User's role, preferences, expertise |
| `feedback` | Corrections and confirmed approaches |
| `project` | Ongoing work, goals, deadlines — platform, language, entry point |
| `reference` | Pointers to external systems/docs |

Project facts (platform, language, framework, entry point) are saved immediately when learned so they survive context summarization. Memories live at `~/.agentic/projects/<hash>/memory/`.

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

```
agentic/
├── core/          # Agent loop, LLM clients (Anthropic + OpenAI), context, config
├── tools/         # Read, Write, Edit, Bash, WebFetch, WebSearch, Task*, Agent
├── memory/        # Persistent memory with MEMORY.md index
├── skills/        # Slash-command skills (YAML) + built-ins
├── mcp/           # MCP stdio client + tool bridge
├── permissions/   # Allow/deny rule engine
├── hooks/         # Event-driven shell hooks
├── scheduling/    # APScheduler cron/interval jobs
├── sandbox/       # Docker sandbox (DockerSandbox, SandboxedBashTool)
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
```

## Development

```bash
pip install -e ".[dev]"
pytest                  # run all tests (101 passing)
ruff check agentic/     # lint
```
