"""Skill execution — injects skill prompt into the agent loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic.skills.manager import SkillDefinition


class SkillRunner:
    """Runs a skill by formatting its prompt and returning it for the agent loop."""

    @staticmethod
    def build_prompt(skill: "SkillDefinition", args: str = "") -> str:
        """Format the skill prompt with the given args."""
        return skill.format_prompt(args)

    @staticmethod
    def parse_slash_command(text: str) -> tuple[str, str] | None:
        """Parse '/skill-name args...' from user input. Returns (skill_name, args) or None."""
        text = text.strip()
        if not text.startswith("/"):
            return None
        # /! is a raw bash shortcut, not a skill
        if text.startswith("/!"):
            return None
        parts = text[1:].split(None, 1)
        name = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        return name, args
