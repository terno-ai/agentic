"""Terminal output rendering with Rich."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
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
        self._thinking_started = False
        self._active_spinners: list[Live] = []

    def user_prompt(self) -> None:
        self.console.print()

    def print_user(self, text: str) -> None:
        self.console.print(f"[user]You:[/user] {text}")

    def print_assistant_start(self) -> None:
        self.console.print("\n[assistant]Assistant:[/assistant]", end=" ")

    def stream_thinking(self, delta: str) -> None:
        """Stream extended thinking content (shown dimmed)."""
        if not self._thinking_started:
            self._thinking_started = True
            print("\n\x1b[2m[thinking]\x1b[0m", end="", flush=True)
        print(f"\x1b[2m{delta}\x1b[0m", end="", flush=True)

    def stream_text(self, delta: str) -> None:
        """Stream text character by character."""
        if self._thinking_started:
            # Close the thinking block before response text
            print("\n\x1b[2m[/thinking]\x1b[0m\n", end="", flush=True)
            self._thinking_started = False
        self._streaming_buffer += delta
        print(delta, end="", flush=True)

    def finish_streaming(self) -> None:
        """Called when streaming is complete."""
        if self._thinking_started:
            print("\n\x1b[2m[/thinking]\x1b[0m", end="", flush=True)
            self._thinking_started = False
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
        summary = self._summarize_input(tool_name, tool_input)
        self.console.print(f"\n[tool.name]⚙ {tool_name}[/tool.name] [tool.input]{summary}[/tool.input]")

    def start_spinner(self, tool_name: str) -> Live:
        """Start an animated spinner shown while a tool is executing."""
        spin = Spinner("dots", text=f"[dim]{tool_name}…[/dim]", style="dim")
        live = Live(spin, console=self.console, refresh_per_second=12, transient=True)
        live.start()
        self._active_spinners.append(live)
        return live

    def stop_spinner(self, live: Live) -> None:
        try:
            live.stop()
        except Exception:
            pass
        if live in self._active_spinners:
            self._active_spinners.remove(live)

    def stop_all_spinners(self) -> None:
        """Stop every active spinner — must be called before interactive input prompts."""
        for live in list(self._active_spinners):
            try:
                live.stop()
            except Exception:
                pass
        self._active_spinners.clear()

    def print_tool_result(self, tool_name: str, result_text: str, is_error: bool = False, elapsed: float = 0.0) -> None:
        time_str = f" [dim]{elapsed:.1f}s[/dim]" if elapsed >= 0.5 else ""
        if is_error:
            preview = result_text[:300] + ("..." if len(result_text) > 300 else "")
            self.console.print(f"  [tool.error]✗ {preview}[/tool.error]{time_str}")
        elif tool_name == "Edit" and "\n@@" in result_text:
            # Split summary line from diff body
            lines = result_text.strip().splitlines()
            summary = next((l for l in lines if not l.startswith(("---", "+++", "@@", "-", "+", " "))), lines[0])
            diff_body = "\n".join(l for l in lines if l.startswith(("---", "+++", "@@", "-", "+", " ")))
            self.console.print(f"  [tool.result]✓ {summary}[/tool.result]{time_str}")
            if diff_body:
                self.console.print(Syntax(diff_body, "diff", theme="monokai", background_color="default"))
        else:
            lines = result_text.strip().splitlines()
            if len(lines) <= 3:
                preview = result_text.strip()
            else:
                preview = "\n".join(lines[:3]) + f"\n  ... ({len(lines)} lines total)"
            self.console.print(f"  [tool.result]✓ {preview}[/tool.result]{time_str}")

    def print_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
        session_cost: float = 0.0,
    ) -> None:
        parts = [f"in={input_tokens:,}", f"out={output_tokens:,}"]
        if cache_read:
            parts.append(f"cache_hit={cache_read:,}")
        if cache_write:
            parts.append(f"cache_write={cache_write:,}")
        cost_str = f"  [dim]~${session_cost:.4f} session[/dim]" if session_cost > 0 else ""
        self.console.print(f"[context]  tokens · {' · '.join(parts)}{cost_str}[/context]")

    def print_system(self, text: str) -> None:
        self.console.print(f"[system]{text}[/system]")

    def print_markdown(self, text: str) -> None:
        try:
            self.console.print(Markdown(text))
        except Exception:
            self.console.print(text)

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
- `/memory search <query>` — search memories (relevance-ranked)
- `/memory delete <name>` — delete a memory
- `/memory stale` — list project memories not updated in 30+ days
- `/history` — show condensed conversation transcript
- `/config` — show current settings
- `/model <name>` — switch model (e.g. gpt-4o, claude-opus-4-7)
- `/provider <anthropic|openai>` — switch provider
- `/plan` — toggle plan mode (read-only)
- `/btw <note>` — save a note to memory; prefix `[type]` to set type, e.g. `/btw [project] uses postgres`
- `/think [N|off]` — enable extended thinking with budget N tokens (Claude 3.7+ only)
- `/compact` — manually compress conversation context
- `/clear` — clear conversation history
- `/exit` or `Ctrl+D` — exit

**Multi-line input:**
- `Esc+Enter` (or `Alt+Enter`) — insert a newline without submitting
- `Enter` — submit the message

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
        elif tool_name in ("Read", "Write", "Edit", "MultiEdit"):
            path = tool_input.get("file_path", "")
            if tool_name == "MultiEdit":
                n = len(tool_input.get("edits", []))
                return f"{path} ({n} edits)"
            return path
        elif tool_name == "WebFetch":
            return tool_input.get("url", "")
        elif tool_name == "WebSearch":
            return tool_input.get("query", "")
        elif tool_name == "Agent":
            return tool_input.get("description", "")
        elif tool_name == "MemoryWrite":
            name = tool_input.get("name", "")
            mtype = tool_input.get("type", "")
            return f"{name} ({mtype})"
        else:
            parts = [f"{k}={repr(v)[:30]}" for k, v in list(tool_input.items())[:2]]
            return ", ".join(parts)
