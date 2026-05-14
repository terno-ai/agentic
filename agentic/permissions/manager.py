"""Permission system: allow/deny rules with interactive prompting."""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Callable, Awaitable

from agentic.core.config import PermissionsConfig


class PermissionManager:
    """Checks tool calls against allow/deny rules, prompts user when ambiguous."""

    def __init__(
        self,
        config: PermissionsConfig,
        prompt_fn: Callable[[str, str, dict[str, Any]], Awaitable[str]] | None = None,
    ):
        self._config = config
        self._prompt_fn = prompt_fn
        self._session_allows: set[str] = set()
        self._session_denies: set[str] = set()

    def _format_tool_call(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Format a tool call as a matchable string like 'Bash(git status)'."""
        if "command" in tool_input:
            return f"{tool_name}({tool_input['command']})"
        if "file_path" in tool_input:
            return f"{tool_name}({tool_input['file_path']})"
        return tool_name

    def _matches(self, pattern: str, call_str: str) -> bool:
        """Match a pattern like 'Bash(git *)', 'Read', 'Write(/etc/*)' against a call string."""
        # Normalize
        if "(" not in pattern:
            # Tool name only — match the tool regardless of args
            tool_part = call_str.split("(")[0]
            return fnmatch.fnmatch(tool_part, pattern)
        return fnmatch.fnmatch(call_str, pattern)

    async def check(self, tool_name: str, tool_input: dict[str, Any]) -> tuple[bool, str]:
        """Return (allowed, reason). Prompts user if no rule matches."""
        call_str = self._format_tool_call(tool_name, tool_input)

        # Session-level overrides
        if call_str in self._session_allows or tool_name in self._session_allows:
            return True, "session-allowed"
        if call_str in self._session_denies or tool_name in self._session_denies:
            return False, "session-denied"

        # Check deny rules first (deny takes precedence)
        for pattern in self._config.deny:
            if self._matches(pattern, call_str):
                return False, f"denied by rule: {pattern}"

        # Check allow rules
        for pattern in self._config.allow:
            if self._matches(pattern, call_str):
                return True, f"allowed by rule: {pattern}"

        # No rule matched — prompt user
        if self._prompt_fn:
            choice = await self._prompt_fn(tool_name, call_str, tool_input)
            if choice == "allow_session":
                self._session_allows.add(tool_name)
                return True, "user allowed (session)"
            elif choice == "allow_once":
                return True, "user allowed (once)"
            elif choice == "deny":
                return False, "user denied"
            else:
                return False, "user denied"

        # Default: allow (permissive mode when no prompting)
        return True, "default-allow"

    def add_allow(self, pattern: str) -> None:
        if pattern not in self._config.allow:
            self._config.allow.append(pattern)

    def add_deny(self, pattern: str) -> None:
        if pattern not in self._config.deny:
            self._config.deny.append(pattern)

    def reset_session(self) -> None:
        self._session_allows.clear()
        self._session_denies.clear()
