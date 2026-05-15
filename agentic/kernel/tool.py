"""KernelTool — agent interface to the persistent Python kernel."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.kernel.manager import KernelManager
    from agentic.kernel.result import KernelResult, KernelInspectResult


class KernelTool(Tool):
    name = "PythonKernel"
    description = (
        "Execute Python code in a persistent kernel that retains variables between calls. "
        "Prefer this over Bash for all Python and data-science work — no file writing needed.\n\n"
        "actions:\n"
        "  execute  — run Python code; stdout, stderr, and the return value are captured separately\n"
        "  restart  — reset the kernel and clear all variables (use after OOM or corrupted state)\n"
        "  inspect  — list all variables in scope with their types, shapes, and memory sizes\n"
        "  interrupt — cancel a long-running or hung execution (sends KeyboardInterrupt)"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["execute", "restart", "inspect", "interrupt"],
                "description": "What to do",
            },
            "code": {
                "type": "string",
                "description": "Python code to execute (required for action=execute)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default from config, 0 = no limit)",
            },
            "stdin": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Pre-supplied answers for code that calls input(). "
                    "Always provide this when your code uses input() to avoid hanging."
                ),
            },
        },
        "required": ["action"],
    }

    def __init__(self, kernel: "KernelManager"):
        self._kernel = kernel

    async def execute(
        self,
        action: str,
        code: str = "",
        timeout: int | None = None,
        stdin: list[str] | None = None,
    ) -> ToolResult:
        from agentic.kernel.manager import KernelNotRunning, KernelBusy

        try:
            if action == "execute":
                return await self._do_execute(code, timeout, stdin)
            elif action == "restart":
                return await self._do_restart()
            elif action == "inspect":
                return await self._do_inspect()
            elif action == "interrupt":
                return await self._do_interrupt()
            else:
                return ToolResult.error(f"Unknown action: {action}")
        except KernelNotRunning as e:
            return ToolResult.error(f"Kernel not running: {e}")
        except KernelBusy:
            return ToolResult.error(
                "Kernel is busy. Wait for the current execution to finish, "
                "or use action='interrupt' to cancel it."
            )
        except Exception as e:
            return ToolResult.error(f"Kernel error: {e}")

    # ------------------------------------------------------------------

    async def _do_execute(
        self, code: str, timeout: int | None, stdin: list[str] | None
    ) -> ToolResult:
        if not code.strip():
            return ToolResult.error("No code provided for action=execute.")

        result = await self._kernel.execute(code, timeout=timeout, stdin_lines=stdin)
        return ToolResult(
            content=_format_result(result),
            is_error=result.kind in ("error", "oom_error", "unresponsive", "timeout"),
        )

    async def _do_restart(self) -> ToolResult:
        msg = await self._kernel.restart()
        return ToolResult.ok(f"🔄 {msg}")

    async def _do_inspect(self) -> ToolResult:
        result = await self._kernel.inspect()
        return ToolResult.ok(_format_inspect(result))

    async def _do_interrupt(self) -> ToolResult:
        msg = await self._kernel.interrupt()
        return ToolResult.ok(f"⏹ {msg}")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

# Hard cap on total characters returned to the agent per kernel execution.
# The worker already truncates per-stream, but this is a final safety net
# to ensure the combined tool result (stdout + stderr + result + metadata)
# never exceeds this amount regardless of config.
_TOTAL_OUTPUT_CAP = 12_000


def _format_result(r: "KernelResult") -> str:
    parts: list[str] = []

    # Memory warnings (surfaced before the result)
    for w in r.warnings:
        parts.append(f"⚠️  {w}")

    if r.kind == "result":
        if r.stdout:
            parts.append(f"[stdout]\n{r.stdout.rstrip()}")
        if r.stderr:
            parts.append(f"[stderr]\n{r.stderr.rstrip()}")
        if r.result_repr is not None:
            parts.append(f"[result]\n{r.result_repr}")
        if not r.stdout and not r.result_repr:
            parts.append("[stdout]\n(no output)")
        parts.append(
            f"[memory] {r.memory_mb:.1f} MB  "
            f"[exec #{r.execution_count}]  [{r.duration_ms}ms]"
        )

    elif r.kind == "error":
        if r.stdout:
            parts.append(f"[stdout]\n{r.stdout.rstrip()}")
        if r.stderr:
            parts.append(f"[stderr]\n{r.stderr.rstrip()}")
        tb = "\n".join(r.traceback) if r.traceback else f"{r.error_name}: {r.error_value}"
        parts.append(f"[error] {r.error_name}: {r.error_value}\n{tb}")
        parts.append(f"[memory] {r.memory_mb:.1f} MB  [exec #{r.execution_count}]  [{r.duration_ms}ms]")

    elif r.kind == "timeout":
        parts.append(
            f"⏱️  {r.message}\n"
            "Use action='interrupt' to cancel the current run, "
            "or action='restart' to reset the kernel."
        )

    elif r.kind == "oom_error":
        parts.append(
            f"💥 {r.message}\n"
            "Fix: delete large variables with `del var`, process data in chunks, "
            "or increase memory_limit_mb in settings."
        )

    elif r.kind == "unresponsive":
        parts.append(f"🔴 {r.message}")

    else:
        parts.append(r.message or str(r.kind))

    text = "\n\n".join(p for p in parts if p)

    # Final hard cap — keep the tail so the agent sees the most recent output
    if len(text) > _TOTAL_OUTPUT_CAP:
        dropped = len(text) - _TOTAL_OUTPUT_CAP
        text = (
            f"[output truncated: {dropped:,} chars omitted from start]\n\n"
            + text[-_TOTAL_OUTPUT_CAP:]
        )

    return text


def _format_inspect(r: "KernelInspectResult") -> str:
    if not r.variables:
        return (
            f"Kernel namespace is empty.\n"
            f"[memory] {r.memory_mb:.1f} MB  [exec #{r.execution_count}]"
        )

    rows = [f"{'Name':<20} {'Type':<22} {'Size MB':>8}  Repr"]
    rows.append("-" * 72)
    for v in sorted(r.variables, key=lambda x: -x.size_mb):
        rows.append(f"{v.name:<20} {v.type:<22} {v.size_mb:>8.3f}  {v.repr[:50]}")

    rows.append("")
    rows.append(f"[memory] {r.memory_mb:.1f} MB  [exec #{r.execution_count}]")
    return "\n".join(rows)
