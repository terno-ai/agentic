# Autonomous Agent — Architecture & Implementation Plan

## Overview

Build a production-quality autonomous coding agent (`agentic`) that mirrors Claude Code's capabilities:
interactive REPL, persistent memory, skills/slash-commands, MCP servers, context summarization,
sub-agent spawning, permissions, hooks, and scheduling.

---

## Feature Parity with Claude Code

| Feature | Description |
|---|---|
| **REPL / CLI** | Interactive terminal with readline, history, syntax highlighting |
| **Agent Loop** | LLM + tool-use loop with streaming, token counting |
| **Tool System** | Read, Write, Edit, Bash, WebFetch, WebSearch, Agent, Task* |
| **Memory** | File-based persistent memory (user/feedback/project/reference types) |
| **Skills** | `/skill-name` slash commands — built-in and user-defined YAML skills |
| **MCP Integration** | MCP client that connects to external MCP servers, bridges tools |
| **Context Summarization** | Auto-summarize conversation when approaching token limits |
| **Permissions** | Allow/deny tool execution per project or globally |
| **Hooks** | Shell commands triggered on agent events (pre/post tool calls) |
| **Scheduling** | Cron-based and one-shot scheduled agent runs |
| **Sub-agents** | Spawn specialized or general-purpose sub-agents |
| **AGENT.md** | Project-level docs auto-loaded into system prompt |
| **Plan Mode** | Structured planning before execution (read-only mode) |
| **Settings** | JSON settings with project-local and global overrides |
| **Multi-model** | Support any Anthropic model; configurable per session |

---

## Technology Stack

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Rich ecosystem, Anthropic SDK first-class |
| LLM SDK | `anthropic` | Native tool use, streaming, caching |
| Terminal UI | `rich` + `prompt_toolkit` | Markdown rendering, history, completions |
| CLI | `typer` | Type-safe CLI with auto-help |
| HTTP | `httpx` | Async, used for WebFetch |
| Data models | `pydantic` v2 | Validation, serialization |
| Scheduling | `apscheduler` | Cron + interval jobs |
| MCP | `mcp` (official SDK) | stdio/SSE MCP client |
| Config | `python-dotenv` + JSON | env vars + structured config |

---

## Project Structure

```
agentic/
├── agentic/                   # Main package
│   ├── __init__.py
│   ├── main.py                # CLI entry point (typer app)
│   │
│   ├── core/                  # Agent brain
│   │   ├── agent.py           # AgentLoop — main REPL + tool-use loop
│   │   ├── llm.py             # AnthropicClient wrapper (streaming, caching)
│   │   ├── context.py         # ContextManager — token counting, summarization
│   │   ├── conversation.py    # ConversationHistory — message list management
│   │   └── config.py          # Settings — layered JSON config
│   │
│   ├── tools/                 # Built-in tools
│   │   ├── base.py            # Tool ABC, ToolResult, ToolCall models
│   │   ├── registry.py        # ToolRegistry — registration & dispatch
│   │   ├── bash.py            # BashTool — sandboxed shell execution
│   │   ├── file_tools.py      # ReadTool, WriteTool, EditTool
│   │   ├── web_tools.py       # WebFetchTool, WebSearchTool
│   │   ├── task_tools.py      # TaskCreate/Get/List/Update/Stop/Output
│   │   ├── agent_tool.py      # AgentTool — spawn sub-agents
│   │   └── notification.py    # PushNotification, AskUserQuestion tools
│   │
│   ├── memory/                # Persistent memory system
│   │   ├── manager.py         # MemoryManager — CRUD, MEMORY.md index
│   │   └── types.py           # MemoryType enum, Memory dataclass
│   │
│   ├── skills/                # Slash-command skills
│   │   ├── manager.py         # SkillManager — discover & load skills
│   │   ├── runner.py          # SkillRunner — execute skill prompts
│   │   └── builtin/           # Built-in skill YAML definitions
│   │       ├── init.yaml      # /init — initialize AGENT.md
│   │       ├── review.yaml    # /review — PR review
│   │       ├── simplify.yaml  # /simplify — code quality check
│   │       └── security.yaml  # /security-review
│   │
│   ├── mcp/                   # Model Context Protocol
│   │   ├── client.py          # MCPClient — stdio/SSE connection
│   │   ├── server_manager.py  # MCPServerManager — start/stop servers
│   │   └── bridge.py          # Converts MCP tools → agent Tool objects
│   │
│   ├── permissions/           # Allow/deny system
│   │   ├── manager.py         # PermissionManager — check & prompt
│   │   └── patterns.py        # Glob/regex pattern matching for rules
│   │
│   ├── hooks/                 # Event hooks
│   │   ├── manager.py         # HookManager — register & fire hooks
│   │   └── events.py          # Event enum: PreToolCall, PostToolCall, etc.
│   │
│   ├── scheduling/            # Task scheduling
│   │   ├── manager.py         # ScheduleManager — APScheduler wrapper
│   │   └── models.py          # ScheduledTask, CronJob models
│   │
│   └── ui/                    # Terminal interface
│       ├── repl.py            # REPL — prompt_toolkit session, history
│       ├── renderer.py        # Markdown/code/tool-output rendering
│       ├── completions.py     # Autocompletion (skills, files, tools)
│       └── keybindings.py     # Keybinding configuration
│
├── tests/
│   ├── unit/
│   └── integration/
│
├── .agentic/                  # Project-level config (like .claude/)
│   ├── settings.json
│   └── skills/                # Project-specific skills
│
├── AGENT.md                   # Auto-loaded project docs
├── pyproject.toml
└── README.md
```

