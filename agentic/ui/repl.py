"""Interactive REPL using prompt_toolkit."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from agentic.ui.completions import AgentCompleter

if TYPE_CHECKING:
    from agentic.core.agent import AgentLoop
    from agentic.ui.renderer import Renderer


PROMPT_STYLE = Style.from_dict({
    "prompt": "ansicyan bold",
    "model-info": "ansigreen",
})


def _get_prompt_tokens(model: str, plan_mode: bool) -> FormattedText:
    mode = " [PLAN]" if plan_mode else ""
    return FormattedText([
        ("class:prompt", f"❯{mode} "),
    ])


class REPL:
    def __init__(
        self,
        agent: "AgentLoop",
        renderer: "Renderer",
        history_file: Path,
    ):
        self._agent = agent
        self._renderer = renderer
        self._history_file = history_file
        self._completer = AgentCompleter()
        self._session: PromptSession | None = None
        self._agent_task: asyncio.Task | None = None  # current running agent turn

    def setup(self) -> None:
        history_file = self._history_file
        history_file.parent.mkdir(parents=True, exist_ok=True)

        bindings = KeyBindings()

        @bindings.add("c-c")
        def handle_ctrl_c(event):
            # Cancel a running agent turn; otherwise just clear the input line
            if self._agent_task and not self._agent_task.done():
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(self._agent_task.cancel)
            else:
                event.app.current_buffer.reset()

        @bindings.add("enter")
        def submit_on_enter(event):
            """Enter submits; Esc+Enter (bound below) inserts a newline."""
            buf = event.app.current_buffer
            if buf.text.strip():
                buf.validate_and_handle()
            else:
                buf.insert_text("\n")

        @bindings.add("escape", "enter", eager=True)
        def insert_newline(event):
            """Esc+Enter (Alt+Enter on most terminals) inserts a literal newline."""
            event.app.current_buffer.insert_text("\n")

        self._session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=self._completer,
            key_bindings=bindings,
            style=PROMPT_STYLE,
            enable_history_search=True,
            complete_while_typing=False,
            multiline=True,
            prompt_continuation="... ",
        )

        # Update completions with current skills
        if hasattr(self._agent, "_skill_manager"):
            self._completer.update_skills(self._agent._skill_manager.names())

    async def run(self) -> None:
        self.setup()
        settings = self._agent._config.settings
        from agentic.core.config import detect_provider
        provider = settings.provider or detect_provider(settings.model)
        sandbox_on = self._agent._sandbox is not None
        self._renderer.print_welcome(settings.model, "0.1.0", provider=provider, sandbox=sandbox_on)

        while True:
            try:
                model = self._agent._config.settings.model
                plan_mode = self._agent._config.settings.plan_mode

                user_input = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._session.prompt(
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

    async def _handle_input(self, text: str) -> None:
        # Built-in commands
        if text in ("/exit", "/quit", "exit", "quit"):
            raise SystemExit(0)

        if text == "/help":
            self._renderer.print_help()
            return

        if text == "/clear":
            self._agent._conversation.replace_with_summary("(conversation cleared)", 0)
            self._renderer.print_system("Conversation cleared.")
            return

        if text == "/memory":
            index = self._agent._memory.load_index()
            self._renderer.print_system(index or "No memories yet.")
            return

        if text.startswith("/memory search "):
            query = text.removeprefix("/memory search ").strip()
            results = self._agent._memory.search(query)
            if results:
                for r in results:
                    self._renderer.print_system(f"[{r.memory_type.value}] {r.name}: {r.description}")
            else:
                self._renderer.print_system("No matching memories.")
            return

        if text == "/skills":
            skills = self._agent._skill_manager.list_all()
            if skills:
                lines = [f"/{s.name:20} — {s.description}" for s in skills]
                self._renderer.print_system("\n".join(lines))
            else:
                self._renderer.print_system("No skills found.")
            return

        if text == "/config":
            import json
            s = self._agent._config.settings
            self._renderer.print_system(s.model_dump_json(indent=2))
            return

        if text.startswith("/model "):
            model = text.removeprefix("/model ").strip()
            self._agent._config.save_global(model=model)
            self._agent._llm.model = model
            self._renderer.print_system(f"Model set to: {model}")
            return

        if text.startswith("/provider "):
            provider = text.removeprefix("/provider ").strip()
            if provider not in ("anthropic", "openai"):
                self._renderer.print_error("Provider must be 'anthropic' or 'openai'")
                return
            self._agent._config.save_global(provider=provider)
            # Rebuild LLM client with new provider
            from agentic.core.llm import create_llm_client
            settings = self._agent._config.settings
            self._agent._llm = create_llm_client(
                provider=provider,
                model=settings.model,
                api_key=settings.api_key,
                openai_api_key=settings.openai_api_key,
            )
            self._renderer.print_system(f"Provider set to: {provider} (model: {settings.model})")
            return

        if text == "/plan":
            current = self._agent._config.settings.plan_mode
            self._agent._config.save_project(plan_mode=not current)
            self._renderer.print_system(f"Plan mode: {'ON' if not current else 'OFF'}")
            return

        if text.startswith("/think"):
            arg = text.removeprefix("/think").strip()
            if arg == "off" or arg == "0":
                self._agent._config.save_project(thinking_budget=0)
                self._renderer.print_system("Extended thinking: OFF")
            else:
                budget = int(arg) if arg.isdigit() else 8000
                self._agent._config.save_project(thinking_budget=budget)
                self._renderer.print_system(
                    f"Extended thinking: ON (budget={budget:,} tokens). "
                    "Only effective with Claude 3.7+ models."
                )
            return

        if text.startswith("/btw "):
            note = text.removeprefix("/btw ").strip()
            if not note:
                self._renderer.print_system("Usage: /btw <note>")
                return
            from agentic.memory.types import MemoryType
            import re, datetime
            slug = "btw_" + re.sub(r"[^a-z0-9]", "_", note[:40].lower()).strip("_")
            self._agent._memory.create(
                name=slug,
                description=note[:120],
                memory_type=MemoryType.USER,
                body=f"{note}\n\n*(saved via /btw on {datetime.date.today()})*",
            )
            self._renderer.print_system(f"Noted: {note}")
            return

        # /! shell shortcut
        if text.startswith("/! ") or text.startswith("!"):
            cmd = text.removeprefix("/! ").removeprefix("!")
            from agentic.tools.bash import BashTool
            tool = BashTool()
            result = await tool.execute(command=cmd)
            self._renderer.print_system(result.content)
            return

        # Run through agent loop — wrapped in a task so Ctrl+C can cancel it cleanly
        self._agent_task = asyncio.create_task(self._agent.run_turn(text))
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
