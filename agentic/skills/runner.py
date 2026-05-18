"""Skill execution — injects skill prompt into the agent loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic.skills.manager import SkillDefinition


class SkillRunner:
    """Runs a skill by formatting its prompt and returning it for the agent loop."""

    @staticmethod
    def build_prompt(skill: "SkillDefinition", args: str = "") -> str:
        """Format the skill prompt with the given args.

        Returns a (prompt, warning) tuple via the warning embedded in the prompt
        when args are empty but the skill template expects them.
        """
        uses_args = "{{args}}" in skill.prompt or "{{ args }}" in skill.prompt
        if uses_args and not args.strip():
            hint = f"\n\n(Note: this skill expects arguments — {skill.args_description or 'see /skills'})" \
                   if skill.args_description else ""
            return skill.format_prompt(args) + hint
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
