"""Interactive REPL using prompt_toolkit."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from agentic.ui.completions import AgentCompleter

if TYPE_CHECKING:
    from agentic.sdk.agent import Session
    from agentic.ui.renderer import Renderer


PROMPT_STYLE = Style.from_dict({
    "prompt": "ansicyan bold",
    "model-info": "ansigreen",
})


def _get_prompt_tokens(model: str, plan_mode: bool) -> FormattedText:
    short = model.split("/")[-1]
    short = (short
             .replace("claude-", "")
             .replace("sonnet-", "s")
             .replace("opus-", "o")
             .replace("haiku-", "h"))
    mode = " [PLAN]" if plan_mode else ""
    return FormattedText([
        ("class:model-info", f"({short})"),
        ("class:prompt", f"❯{mode} "),
    ])


class REPL:
    def __init__(
        self,
        session: "Session",
        renderer: "Renderer",
        history_file: Path,
    ):
        self._session = session
        self._renderer = renderer
        self._history_file = history_file
        self._completer = AgentCompleter()
        self._prompt_session: PromptSession | None = None
        self._agent_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Convenience: direct access to the underlying AgentLoop internals
    # ------------------------------------------------------------------

    @property
    def _a(self):  # noqa: ANN202
        """The underlying AgentLoop (for slash commands that need internals)."""
        return self._session._inner

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self) -> None:
        history_file = self._history_file
        history_file.parent.mkdir(parents=True, exist_ok=True)

        bindings = KeyBindings()

        @bindings.add("c-c")
        def handle_ctrl_c(event):
            if self._agent_task and not self._agent_task.done():
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(self._agent_task.cancel)
            else:
                event.app.current_buffer.reset()

        @bindings.add("enter")
        def submit_on_enter(event):
            buf = event.app.current_buffer
            if buf.text.strip():
                buf.validate_and_handle()
            else:
                buf.insert_text("\n")

        @bindings.add("escape", "enter", eager=True)
        def insert_newline(event):
            event.app.current_buffer.insert_text("\n")

        self._prompt_session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=self._completer,
            key_bindings=bindings,
            style=PROMPT_STYLE,
            enable_history_search=True,
            complete_while_typing=False,
            multiline=True,
            prompt_continuation="... ",
        )

        inner = self._a
        if inner and hasattr(inner, "_skill_manager"):
            self._completer.update_skills(inner._skill_manager.names())

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self.setup()
        inner = self._a
        settings = inner._config.settings
        from agentic.core.config import detect_provider
        provider = settings.provider or detect_provider(settings.model)
        sandbox_on = inner._sandbox is not None
        self._renderer.print_welcome(settings.model, "0.2.0", provider=provider, sandbox=sandbox_on)

        while True:
            try:
                model = self._a._config.settings.model
                plan_mode = self._a._config.settings.plan_mode

                user_input = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._prompt_session.prompt(
                        lambda: _get_prompt_tokens(model, plan_mode),
                        style=PROMPT_STYLE,
                    )
                )
            except EOFError:
                self._renderer.print_system("Goodbye!")
                break
            except KeyboardInterrupt:
                self._renderer.print_system("(Ctrl+C — use Ctrl+D to exit)")
                continue
            except Exception as e:
                self._renderer.print_error(f"Input error: {e}")
                continue

            if not user_input.strip():
                continue

            await self._handle_input(user_input.strip())

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _handle_input(self, text: str) -> None:
        inner = self._a

        if text in ("/exit", "/quit", "exit", "quit"):
            raise SystemExit(0)

        if text == "/help":
            self._renderer.print_help()
            return

        if text == "/clear":
            inner._conversation.replace_with_summary("(conversation cleared)", 0)
            self._renderer.print_system("Conversation cleared.")
            return

        if text == "/compact":
            system = inner._build_system_prompt()
            summary = inner._context_mgr.summarize(system)
            if summary:
                self._renderer.print_system(
                    f"Context compacted ({inner._context_mgr.summarization_count} total compressions)."
                )
            else:
                self._renderer.print_system("Nothing to compact (conversation is short).")
            return

        if text == "/memory":
            index = inner._memory.load_index()
            if index:
                self._renderer.print_markdown(index)
            else:
                self._renderer.print_system("No memories yet.")
            return

        if text == "/history":
            msgs = inner._conversation.messages
            if not msgs:
                self._renderer.print_system("No conversation history.")
            else:
                lines = []
                for m in msgs:
                    role = m["role"].upper()
                    content = m.get("content", "")
                    if isinstance(content, list):
                        text_parts = [
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        content = " ".join(text_parts)
                    preview = str(content)[:120].replace("\n", " ")
                    lines.append(f"[dim]{role}:[/dim] {preview}")
                self._renderer.console.print("\n".join(lines))
            return

        if text.startswith("/memory search "):
            query = text.removeprefix("/memory search ").strip()
            results = inner._memory.search(query)
            if results:
                for r in results:
                    age = f" ({r.age_days():.0f}d ago)" if r.age_days() > 1 else ""
                    self._renderer.print_system(
                        f"[{r.memory_type.value}] {r.name}{age}: {r.description}"
                    )
            else:
                self._renderer.print_system("No matching memories.")
            return

        if text.startswith("/memory delete "):
            name = text.removeprefix("/memory delete ").strip()
            if inner._memory.delete(name):
                self._renderer.print_system(f"Deleted memory: {name}")
            else:
                self._renderer.print_system(f"Memory not found: {name}")
            return

        if text.startswith("/memory stale"):
            stale = inner._memory.stale_project_memories(threshold_days=30)
            if stale:
                lines = ["Stale project memories (not updated in 30+ days):"]
                for r in stale:
                    lines.append(f"  {r.name} ({r.age_days():.0f}d) — {r.description}")
                self._renderer.print_system("\n".join(lines))
            else:
                self._renderer.print_system("No stale project memories.")
            return

        if text == "/skills":
            skills = inner._skill_manager.list_all()
            if skills:
                lines = []
                for s in skills:
                    args = f"  args: {s.args_description}" if s.args_description else ""
                    lines.append(f"/{s.name:20} — {s.description}{args}")
                self._renderer.print_system("\n".join(lines))
            else:
                self._renderer.print_system("No skills found.")
            return

        if text == "/config":
            self._renderer.print_system(inner._config.settings.model_dump_json(indent=2))
            return

        if text.startswith("/model "):
            model = text.removeprefix("/model ").strip()
            from agentic.core.config import detect_provider
            from agentic.core.llm import create_llm_client
            new_provider = detect_provider(model)
            inner._config.save_global(model=model, provider=new_provider)
            settings = inner._config.settings
            inner._llm = create_llm_client(
                provider=new_provider,
                model=model,
                api_key=settings.api_key,
                openai_api_key=settings.openai_api_key,
            )
            self._renderer.print_system(f"Model: {model}  Provider: {new_provider}")
            return

        if text.startswith("/provider "):
            provider = text.removeprefix("/provider ").strip()
            if provider not in ("anthropic", "openai"):
                self._renderer.print_error("Provider must be 'anthropic' or 'openai'")
                return
            inner._config.save_global(provider=provider)
            from agentic.core.llm import create_llm_client
            settings = inner._config.settings
            inner._llm = create_llm_client(
                provider=provider,
                model=settings.model,
                api_key=settings.api_key,
                openai_api_key=settings.openai_api_key,
            )
            self._renderer.print_system(f"Provider set to: {provider} (model: {settings.model})")
            return

        if text == "/plan":
            current = inner._config.settings.plan_mode
            inner._config.save_project(plan_mode=not current)
            self._renderer.print_system(f"Plan mode: {'ON' if not current else 'OFF'}")
            return

        if text.startswith("/think"):
            arg = text.removeprefix("/think").strip()
            if arg in ("off", "0"):
                inner._config.save_project(thinking_budget=0)
                self._renderer.print_system("Extended thinking: OFF")
            else:
                budget = int(arg) if arg.isdigit() else 8000
                inner._config.save_project(thinking_budget=budget)
                self._renderer.print_system(
                    f"Extended thinking: ON (budget={budget:,} tokens). "
                    "Only effective with Claude 3.7+ models."
                )
            return

        if text.startswith("/btw "):
            note = text.removeprefix("/btw ").strip()
            if not note:
                self._renderer.print_system(
                    "Usage: /btw <note>\n"
                    "       /btw [feedback] always use ruff before committing\n"
                    "       /btw [project] entry point is main.py\n"
                    "       /btw [reference] docs at https://...\n"
                    "Type prefix is optional — defaults to 'user'."
                )
                return
            import re
            import datetime
            from agentic.memory.types import MemoryType

            mem_type = MemoryType.USER
            type_match = re.match(r"^\[(\w+)\]\s*", note)
            if type_match:
                type_str = type_match.group(1).lower()
                try:
                    mem_type = MemoryType(type_str)
                    note = note[type_match.end():]
                except ValueError:
                    pass

            if not note:
                self._renderer.print_system("Note text is empty after type prefix.")
                return

            slug = "btw_" + re.sub(r"[^a-z0-9]", "_", note[:40].lower()).strip("_")
            inner._memory.upsert(
                name=slug,
                description=note[:120],
                memory_type=mem_type,
                body=f"{note}\n\n*(saved via /btw on {datetime.date.today()})*",
            )
            self._renderer.print_memory_saved(slug, mem_type.value)
            return

        if text.startswith("/! ") or text.startswith("!"):
            cmd = text.removeprefix("/! ").removeprefix("!")
            from agentic.tools.bash import BashTool
            tool = BashTool()
            result = await tool.execute(command=cmd)
            self._renderer.print_system(result.content)
            return

        # Agent turn — wrapped in a task so Ctrl+C can cancel it cleanly
        self._agent_task = asyncio.create_task(self._run_agent_turn(text))
        try:
            await self._agent_task
        except asyncio.CancelledError:
            self._renderer.print_system("\n(Interrupted)")
        except KeyboardInterrupt:
            self._renderer.print_system("\n(Interrupted)")
        except Exception as e:
            self._renderer.print_error(str(e))
        finally:
            self._agent_task = None

    # ------------------------------------------------------------------
    # Agent turn — rendered directly via the Rich renderer
    # ------------------------------------------------------------------

    async def _run_agent_turn(self, text: str) -> None:
        """Run one user turn, rendering directly through the Rich renderer.

        The REPL uses the AgentLoop's Rich renderer directly (same as before
        the SDK refactor). The SDK event-streaming path (session.stream) is
        for external consumers (web apps, scripts) — not the interactive REPL,
        which needs spinners, diffs, and Markdown rendered in the terminal.
        """
        await self._session._inner.run_turn(text)