---

## Core Data Flow

```
User Input
    │
    ▼
REPL (prompt_toolkit)
    │  parse slash-commands → SkillRunner
    │  parse /! commands    → BashTool direct
    ▼
AgentLoop.run_turn()
    │
    ├─ inject system prompt:
    │   AGENT.md + memory summaries + tool schemas + MCP tool schemas
    │
    ├─ ContextManager.maybe_summarize() ← if tokens > threshold
    │
    ▼
AnthropicClient.stream_message()
    │  model: claude-sonnet-4-6 (configurable)
    │  tools: [all registered tools]
    │  messages: conversation history
    │
    ▼
stream: text_delta | tool_use
    │
    ├─ text_delta → Renderer.stream_markdown()
    │
    └─ tool_use block:
        │
        ├─ PermissionManager.check(tool, input)
        │   └─ if denied → return error ToolResult
        │
        ├─ HookManager.fire(PreToolCall, tool, input)
        │
        ├─ ToolRegistry.execute(tool_name, tool_input)
        │   ├─ built-in tool → direct execution
        │   └─ MCP tool    → MCPClient.call_tool()
        │
        ├─ HookManager.fire(PostToolCall, tool, result)
        │
        └─ ToolResult → append to messages → next LLM call
```

---

## Key Components — Deep Dive

### 1. Agent Loop (`core/agent.py`)

```python
class AgentLoop:
    """
    Main agentic loop. Manages:
    - Conversation history
    - Tool dispatch
    - Streaming output
    - Sub-agent spawning
    """
    MAX_TOOL_ITERATIONS = 50   # safety limit
    
    async def run_turn(self, user_message: str) -> str:
        # 1. Append user message
        # 2. Check & summarize context if needed
        # 3. Build system prompt (AGENT.md + memory)
        # 4. Call LLM with tools
        # 5. Process streaming response
        # 6. If tool_use blocks → execute → loop back to step 4
        # 7. Return final text response
```

### 2. Context Summarization (`core/context.py`)

- Track token usage from every API response
- When `total_tokens > summarize_threshold` (default 80k):
  1. Take all messages except the last N (default 10)
  2. Call LLM to produce a structured summary
  3. Replace old messages with a single `{"role": "user", "content": "<summary>"}` + assistant acknowledgement
  4. Keep recent messages intact for continuity

### 3. Memory System (`memory/manager.py`)

Four memory types stored as markdown files with YAML frontmatter:
- **user** — who the user is, preferences, expertise
- **feedback** — guidance on behavior (corrections + confirmations)
- **project** — ongoing work, goals, deadlines
- **reference** — pointers to external systems

`MEMORY.md` is an index always loaded into context.
Memory files live at `~/.agentic/projects/<cwd-hash>/memory/`.

### 4. Skills System (`skills/`)

