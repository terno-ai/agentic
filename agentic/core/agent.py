"""Core agent loop — orchestrates LLM, tools, memory, skills, and context management."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from agentic.core.config import ConfigManager
from agentic.core.context import ContextManager
from agentic.core.conversation import ConversationHistory
from agentic.core.llm import create_llm_client, TextDelta, ThinkingDelta, ThinkingBlockComplete, ToolUseStart, ToolInputDelta, MessageComplete, UsageInfo
from agentic.hooks.events import HookEvent
from agentic.hooks.manager import HookManager
from agentic.memory.manager import MemoryManager
from agentic.memory.types import MemoryType
from agentic.mcp.bridge import MCPServerManager
from agentic.permissions.manager import PermissionManager
from agentic.skills.manager import SkillManager
from agentic.skills.runner import SkillRunner
from agentic.tools.base import ToolResult
from agentic.tools.registry import ToolRegistry


SYSTEM_PROMPT_BASE = """You are Agentic, an autonomous coding agent. You help engineers with:
- Writing, reading, and editing code
- Running shell commands and interpreting output
- Debugging and fixing bugs
- Explaining codebases and concepts
- Managing files and projects

## When to use tools vs. text
- User asks to **create / build / generate** something → use Write to create the actual files.
  Do not show code in a markdown block and stop — that is a description, not a deliverable.
- User asks to **fix / edit** something → Read the file first, then use Edit for targeted changes.
- User asks to **download / fetch / get** a file → use Bash with curl or wget. Never tell the
  user how to download something themselves — just do it:
    `curl -L "https://example.com/file.mp3" -o file.mp3`
  You have full internet access via Bash. Use it.
- User asks a **question or wants an explanation** → answer in text; markdown code blocks are fine.

When it is ambiguous whether the user wants files created or just wants to see code,
prefer creating the files. The user can always delete them; but if they asked for a game
and got a markdown snippet, they got nothing useful.

## Planning and task tracking

For any non-trivial request (more than a single file change or one-liner fix):

1. **Think first.** Before touching any tool, reason through:
   - What is the user actually asking for?
   - What are the discrete steps needed?
   - What do I need to read or explore first?
   - What could go wrong?

2. **Create tasks** for each meaningful step using `TaskCreate`, then work through them in order:
   - Mark each task `in_progress` with `TaskUpdate` when you start it.
   - Mark it `completed` (or `failed`) when done.
   - Never batch-mark — update status as you go so the user can follow progress.

3. **What counts as non-trivial:**
   - Building a new feature, app, or script with multiple files
   - Debugging an issue that requires investigation across files
   - Refactoring that touches more than one file
   - Any task where the steps are not immediately obvious

4. **What does NOT need tasks:** single-file edits, answering a question, running one command.

Example flow for "build a todo app":
- TaskCreate("Understand requirements and plan files")
- TaskCreate("Create index.html with structure")
- TaskCreate("Create app.js with logic")
- TaskCreate("Test in browser and fix issues")
- … then work through them one by one, updating status at each step.

## Core principles
- Be direct and concise. Prefer action over lengthy explanation.
- **Never say "I can't" for things the tools can do.** You have Bash — you can run any shell
  command, download files with curl/wget, install packages, call APIs, run scripts, etc.
  If something is possible in a terminal, do it.
- **Before exploring the filesystem or making assumptions about a project, check what you
  already know** from the conversation history, your memories, and any AGENT.md.
  Never assume a project's language, platform, or entry point without evidence — ask yourself:
  "Did the user say this is a browser game? A Python app? A CLI tool?" and act accordingly.
- Use tools proactively. Read files before editing them.
- Write correct, secure, idiomatic code. No placeholders or half-implementations.
- Default to no comments unless the WHY is non-obvious.
- Never add features beyond what's asked. Don't anticipate hypothetical needs.
- Trust framework guarantees; only validate at system boundaries.

