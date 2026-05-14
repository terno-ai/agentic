# Agentic

An autonomous coding agent with memory, skills, MCP integration, and context summarization — inspired by Claude Code. Supports both **Anthropic** (Claude) and **OpenAI** (GPT / o-series) models.

## Features

- **Multi-provider** — Anthropic Claude and OpenAI GPT/o-series, switchable mid-session
- **Interactive REPL** — readline history, tab completion, slash commands
- **Persistent Memory** — file-based memory across sessions (user / feedback / project / reference types)
- **Skills** — slash commands (`/review`, `/init`, `/simplify`, `/security-review`, `/test`) with YAML-defined custom skills
- **MCP Integration** — connect any MCP server via stdio; its tools appear automatically in the agent
- **Context Summarization** — auto-summarizes conversation when approaching token limits
- **Permissions** — glob-based allow/deny rules for tool execution with interactive prompting
- **Hooks** — shell commands triggered on agent events (PreToolCall, PostToolCall, AgentStart, …)
- **Scheduling** — cron / interval autonomous agent runs via APScheduler
- **Sub-agents** — spawn specialized child agents for focused subtasks

## Quick Start

```bash
pip install -e .

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
agentic

# OpenAI
export OPENAI_API_KEY=sk-...
agentic --provider openai --model gpt-4o
```

## Usage

```bash
# Interactive REPL (Anthropic, default)
agentic

# Interactive REPL — OpenAI
agentic --provider openai --model gpt-4o

# Provider auto-detected from model name
agentic --model gpt-4o-mini          # → OpenAI
agentic --model claude-opus-4-7      # → Anthropic

# Run a single prompt non-interactively
agentic run "explain this codebase"
agentic run --provider openai --model gpt-4o "review the auth module"

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
| `/model <name>` | Switch model (e.g. `gpt-4o`, `claude-opus-4-7`) |
| `/provider <anthropic\|openai>` | Switch provider |
| `/memory` | Show memory index |
| `/memory search <query>` | Search memories |
| `/plan` | Toggle plan mode (read-only, no edits) |
| `/clear` | Clear conversation history |
| `/! <cmd>` or `!<cmd>` | Run a shell command directly |
| `/exit` or Ctrl+D | Exit |

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

## MCP Servers

Any MCP-compatible server defined in `mcp_servers` is started automatically and its tools are injected into the agent. Tool names are namespaced as `mcp__<server>__<tool>`.

```bash
# Example: use the filesystem MCP server
agentic --model gpt-4o
# Inside REPL, the agent can now call mcp__filesystem__read_file, etc.
```

## Memory System

The agent automatically saves important context across sessions. Four memory types:

| Type | When saved |
|---|---|
| `user` | User's role, preferences, expertise |
| `feedback` | Corrections and confirmed approaches |
| `project` | Ongoing work, goals, deadlines |
| `reference` | Pointers to external systems/docs |

Memories live at `~/.agentic/projects/<hash>/memory/` and are loaded into every session via `MEMORY.md`.

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
└── ui/            # prompt_toolkit REPL + Rich renderer
```

## Development

```bash
pip install -e ".[dev]"
pytest                  # run all tests
ruff check agentic/     # lint
```