Skills are YAML files describing a slash command:
```yaml
name: review
description: Review a pull request
prompt: |
  Review the changes in {{args}} focusing on:
  - Correctness and logic
  - Security vulnerabilities  
  - Performance implications
  ...
tools_allowed: [Bash, Read, WebFetch]
read_only: false
```

`SkillManager` discovers skills from:
1. Built-in package skills
2. `~/.agentic/skills/` (global user skills)  
3. `.agentic/skills/` (project skills)

### 5. MCP Integration (`mcp/`)

MCP servers defined in settings:
```json
{
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "github": {
      "command": "uvx",
      "args": ["mcp-server-github"],
      "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    }
  }
}
```

`MCPServerManager` starts each server as subprocess, `MCPClient` communicates via stdio JSON-RPC.
`MCPBridge` converts MCP tool schemas → `Tool` objects injected into the agent's tool registry.

### 6. Permissions (`permissions/manager.py`)

Layered allow/deny rules (checked in order):
1. Project `.agentic/settings.json` → `permissions.allow[]` / `permissions.deny[]`
2. Global `~/.agentic/settings.json`
3. Prompt user if no rule matches (with remember option)

Pattern format: `"Bash(git *)"`, `"Read"`, `"Write(/etc/*)"` (glob-style).

### 7. Hooks (`hooks/manager.py`)

Events fired with context payload:
- `PreToolCall` — before any tool runs (can block)
- `PostToolCall` — after tool runs (receives result)
- `AgentStart` / `AgentStop`
- `UserMessage`
- `AssistantMessage`

Hook definitions in settings:
```json
{
  "hooks": {
    "PostToolCall": [
      {"matcher": "Bash", "command": "echo 'Bash ran: $TOOL_INPUT' >> ~/agent.log"}
    ]
  }
}
```

### 8. Sub-Agent Spawning (`tools/agent_tool.py`)

`AgentTool` creates a child `AgentLoop` with:
- Isolated conversation history
- Subset of parent tools (configurable)
- Optional `isolation: worktree` → git worktree for code changes
- Result returned as tool output to parent

### 9. Scheduling (`scheduling/manager.py`)

Uses APScheduler with SQLite job store for persistence:
- `CronCreate` — create cron/interval job
- `CronDelete` — remove job
- `CronList` — list active jobs
- Jobs run agent with specified prompt autonomously

---

## Settings Schema

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 8192,
  "context_summarize_threshold": 80000,
  "context_keep_recent": 10,
  "permissions": {
    "allow": ["Read", "Bash(git *)", "Bash(ls *)"],
    "deny": ["Bash(rm -rf *)"]
  },
  "mcp_servers": {},
  "hooks": {},
  "skills_dirs": [],
  "memory_dir": "~/.agentic/projects/{project_hash}/memory",
  "history_file": "~/.agentic/history",
  "theme": "dark",
  "stream": true,
  "auto_memory": true
}
```

---

## Implementation Phases

### Phase 1 — Core Agent (MVP)
- [x] Project scaffold (pyproject.toml, package structure)
- [x] `AnthropicClient` with streaming
- [x] `AgentLoop` with basic tool-use
- [x] Tools: Read, Write, Edit, Bash
- [x] REPL with prompt_toolkit
- [x] Settings system

### Phase 2 — Memory & Context
- [x] Memory manager (CRUD + MEMORY.md index)
- [x] Auto-memory detection in agent loop
- [x] Context summarization
- [x] AGENT.md loading

### Phase 3 — Skills & Permissions
- [x] Skill YAML loading and execution
- [x] Permission manager with allow/deny
- [x] Hook system

### Phase 4 — MCP & Sub-agents
- [x] MCP client and bridge
- [x] Sub-agent spawning with Agent tool
- [x] Task tracking tools

### Phase 5 — Advanced Features
- [x] Scheduling (cron jobs)
- [x] Web tools (Fetch, Search)
- [x] Plan mode
- [x] Autocompletion
- [x] Keybindings config

---

## Running the Agent

```bash
# Install
pip install -e .

# Start interactive REPL
agentic

# Run with a prompt
agentic run "explain this codebase"

# Use a specific model
agentic --model claude-opus-4-7

# With MCP server
agentic --mcp-server filesystem

# List scheduled jobs
agentic schedule list

# Add a skill
agentic skills add ./my-skill.yaml
```
