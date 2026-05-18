"""Terminal output rendering with Rich."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme


DARK_THEME = Theme({
    "tool.name": "bold cyan",
    "tool.input": "dim white",
    "tool.result": "dim green",
    "tool.error": "bold red",
    "user": "bold blue",
    "assistant": "white",
    "system": "dim yellow",
    "memory": "dim magenta",
    "skill": "bold yellow",
    "context": "dim cyan",
})


class Renderer:
    def __init__(self, theme: str = "dark"):
        self.console = Console(
            theme=DARK_THEME,
            highlight=True,
            markup=True,
        )
        self._streaming_buffer = ""

    def user_prompt(self) -> None:
        self.console.print()

    def print_user(self, text: str) -> None:
        self.console.print(f"[user]You:[/user] {text}")

    def print_assistant_start(self) -> None:
        self.console.print("\n[assistant]Assistant:[/assistant]", end=" ")

    def stream_text(self, delta: str) -> None:
        """Stream text character by character."""
        self._streaming_buffer += delta
        print(delta, end="", flush=True)

    def finish_streaming(self) -> None:
        """Called when streaming is complete."""
        if self._streaming_buffer:
            print()  # newline after streamed content
        self._streaming_buffer = ""

    def print_assistant(self, text: str) -> None:
        self.console.print()
        try:
            self.console.print(Markdown(text))
        except Exception:
            self.console.print(text)

    def print_tool_call(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        # Show compact tool call
        summary = self._summarize_input(tool_name, tool_input)
        self.console.print(f"\n[tool.name]⚙ {tool_name}[/tool.name] [tool.input]{summary}[/tool.input]")

    def print_tool_result(self, tool_name: str, result_text: str, is_error: bool = False) -> None:
        if is_error:
            preview = result_text[:200] + ("..." if len(result_text) > 200 else "")
            self.console.print(f"  [tool.error]✗ {preview}[/tool.error]")
        else:
            # Show first line or brief summary
            lines = result_text.strip().splitlines()
            if len(lines) <= 3:
                preview = result_text.strip()
            else:
                preview = "\n".join(lines[:3]) + f"\n  ... ({len(lines)} lines total)"
            self.console.print(f"  [tool.result]✓ {preview}[/tool.result]")

    def print_system(self, text: str) -> None:
        self.console.print(f"[system]{text}[/system]")

    def print_memory_saved(self, name: str, memory_type: str) -> None:
        self.console.print(f"[memory]💾 Memory saved: {name} ({memory_type})[/memory]")

    def print_skill(self, skill_name: str) -> None:
        self.console.print(f"[skill]⚡ Running skill: /{skill_name}[/skill]")

    def print_context_status(self, status: str) -> None:
        self.console.print(f"[context]{status}[/context]")

    def print_context_summarized(self, count: int) -> None:
        self.console.print(f"[context]📝 Context summarized ({count} messages compressed)[/context]")

    def print_error(self, text: str) -> None:
        self.console.print(f"[bold red]Error:[/bold red] {text}")

    def print_permission_prompt(
        self, tool_name: str, call_str: str, tool_input: dict[str, Any]
    ) -> None:
        self.console.print(
            Panel(
                f"Tool: [tool.name]{tool_name}[/tool.name]\n"
                f"Call: [tool.input]{call_str}[/tool.input]",
                title="[yellow]Permission Required[/yellow]",
                border_style="yellow",
            )
        )

    def print_separator(self) -> None:
        self.console.print("─" * 60, style="dim")

    def print_welcome(self, model: str, version: str, provider: str = "anthropic",
                      sandbox: bool = False) -> None:
        sandbox_line = "  Sandbox: [green]ON[/green] (Docker)" if sandbox else ""
        self.console.print(
            Panel(
                f"[bold]Agentic[/bold] v{version} — autonomous coding agent\n"
                f"Provider: [cyan]{provider}[/cyan]  Model: [cyan]{model}[/cyan]{sandbox_line}\n"
                f"Type [yellow]/help[/yellow] for commands, [yellow]/skills[/yellow] to list skills\n"
                f"[yellow]/provider openai[/yellow] or [yellow]/provider anthropic[/yellow] to switch",
                border_style="cyan",
            )
        )

    def print_help(self) -> None:
        help_text = """
**Built-in commands:**
- `/help` — show this help
- `/skills` — list available skills
- `/memory` — show memory index
- `/memory search <query>` — search memories
- `/config` — show current settings
- `/model <name>` — switch model (e.g. gpt-4o, claude-opus-4-7)
- `/provider <anthropic|openai>` — switch provider
- `/plan` — toggle plan mode (read-only)
- `/btw <note>` — save a note to memory instantly (no LLM call)
- `/clear` — clear conversation history
- `/exit` or `Ctrl+D` — exit

**Skill commands:**
- `/init` — initialize AGENT.md
- `/review [PR#]` — review pull request
- `/simplify [file]` — simplify code
- `/security-review` — security audit
- `/test <file>` — write tests

**Shell shortcut:**
- `/! <command>` — run shell command
- `!<command>` — also works

**Key bindings:**
- `Ctrl+C` — interrupt current operation
- `Ctrl+D` — exit
- `↑/↓` — history navigation
- `Tab` — autocomplete
"""
        self.console.print(Markdown(help_text))

    @staticmethod
    def _summarize_input(tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            return cmd[:80] + ("..." if len(cmd) > 80 else "")
        elif tool_name in ("Read", "Write", "Edit"):
            path = tool_input.get("file_path", "")
            return path
        elif tool_name == "WebFetch":
            return tool_input.get("url", "")
        elif tool_name == "WebSearch":
            return tool_input.get("query", "")
        elif tool_name == "Agent":
            return tool_input.get("description", "")
        else:
            parts = [f"{k}={repr(v)[:30]}" for k, v in list(tool_input.items())[:2]]
            return ", ".join(parts)
