# Agentic — Project Context

## What This Is
A production-quality autonomous coding agent that mirrors Claude Code's architecture.
Implemented in Python as a package called `agentic`.

## Architecture
```
agentic/
├── core/          # Agent brain: loop, LLM client, context, conversation, config
├── tools/         # Built-in tools: Bash, Read, Write, Edit, WebFetch, WebSearch, Task*, Agent
├── memory/        # Persistent file-based memory with MEMORY.md index
├── skills/        # Slash-command skills (YAML definitions + runner)
│   └── builtin/   # Built-in skills: init, review, simplify, security-review, test
├── mcp/           # MCP client (stdio JSON-RPC) + tool bridge
├── permissions/   # Allow/deny rules with interactive prompting
├── hooks/         # Shell commands triggered on events (PreToolCall, PostToolCall, etc.)
├── scheduling/    # Cron/interval job scheduling with APScheduler
└── ui/            # Terminal REPL (prompt_toolkit) + Rich renderer
```

## Key Files
- `agentic/core/agent.py` — Main AgentLoop class, the orchestrator
- `agentic/core/llm.py` — AnthropicClient with streaming events
- `agentic/core/context.py` — Token tracking + auto-summarization
- `agentic/core/config.py` — Layered settings (global < project < env)
- `agentic/main.py` — Typer CLI: `agentic`, `agentic run`, `agentic skills`, etc.

## Development Commands
```bash
# Install in editable mode
pip install -e ".[dev]"

# Run interactive REPL
agentic

# Run with a prompt
agentic run "explain this codebase"

# Run tests
pytest

# Lint
ruff check agentic/
```

## Tech Stack
- Python 3.11+, `anthropic` SDK, `rich`, `prompt_toolkit`, `typer`, `pydantic` v2
- `apscheduler` for scheduling, `httpx` for web tools, `pyyaml` for skills

## Configuration
Settings live in `~/.agentic/settings.json` (global) and `.agentic/settings.json` (project).
Key settings: `model`, `permissions.allow/deny`, `mcp_servers`, `hooks`.

## Conventions
- All tools inherit from `agentic.tools.base.Tool` (ABC)
- Skills are YAML files with `name`, `description`, `prompt` (with `{{args}}` placeholder)
- Memory files use YAML frontmatter: `name`, `description`, `metadata.type`
- Hook commands receive env vars: `HOOK_EVENT`, `TOOL_NAME`, `TOOL_INPUT`, `TOOL_RESULT`
