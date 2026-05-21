# Memory System Overview

## Purpose

The memory system gives the agent persistent knowledge that survives across sessions. Instead of re-learning the same facts about a user or project every conversation, the agent writes memories explicitly and reads them back automatically on the next session.

---

## Storage Layout

Memories are plain Markdown files on disk, one file per memory record, stored in a per-user, per-project directory:

```
~/.agentic/users/<user_id>/projects/<hash>/memory/
├── MEMORY.md                    ← index: names + descriptions only
├── feedback_use_ruff.md
├── project_entry_point.md
├── user_prefers_tabs.md
└── reference_grafana_board.md
```

The directory path is computed from `user_id + project_dir` (SHA-256 hash), so two users working on the same project get **fully isolated** memory stores and never see each other's memories.

**Code:** `agentic/core/config.py` → `ConfigManager.memory_dir()`

---

## File Format

Every memory file has a YAML frontmatter block followed by free-form Markdown body:

```markdown
---
name: use-ruff-before-commit
description: Always run ruff after editing Python files
metadata:
  type: feedback
  created_at: 2026-05-01T10:00:00Z
  updated_at: 2026-05-21T14:30:00Z
---

Always run `ruff check` after editing any Python file before committing.

**Why:** The repo has a pre-commit hook and CI fails on lint errors.
**How to apply:** Run `ruff check <file>` immediately after any Edit/Write on a .py file.
```

**Fields:**
- `name` — kebab-case slug, used as the unique key for upsert/delete
- `description` — one-line summary shown in the index
- `metadata.type` — one of `feedback`, `user`, `project`, `reference`
- `metadata.created_at / updated_at` — ISO UTC timestamps, updated on every write

**Code:** `agentic/memory/manager.py` → `MemoryRecord.to_markdown()` / `_load_file()`

---

## Memory Types

| Type | Purpose | Context budget |
|------|---------|----------------|
| `feedback` | Behavioral corrections and confirmed approaches | 6 000 chars |
| `user` | User's role, preferences, expertise | 3 000 chars |
| `project` | Platform, language, entry point, ongoing work, deadlines | 4 000 chars |
| `reference` | Pointers to external systems (dashboards, issue trackers, docs) | 2 000 chars |

Total budget injected into the system prompt per turn: **12 000 chars**.

**Code:** `agentic/memory/types.py`, `agentic/memory/manager.py` → `_CONTEXT_BUDGET`

---

## How Memories Are Loaded Into Context

Every agent turn, `load_for_context()` runs before the LLM call:

1. All memory files are read from disk.
2. Records are grouped by type and sorted newest-first within each group.
3. Types are emitted in priority order: feedback → user → project → reference.
4. Each type has a character budget; records are included until the budget is exhausted. Oversized records are truncated to fit.
5. The resulting text is injected into the system prompt via a `<<MEMORIES>>` sentinel.

```
### Feedback memories

**use-ruff-before-commit** — Always run ruff after editing Python files
Always run `ruff check` after editing any Python file before committing.
...

### Project memories

**entry-point** — Main entry point is agentic/__main__.py
...
```

**Code:** `agentic/memory/manager.py` → `load_for_context()`
**Injection point:** `agentic/core/agent.py` → `_build_system_prompt()` (replaces `<<MEMORIES>>`)

---

## MEMORY.md Index

A separate `MEMORY.md` file is maintained as a human-readable index. It shows every memory's name, description, and age — but not the full body. This is what `/memory` prints in the REPL.

```markdown
# Memory Index

## Feedback

- [use-ruff-before-commit](feedback_use_ruff.md) — Always run ruff after editing Python files (3d ago)

## Project

- [entry-point](project_entry_point.md) — Main entry point is agentic/__main__.py (1d ago)
```

The index is regenerated on every write/delete by `_save_index()`. It is capped at 200 lines to prevent the REPL `/memory` command from flooding the terminal.

---

## Tools

The agent manipulates memory via three tools available in the tool call loop:

### `MemoryWrite`
Upsert semantics — saves a new memory or overwrites an existing one with the same name. The agent is instructed to call this proactively when it learns something worth remembering.

```json
{
  "name": "use-ruff-before-commit",
  "description": "Always run ruff after editing Python files",
  "type": "feedback",
  "body": "Always run `ruff check` after editing any Python file..."
}
```

### `MemoryRead`
Fetches the full body of a specific memory by name. Used to read the current content before updating. Falls back to a fuzzy search suggestion if the exact name isn't found.

### `MemoryDelete`
Deletes a memory by name, with an optional `reason` for audit. The agent prefers updating over deleting when a memory is only partially outdated.

**Code:** `agentic/memory/tool.py`

---

## Search

`/memory search <query>` (REPL) and `MemoryManager.search()` (internal) use word-overlap relevance scoring:

- **Word overlap**: count of query words that appear in the memory's name + description + body.
- **Phrase bonus**: +2.0 if the full query string appears verbatim.
- **Recency boost**: +0.5 × (1 − age/30days), decaying to zero at 30 days.

Results are sorted descending by score.

**Code:** `agentic/memory/manager.py` → `search()`

---

## Staleness Detection

At session start, the agent checks for project memories not updated in 30+ days and prints a warning:

```
⚠  2 project memory/memories not updated in 30+ days — they may be stale.
Use /memory stale to review or /memory delete <name> to remove.
```

**Code:** `agentic/memory/manager.py` → `stale_project_memories(threshold_days=30)`
**Triggered by:** `agentic/core/agent.py` → first turn of each session

---

## REPL Commands

| Command | What it does |
|---------|-------------|
| `/memory` | Print the MEMORY.md index with age badges |
| `/memory search <query>` | Relevance-ranked search across all memories |
| `/memory delete <name>` | Delete a memory by name |
| `/memory stale` | List project memories not updated in 30+ days |
| `/btw <note>` | Instantly save a short note as a memory |
| `/btw [type] <note>` | Save with explicit type, e.g. `/btw [project] uses postgres` |

**Code:** `agentic/ui/repl.py`

---

## Data Flow Summary

```
Session start
    └── MemoryManager.load_for_context()
            └── reads all *.md files from memory_dir
            └── groups by type, sorts by recency
            └── trims to budget (12k chars total)
            └── injected into system prompt via <<MEMORIES>>

Agent turn (LLM response)
    └── LLM calls MemoryWrite(name, description, type, body)
            └── MemoryManager.upsert()
                    └── writes <type>_<slug>.md to disk
                    └── regenerates MEMORY.md index
            └── renderer prints "💾 Saved memory: <name>"

/btw command
    └── MemoryManager.upsert() directly (no LLM involved)

/memory stale check (first turn only)
    └── MemoryManager.stale_project_memories(30 days)
    └── prints warning if any found
```

---

## Key Files

| File | Role |
|------|------|
| `agentic/memory/types.py` | `MemoryType` enum and type descriptions |
| `agentic/memory/manager.py` | `MemoryManager` — all disk I/O, CRUD, search, context loading |
| `agentic/memory/tool.py` | `MemoryWrite`, `MemoryRead`, `MemoryDelete` tool implementations |
| `agentic/core/config.py` | `memory_dir()` — per-user, per-project path resolution |
| `agentic/core/agent.py` | Injects memory into system prompt; handles MemoryWrite notifications; staleness check |
| `agentic/ui/repl.py` | `/memory`, `/memory search`, `/memory delete`, `/memory stale`, `/btw` commands |
