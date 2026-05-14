"""Hook manager — fire shell commands on agent events."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
from typing import Any

from agentic.core.config import HookConfig
from agentic.hooks.events import HookEvent


class HookManager:
    def __init__(self, hooks_config: dict[str, list[HookConfig]]):
        self._hooks = hooks_config

    async def fire(
        self,
        event: HookEvent,
        context: dict[str, Any] | None = None,
    ) -> list[str]:
        """Fire all hooks for the given event. Returns list of outputs."""
        hook_defs = self._hooks.get(event.value, [])
        if not hook_defs:
            return []

        ctx = context or {}
        outputs = []

        for hook in hook_defs:
            # Check matcher
            tool_name = ctx.get("tool_name", "")
            if hook.matcher != "*" and not fnmatch.fnmatch(tool_name, hook.matcher):
                continue

            env = {
                **os.environ,
                "HOOK_EVENT": event.value,
                "TOOL_NAME": ctx.get("tool_name", ""),
                "TOOL_INPUT": json.dumps(ctx.get("tool_input", {})),
                "TOOL_RESULT": ctx.get("tool_result", ""),
                "AGENTIC_PROJECT_DIR": ctx.get("project_dir", ""),
            }

            try:
                proc = await asyncio.create_subprocess_shell(
                    hook.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                output = stdout.decode(errors="replace").strip()
                if output:
                    outputs.append(output)
            except asyncio.TimeoutError:
                outputs.append(f"Hook timed out: {hook.command}")
            except Exception as e:
                outputs.append(f"Hook error: {e}")

        return outputs

    def add_hook(self, event: HookEvent, hook: HookConfig) -> None:
        event_key = event.value
        if event_key not in self._hooks:
            self._hooks[event_key] = []
        self._hooks[event_key].append(hook)
