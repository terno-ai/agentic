"""Autocompletion for the REPL."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.completion import Completer, Completion

if TYPE_CHECKING:
    from prompt_toolkit.document import Document


BUILTIN_COMMANDS = [
    "/help", "/skills", "/memory", "/history", "/config", "/model",
    "/plan", "/think", "/compact", "/clear", "/exit", "/!", "/btw",
]


class AgentCompleter(Completer):
    def __init__(self, skill_names: list[str] | None = None):
        self._skill_names = skill_names or []
        self._commands = BUILTIN_COMMANDS + [f"/{s}" for s in self._skill_names]

    def update_skills(self, skill_names: list[str]) -> None:
        self._skill_names = skill_names
        self._commands = BUILTIN_COMMANDS + [f"/{s}" for s in self._skill_names]

    def get_completions(self, document: "Document", complete_event: Any) -> list[Completion]:
        text = document.text_before_cursor
        completions = []

        if text.startswith("/"):
            word = text.split()[0] if text.split() else text
            for cmd in self._commands:
                if cmd.startswith(word):
                    completions.append(
                        Completion(cmd[len(word):], start_position=0, display=cmd)
                    )

        return completions
