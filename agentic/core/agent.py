"""Core agent loop — orchestrates LLM, tools, memory, skills, and context management."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentic.core.config import ConfigManager
from agentic.core.context import ContextManager
from agentic.core.conversation import ConversationHistory
from agentic.core.llm import create_llm_client, TextDelta, ToolUseStart, ToolInputDelta, MessageComplete, UsageInfo
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
- User asks a **question or wants an explanation** → answer in text; markdown code blocks are fine.

When it is ambiguous whether the user wants files created or just wants to see code,
prefer creating the files. The user can always delete them; but if they asked for a game
and got a markdown snippet, they got nothing useful.

## Core principles
- Be direct and concise. Prefer action over lengthy explanation.
- Use tools proactively. Read files before editing them.
- Write correct, secure, idiomatic code. No placeholders or half-implementations.
- Default to no comments unless the WHY is non-obvious.
- Never add features beyond what's asked. Don't anticipate hypothetical needs.
- Trust framework guarantees; only validate at system boundaries.

## Memory system
You have access to persistent memory. When you learn something important about:
- The user's role, preferences, or expertise → save as 'user' memory
- Behavioral guidance (corrections, confirmed approaches) → save as 'feedback' memory
- Ongoing work, goals, or deadlines → save as 'project' memory
- External resources or references → save as 'reference' memory

To save a memory, output a JSON block tagged with <memory_save>:
```
<memory_save>
{"name": "SLUG", "description": "ONE-LINE SUMMARY", "type": "user|feedback|project|reference", "body": "CONTENT"}
</memory_save>
```

## Working directory
<<CWD>>

## Loaded context
<<AGENT_MD>>

## Memory index
<<MEMORY_INDEX>>
"""

AUTO_MEMORY_HINT = """
When the conversation reveals something worth remembering for future sessions, save it using
the <memory_save> tag. Save feedback memories when corrected or when an approach is confirmed.
"""


class AgentLoop:
    """Main agent loop. One instance per session (or sub-agent call)."""

    def __init__(
        self,
        config: ConfigManager,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        is_subagent: bool = False,
        renderer: Any | None = None,
    ):
        self._config = config
        self._is_subagent = is_subagent
        self._renderer = renderer

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
        self._permission_mgr = PermissionManager(
            config=settings.permissions,
            prompt_fn=self._permission_prompt if not is_subagent else None,
        )
        self._hook_mgr = HookManager(
            {k: v for k, v in settings.hooks.items()}
        )
        self._mcp_manager = MCPServerManager()
        self._tool_registry = ToolRegistry(permission_manager=self._permission_mgr)
        self._allowed_tools = allowed_tools
        self._iteration_count = 0

        self._setup_tools()

    def _setup_tools(self) -> None:
        from agentic.tools.bash import BashTool
        from agentic.tools.file_tools import ReadTool, WriteTool, EditTool
        from agentic.tools.web_tools import WebFetchTool, WebSearchTool
        from agentic.tools.task_tools import (
            TaskCreateTool, TaskGetTool, TaskListTool,
            TaskUpdateTool, TaskStopTool, TaskOutputTool,
        )
        from agentic.tools.agent_tool import AgentTool
        from agentic.tools.notification import AskUserQuestionTool, PushNotificationTool

        all_tools = [
            BashTool(cwd=Path.cwd()),
            ReadTool(),
            WriteTool(),
            EditTool(),
            WebFetchTool(),
            WebSearchTool(),
            TaskCreateTool(),
            TaskGetTool(),
            TaskListTool(),
            TaskUpdateTool(),
            TaskStopTool(),
            TaskOutputTool(),
            AgentTool(config_manager=self._config),
            AskUserQuestionTool(ask_fn=self._ask_user if not self._is_subagent else None),
            PushNotificationTool(),
        ]

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
        cwd = str(Path.cwd())

        agent_md_path = Path.cwd() / "AGENT.md"
        agent_md = (
            agent_md_path.read_text(encoding="utf-8")
            if agent_md_path.exists()
            else "(No AGENT.md found. Run /init to create one.)"
        )

        memory_index = self._memory.load_index()

        system_text = (
            SYSTEM_PROMPT_BASE
            .replace("<<CWD>>", cwd)
            .replace("<<AGENT_MD>>", agent_md)
            .replace("<<MEMORY_INDEX>>", memory_index or "(no memories yet)")
        )

        settings = self._config.settings
        if settings.auto_memory:
            system_text += AUTO_MEMORY_HINT

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
        self._conversation.add_user(user_message)
        if self._renderer:
            self._renderer.print_user(user_message)

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
            ):
                if isinstance(event, TextDelta):
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

                    # Build assistant content for the message
                    content: list[dict[str, Any]] = []
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

            # Process auto-memory tags in assistant text
            if assistant_text:
                await self._process_memory_tags(assistant_text)

            await self._hook_mgr.fire(
                HookEvent.ASSISTANT_MESSAGE,
                {"message": assistant_text},
            )

            # If no tool calls, we're done
            if not tool_calls:
                if self._renderer and not self._is_subagent:
                    status = self._context_mgr.status_line()
                    self._renderer.print_context_status(status)
                return assistant_text

            # Execute tool calls and continue loop
            for tc in tool_calls:
                await self._execute_tool(tc)

        return self._conversation.last_assistant_text()

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

    async def _execute_tool(self, tc: dict[str, Any]) -> None:
        tool_name = tc["name"]
        tool_input = tc["input"]
        tool_use_id = tc["id"]

        if self._renderer and not self._is_subagent:
            self._renderer.print_tool_call(tool_name, tool_input)

        await self._hook_mgr.fire(
            HookEvent.PRE_TOOL_CALL,
            {"tool_name": tool_name, "tool_input": tool_input},
        )

        result: ToolResult = await self._tool_registry.execute(tool_name, tool_input)

        if self._renderer and not self._is_subagent:
            self._renderer.print_tool_result(tool_name, result.content, result.is_error)

        await self._hook_mgr.fire(
            HookEvent.POST_TOOL_CALL,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_result": result.content,
            },
        )

        self._conversation.add_tool_result(tool_use_id, result.content, result.is_error)

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