## Memory system

You have persistent memory via `MemoryWrite`, `MemoryRead`, and `MemoryDelete` tools.

**When to save** (call `MemoryWrite` proactively — don't wait to be asked):
- User preferences, expertise, working style → type=`user`
- Corrections you received or approaches that were confirmed → type=`feedback`
- Project facts: platform, language, framework, entry point, constraints → type=`project`
- External systems, docs, APIs the user references → type=`reference`

**How to write good memories:**
- `name`: short kebab-case slug, e.g. `user-prefers-tabs`, `project-entry-point`
- `description`: one line that tells a future session whether this memory is relevant
- `body` for feedback: rule first, then **Why:** (the reason given) and **How to apply:**
- `body` for project: platform, language, entry point, key files, constraints

**When to update/delete:**
- Re-save with the same `name` to update (upsert semantics)
- Call `MemoryRead` first if you need to see what's already saved
- Call `MemoryDelete` when a memory is stale or wrong — don't let bad facts persist

**Project facts must be saved immediately** when learned — they prevent wrong assumptions
(e.g. searching for `main.py` in a browser-only JavaScript game).

## Working directory
<<CWD>>

## Loaded context
<<AGENT_MD>>

## Memories
<<MEMORIES>>
"""

def _git_status_snippet() -> str:
    """Return a compact git status string, or empty string if not a git repo."""
    import subprocess
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=Path.cwd(),
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=3, cwd=Path.cwd(),
        ).stdout.strip()
        if not branch:
            return ""
        lines = [f"branch: {branch}"]
        if status:
            lines.append(status)
        else:
            lines.append("(clean)")
        return "\n".join(lines)
    except Exception:
        return ""




class AgentLoop:
    """Main agent loop. One instance per session (or sub-agent call)."""

    def __init__(
        self,
        config: ConfigManager,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        is_subagent: bool = False,
        renderer: Any | None = None,
        sandbox: Any | None = None,  # DockerSandbox instance, or None for host execution
        kernel: Any | None = None,   # KernelManager instance, or None
        user_id: str = "default",
    ):
        self._config = config
        self._is_subagent = is_subagent
        self._renderer = renderer
        self._sandbox = sandbox
        self._kernel = kernel

        settings = config.settings
        resolved_model = model or settings.model
        self._llm = create_llm_client(
            provider=settings.provider,
            model=resolved_model,
            api_key=settings.api_key,
            openai_api_key=settings.openai_api_key,
        )
        self._conversation = ConversationHistory()
        self._context_mgr = ContextManager(
            self._llm,
            self._conversation,
            summarize_threshold=settings.context_summarize_threshold,
            keep_recent=settings.context_keep_recent,
        )
        self._memory = MemoryManager(config.memory_dir())
        self._skill_manager = SkillManager(
            project_dir=Path.cwd(),
            extra_dirs=settings.skills_dirs,
        )
        # Inside a sandbox the container provides the isolation boundary, so
        # interactive permission prompts are unnecessary — auto-allow everything.
        needs_prompt = not is_subagent and sandbox is None
        self._permission_mgr = PermissionManager(
            config=settings.permissions,
            prompt_fn=self._permission_prompt if needs_prompt else None,
        )
        self._hook_mgr = HookManager(
            {k: v for k, v in settings.hooks.items()}
        )
        self._mcp_manager = MCPServerManager()
        self._tool_registry = ToolRegistry(permission_manager=self._permission_mgr)
        self._allowed_tools = allowed_tools
        self._iteration_count = 0
        self._attached: set[str] = set()  # paths/URLs already attached this session

        self._setup_tools()

    def _setup_tools(self) -> None:
        from agentic.tools.bash import BashTool, MonitorTool
        from agentic.tools.file_tools import ReadTool, WriteTool, EditTool, MultiEditTool
        from agentic.sandbox.sandboxed_bash import SandboxedBashTool
        from agentic.sandbox.sandboxed_file_tools import (
            SandboxedReadTool, SandboxedWriteTool, SandboxedEditTool,
        )
        from agentic.tools.web_tools import WebFetchTool, WebSearchTool
        from agentic.tools.search_tools import GrepTool, GlobTool, LSTool
        from agentic.tools.task_tools import (
            TaskStore,
            TaskCreateTool, TaskGetTool, TaskListTool,
            TaskUpdateTool, TaskStopTool, TaskOutputTool,
        )
        task_store = TaskStore()
        self._context_mgr._task_store = task_store
        from agentic.tools.agent_tool import AgentTool
        from agentic.tools.notification import AskUserQuestionTool, PushNotificationTool
        from agentic.memory.tool import MemoryWriteTool, MemoryReadTool, MemoryDeleteTool

        if self._sandbox is not None:
            workspace = self._sandbox._workspace
            bash_tool  = SandboxedBashTool(self._sandbox)
            read_tool   = SandboxedReadTool(workspace)
            write_tool  = SandboxedWriteTool(workspace)
            edit_tool   = SandboxedEditTool(workspace)
            medit_tool  = MultiEditTool()   # MultiEdit always runs on host (remapped paths)
        else:
            bash_tool   = BashTool(cwd=Path.cwd())
            read_tool   = ReadTool()
            write_tool  = WriteTool()
            edit_tool   = EditTool()
            medit_tool  = MultiEditTool()

        all_tools = [
            bash_tool,
            MonitorTool(cwd=Path.cwd()),
            read_tool,
            write_tool,
            edit_tool,
            medit_tool,
            WebFetchTool(),
            WebSearchTool(),
            GrepTool(sandbox=self._sandbox),
            GlobTool(sandbox=self._sandbox),
            LSTool(sandbox=self._sandbox),
            TaskCreateTool(task_store),
            TaskGetTool(task_store),
            TaskListTool(task_store),
            TaskUpdateTool(task_store),
            TaskStopTool(task_store),
            TaskOutputTool(task_store),
            AgentTool(config_manager=self._config),
            AskUserQuestionTool(ask_fn=self._ask_user if not self._is_subagent else None),
            PushNotificationTool(),
            MemoryWriteTool(self._memory),
            MemoryReadTool(self._memory),
            MemoryDeleteTool(self._memory),
        ]

        if self._kernel is not None:
            from agentic.kernel.tool import KernelTool
            all_tools.append(KernelTool(self._kernel))

        if self._allowed_tools:
            all_tools = [t for t in all_tools if t.name in self._allowed_tools]

        self._tool_registry.register_many(all_tools)

    async def start_mcp_servers(self) -> None:
        """Connect to configured MCP servers and register their tools."""
        settings = self._config.settings
        for name, server_config in settings.mcp_servers.items():
            try:
                await self._mcp_manager.connect(
                    name=name,
                    command=server_config.command,
                    args=server_config.args,
                    env=server_config.env,
                )
            except Exception as e:
                if self._renderer:
                    self._renderer.print_system(f"MCP server '{name}' failed to start: {e}")

        mcp_tools = await self._mcp_manager.get_all_tools()
        if mcp_tools:
            self._tool_registry.register_many(mcp_tools)
            if self._renderer:
                self._renderer.print_system(f"MCP: loaded {len(mcp_tools)} tools")

    def _build_system_prompt(self) -> str | list[dict[str, Any]]:
        # Inside the sandbox the agent always works in /workspace; outside it
        # uses the real host path. Keeping cwd consistent prevents the agent
        # from mixing /workspace and host paths in the same session.
        cwd = "/workspace" if self._sandbox is not None else str(Path.cwd())

        agent_md_path = Path.cwd() / "AGENT.md"
        agent_md = (
            agent_md_path.read_text(encoding="utf-8")
            if agent_md_path.exists()
            else "(No AGENT.md found. Run /init to create one.)"
        )

        memories_text = self._memory.load_for_context()

        system_text = (
            SYSTEM_PROMPT_BASE
            .replace("<<CWD>>", cwd)
            .replace("<<AGENT_MD>>", agent_md)
            .replace("<<MEMORIES>>", memories_text or "(no memories yet — save facts with MemoryWrite)")
        )

        settings = self._config.settings

        # Git status snapshot — helps the agent orient without an explicit Bash call
        git_info = _git_status_snippet()
        if git_info:
            system_text += f"\n\n## Git status\n```\n{git_info}\n```"

        # Project-specific extra instructions from .agentic/prompt.md
        extra_prompt_path = Path.cwd() / ".agentic" / "prompt.md"
        if extra_prompt_path.exists():
            try:
                extra = extra_prompt_path.read_text(encoding="utf-8").strip()
                if extra:
                    system_text += f"\n\n## Project-specific instructions\n{extra}"
            except Exception:
                pass

        if self._sandbox is not None:
            sb = settings.sandbox
            system_text += f"""

## Sandbox environment
You are running inside a dedicated Docker sandbox container (user: {self._sandbox.user_id}).
The host filesystem is NOT accessible. Your working directory is /workspace (mounted read-write).

**Pre-installed system libraries** (no sudo needed for these):
- Python 3 + pip, Node.js 20 + npm
- curl, wget, git, ffmpeg, ripgrep, build-essential, pkg-config
- Cairo (libcairo2-dev), Pango (libpango1.0-dev), GDK, libffi-dev — pycairo/manim C deps
- LaTeX: texlive-latex-base/extra, dvipng, dvisvgm — manim math rendering
- OpenGL: libgl1-mesa-glx — manim OpenGL backend

**Installing more packages:**
- Python:  `pip install <package>`  — works directly, no sudo, no venv needed
- System:  `sudo apt-get install -y <package>`  — passwordless sudo is configured
- Node:    `npm install <package>`

**Rules:**
- NEVER say you "can't install" or ask the user to install things manually.
- For `pip install manim`, just run it — all C dependencies are already present.
- If a pip install fails due to a missing system lib, run `sudo apt-get install -y <lib>` first, then retry.
Network: {"enabled (internet accessible)" if sb.network != "none" else "disabled (offline)"}
Memory limit: {sb.memory_limit}  CPU limit: {sb.cpu_limit}
"""

        if self._kernel is not None:
            kc = settings.kernel
            system_text += f"""

## Python kernel
You have a persistent Python kernel (PythonKernel tool). Variables survive between calls.

### When to use PythonKernel vs. Write/Bash

**Use PythonKernel when the goal is to GET RESULTS:**
- Exploring or analyzing data (load CSV, inspect DataFrame, plot charts)
- Running one-off calculations or experiments
- Iterative work where each step builds on previous variables
- Verifying that code works before deciding to save it

**Use Write (create a .py file) when the goal is to DELIVER A FILE:**
- User asks to "create a script", "build a tool", "write a program", "make a game"
- The code will be run by the user (e.g. `python script.py`), imported, deployed, or shared
- The project already has source files and you are adding to it
- Multi-file projects (apps, libraries, packages)

**Rule of thumb:** Ask yourself — "Is the user expecting a file they can keep and run later?"
- YES → Write the .py file (or whatever source file type fits the project)
- NO (they want to see analysis/results right now) → use PythonKernel

**Use Bash for:** shell ops, git, curl, package installation (`pip install`).

**Key rules:**
- If code calls input(), always pass answers via the `stdin` parameter.
- On OOM: `del large_variable` then retry, or use action='restart'.
- On timeout: **retry the same code with a larger timeout** — do not give up.
  Estimate how long the task needs and pass that as the `timeout` parameter:
    PythonKernel(action="execute", code="...", timeout=300)
  Use timeout=0 to disable the limit entirely for very long tasks (training, large downloads).
- If the kernel is truly hung (not just slow): action='interrupt', then action='restart'.
- Use action='inspect' to see what variables are in scope.

Memory limit: {kc.memory_limit_mb} MB  Default timeout: {kc.default_timeout_s}s (override per-call with timeout=N)
"""

        if settings.plan_mode:
            system_text += "\n\n**PLAN MODE ACTIVE**: Only analyze and plan. Do NOT use Write, Edit, or Bash tools. Describe what you would do instead."

        # Anthropic supports prompt caching via cache_control; OpenAI just uses a plain string.
        from agentic.core.llm import OpenAIClient
        if isinstance(self._llm, OpenAIClient):
            return system_text

        return [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    async def run_turn(self, user_message: str) -> str:
        """Process one user turn through the full agent loop."""
        settings = self._config.settings

        # On the very first turn, warn about stale project memories
        if self._iteration_count == 0 and self._renderer and not self._is_subagent:
            stale = self._memory.stale_project_memories(threshold_days=30)
            if stale:
                names = ", ".join(r.name for r in stale[:3])
                self._renderer.print_system(
                    f"⚠  {len(stale)} project memory/memories not updated in 30+ days "
                    f"({names}{'…' if len(stale) > 3 else ''}). "
                    "Use /memory stale to review or /memory delete <name> to remove."
                )

        # Check for skill invocation
        parsed = SkillRunner.parse_slash_command(user_message)
        if parsed:
            skill_name, args = parsed
            skill = self._skill_manager.get(skill_name)
            if skill:
                if self._renderer:
                    self._renderer.print_skill(skill_name)
                user_message = SkillRunner.build_prompt(skill, args)
            else:
                if self._renderer:
                    self._renderer.print_error(f"Unknown skill: /{skill_name}. Try /skills to list available skills.")
                return ""

        await self._hook_mgr.fire(HookEvent.USER_MESSAGE, {"message": user_message})

        # Auto-detect file paths and URLs mentioned in the message and attach their content
        attachments = await self._collect_attachments(user_message)
        if attachments:
            combined = user_message + "\n\n" + "\n\n".join(attachments)
            self._conversation.add_user(combined)
        else:
            self._conversation.add_user(user_message)

        # Summarize context if needed
        if self._context_mgr.should_summarize():
            system = self._build_system_prompt()
            summary = self._context_mgr.summarize(system)
            if summary and self._renderer:
                replaced = len(self._conversation) // 2
                self._renderer.print_context_summarized(replaced)

        return await self._agent_loop()

    async def run_once(self, prompt: str) -> str:
        """Run a single prompt and return the final response (for sub-agents)."""
        self._conversation.add_user(prompt)
        return await self._agent_loop()

    async def _agent_loop(self) -> str:
        """Inner loop: LLM → tools → LLM until no more tool calls."""
        settings = self._config.settings
        self._iteration_count = 0

        while self._iteration_count < settings.max_tool_iterations:
            self._iteration_count += 1
            system = self._build_system_prompt()
            tool_schemas = self._tool_registry.schemas()

            # Collect streaming response
            assistant_text = ""
            thinking_blocks: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            current_tool: dict[str, Any] | None = None
            current_json_parts: list[str] = []

            if self._renderer and not self._is_subagent:
                self._renderer.print_assistant_start()

            async for event in self._llm.stream_message(
                messages=self._conversation.messages,
                system=system,
                tools=tool_schemas,
                max_tokens=settings.max_tokens,
                thinking_budget=settings.thinking_budget,
            ):
                if isinstance(event, ThinkingDelta):
                    if self._renderer and not self._is_subagent:
                        self._renderer.stream_thinking(event.text)

                elif isinstance(event, ThinkingBlockComplete):
                    # Store completed thinking block to echo back on the next turn
                    thinking_blocks.append({"type": "thinking", "thinking": event.thinking})

                elif isinstance(event, TextDelta):
                    assistant_text += event.text
                    if self._renderer and not self._is_subagent:
                        self._renderer.stream_text(event.text)

                elif isinstance(event, ToolUseStart):
                    if current_tool:
                        # Finalize previous tool
                        await self._finalize_tool_call(current_tool, current_json_parts, tool_calls)
                    current_tool = {"id": event.tool_use_id, "name": event.tool_name}
                    current_json_parts = []

                elif isinstance(event, ToolInputDelta):
                    current_json_parts.append(event.partial_json)

                elif isinstance(event, UsageInfo):
                    self._context_mgr.update_usage(
                        event.input_tokens, event.output_tokens,
                        event.cache_read, event.cache_write,
                    )

                elif isinstance(event, MessageComplete):
                    if current_tool:
                        await self._finalize_tool_call(current_tool, current_json_parts, tool_calls)
                        current_tool = None
                        current_json_parts = []

                    # Build assistant content — thinking blocks must come first
                    content: list[dict[str, Any]] = list(thinking_blocks)
                    if assistant_text:
                        content.append({"type": "text", "text": assistant_text})
                    for tc in tool_calls:
                        content.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc["input"],
                        })
                    self._conversation.add_assistant(content if content else assistant_text)

            if self._renderer and not self._is_subagent:
                self._renderer.finish_streaming()

            await self._hook_mgr.fire(
                HookEvent.ASSISTANT_MESSAGE,
                {"message": assistant_text},
            )

            # If no tool calls, we're done
            if not tool_calls:
                if self._renderer and not self._is_subagent:
                    u = self._context_mgr._last_usage
                    if u:
                        self._renderer.print_usage(
                            u.get("input", 0), u.get("output", 0),
                            u.get("cache_read", 0), u.get("cache_write", 0),
                            session_cost=self._context_mgr.session_cost,
                        )
                    status = self._context_mgr.status_line()
                    self._renderer.print_context_status(status)
                return assistant_text

            # Execute all tool calls for this turn concurrently
            await self._execute_tools_parallel(tool_calls)

        # Max iterations reached — tell the model so it can wrap up cleanly
        limit = settings.max_tool_iterations
        stop_msg = (
            f"[max_tool_iterations={limit} reached — stopping tool loop. "
            "Summarise what was accomplished and what remains.]"
        )
        self._conversation.add_user(stop_msg)
        if self._renderer and not self._is_subagent:
            self._renderer.print_system(
                f"⚠ Max tool iterations ({limit}) reached. Asking agent to summarise."
            )
        return await self._agent_loop_single_turn()

    async def _agent_loop_single_turn(self) -> str:
        """One final LLM call with no tools — used after max_iterations to get a summary."""
        settings = self._config.settings
        system = self._build_system_prompt()
        assistant_text = ""
        if self._renderer and not self._is_subagent:
            self._renderer.print_assistant_start()
        try:
            async for event in self._llm.stream_message(
                messages=self._conversation.messages,
                system=system,
                tools=None,
                max_tokens=settings.max_tokens,
            ):
                if isinstance(event, TextDelta):
                    assistant_text += event.text
                    if self._renderer and not self._is_subagent:
                        self._renderer.stream_text(event.text)
                elif isinstance(event, UsageInfo):
                    self._context_mgr.update_usage(
                        event.input_tokens, event.output_tokens,
                        event.cache_read, event.cache_write,
                    )
        finally:
            if self._renderer and not self._is_subagent:
                self._renderer.finish_streaming()
        self._conversation.add_assistant(assistant_text)
        return assistant_text

    async def _execute_tools_parallel(self, tool_calls: list[dict[str, Any]]) -> None:
        """Print all tool-call headers, run them concurrently, then print results in order."""
        # Show all pending calls up-front so the user sees what's coming
        if self._renderer and not self._is_subagent:
            for tc in tool_calls:
                self._renderer.print_tool_call(tc["name"], tc["input"])

        async def _run(tc: dict[str, Any]) -> tuple[dict[str, Any], "ToolResult", float]:
            import time
            await self._hook_mgr.fire(
                HookEvent.PRE_TOOL_CALL,
                {"tool_name": tc["name"], "tool_input": tc["input"]},
            )
            t0 = time.monotonic()
            if self._renderer and not self._is_subagent:
                spinner = self._renderer.start_spinner(tc["name"])
            else:
                spinner = None
            try:
                result = await self._tool_registry.execute(tc["name"], tc["input"])
            finally:
                elapsed = time.monotonic() - t0
                if spinner is not None:
                    self._renderer.stop_spinner(spinner)
            hook_outputs = await self._hook_mgr.fire(
                HookEvent.POST_TOOL_CALL,
                {"tool_name": tc["name"], "tool_input": tc["input"], "tool_result": result.content},
            )
            return tc, result, elapsed, hook_outputs

        results = await asyncio.gather(*[_run(tc) for tc in tool_calls])

        for tc, result, elapsed, hook_outputs in results:
            if self._renderer and not self._is_subagent:
                self._renderer.print_tool_result(tc["name"], result.content, result.is_error, elapsed)
                # Special notification for memory operations
                if tc["name"] == "MemoryWrite" and not result.is_error:
                    self._renderer.print_memory_saved(
                        result.metadata.get("memory_name", tc["input"].get("name", "")),
                        result.metadata.get("memory_type", tc["input"].get("type", "")),
                    )
                elif tc["name"] == "MemoryDelete" and not result.is_error:
                    self._renderer.print_system(f"🗑  Memory deleted: {tc['input'].get('name', '')}")

            # Images returned by ReadTool are passed as vision content blocks
            if result.metadata.get("image") and not result.is_error:
                content_str = result.content  # "[image:image/png:<b64>]"
                media_type = result.metadata.get("media_type", "image/png")
                b64 = content_str.split(":", 2)[2].rstrip("]")
                self._conversation.add_tool_result(
                    tc["id"],
                    [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}],
                )
            else:
                # Append any PostToolCall hook outputs to the result so the model sees them
                combined = result.content
                if hook_outputs:
                    hook_text = "\n".join(hook_outputs)
                    combined = f"{combined}\n\n[hook output]\n{hook_text}" if combined else hook_text
                self._conversation.add_tool_result(tc["id"], combined, result.is_error)

    async def _finalize_tool_call(
        self,
        tool: dict[str, Any],
        json_parts: list[str],
        tool_calls: list[dict[str, Any]],
    ) -> None:
        raw_json = "".join(json_parts)
        try:
            tool_input = json.loads(raw_json) if raw_json.strip() else {}
        except json.JSONDecodeError:
            tool_input = {}
        tool["input"] = tool_input
        tool_calls.append(tool)

    # Patterns for auto-detection: absolute/relative/home paths and http(s) URLs.
    # Negative lookbehind/ahead for backtick prevents matching inside `code spans`.
    _FILE_RE = re.compile(
        r'(?<![`\w/])'                   # not preceded by backtick, word char, or slash
        r'((?:~|\.{1,2})?/[\w./\-]+)'   # path starting with ~/, ./, ../, or /
        r'(?![`\w/])'                    # not followed by backtick, word char, or slash
    )
    _URL_RE = re.compile(r'https?://[^\s<>"\'`]+')

    async def _collect_attachments(self, message: str) -> list[str]:
        """Pre-read file paths and fetch URLs mentioned in the user's message."""
        attachments: list[str] = []
        seen: set[str] = set()

        for m in self._FILE_RE.finditer(message):
            raw = m.group(1)
            path = Path(raw).expanduser()
            key = str(path)
            if key in seen or key in self._attached:
                continue
            seen.add(key)
            self._attached.add(key)
            if path.is_file():
                try:
                    text = await asyncio.to_thread(
                        path.read_text, encoding="utf-8", errors="replace"
                    )
                    if len(text) > 20_000:
                        text = text[:20_000] + "\n[...truncated at 20 000 chars]"
                    attachments.append(f"<file path=\"{raw}\">\n{text}\n</file>")
                    if self._renderer and not self._is_subagent:
                        self._renderer.print_system(f"Attached file: {raw}")
                except Exception:
                    pass

        for m in self._URL_RE.finditer(message):
            url = m.group(0)
            if url in seen or url in self._attached:
                continue
            seen.add(url)
            self._attached.add(url)
            try:
                import httpx
                async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    ct = resp.headers.get("content-type", "")
                    body = resp.text
                    if "html" in ct:
                        body = re.sub(r"<[^>]+>", " ", re.sub(
                            r"<(script|style|head)[^>]*>.*?</\1>", "", body,
                            flags=re.DOTALL | re.IGNORECASE,
                        ))
                        body = re.sub(r"\s{3,}", "\n\n", body).strip()
                    if len(body) > 20_000:
                        body = body[:20_000] + "\n[...truncated]"
                    attachments.append(f"<url href=\"{url}\">\n{body}\n</url>")
                    if self._renderer and not self._is_subagent:
                        self._renderer.print_system(f"Fetched URL: {url}")
            except Exception:
                pass  # silently skip — URL might be illustrative, not real

        return attachments

    async def _process_memory_tags(self, text: str) -> None:
        """Extract and save <memory_save> blocks from assistant output."""
        pattern = r"<memory_save>\s*```(?:json)?\s*(.*?)\s*```\s*</memory_save>"
        matches = re.findall(pattern, text, re.DOTALL)
        if not matches:
            # Try without code block
            pattern2 = r"<memory_save>\s*(\{.*?\})\s*</memory_save>"
            matches = re.findall(pattern2, text, re.DOTALL)

        for match in matches:
            try:
                data = json.loads(match)
                name = data.get("name", "")
                description = data.get("description", "")
                mem_type = MemoryType(data.get("type", "user"))
                body = data.get("body", "")
                if name and body:
                    self._memory.upsert(name, description, mem_type, body)
                    if self._renderer and not self._is_subagent:
                        self._renderer.print_memory_saved(name, mem_type.value)
            except Exception:
                pass

    async def _permission_prompt(
        self, tool_name: str, call_str: str, tool_input: dict[str, Any]
    ) -> str:
        """Interactive permission prompt via terminal."""
        if self._renderer:
            self._renderer.print_permission_prompt(tool_name, call_str, tool_input)

        import sys
        print("\nAllow this action?")
        print("  [y] Yes, once")
        print("  [a] Yes, allow all calls to this tool this session")
        print("  [n] No, deny")
        print("Choice [y/a/n]: ", end="", flush=True)

        try:
            choice = await self._read_line()
            choice = choice.strip().lower()
            if choice == "a":
                return "allow_session"
            elif choice == "y":
                return "allow_once"
            else:
                return "deny"
        except Exception:
            return "deny"

    async def _ask_user(self, question: str, options: list[str]) -> str:
        """Ask the user a question and return their answer."""
        print(f"\n{question}")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
        print("Your answer: ", end="", flush=True)
        return await self._read_line()

    @staticmethod
    async def _read_line() -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input)

    async def shutdown(self) -> None:
        await self._mcp_manager.disconnect_all()
        await self._hook_mgr.fire(HookEvent.AGENT_STOP, {})
        if self._kernel is not None:
            await self._kernel.stop()
        if self._sandbox is not None:
            await self._sandbox.stop()
        # Terminate the persistent bash shell if one was created
        bash = self._tool_registry.get("Bash")
        if bash is not None and hasattr(bash, "terminate"):
            await bash.terminate()
