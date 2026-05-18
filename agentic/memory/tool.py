"""Memory tools — MemoryWrite, MemoryRead, MemoryDelete."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic.memory.types import MemoryType
from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.memory.manager import MemoryManager


class MemoryWriteTool(Tool):
    name = "MemoryWrite"
    description = (
        "Save or update a persistent memory available in future sessions. "
        "Call proactively whenever you learn something worth remembering: "
        "user preferences, behavioral corrections, project facts, or external references. "
        "Use upsert semantics — re-saving the same name overwrites the old body."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short kebab-case slug, e.g. 'user-prefers-tabs'. Reuse the same name to update.",
            },
            "description": {
                "type": "string",
                "description": "One-line summary shown in the memory index.",
            },
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": (
                    "user=preferences/expertise, "
                    "feedback=behavioral corrections & confirmations, "
                    "project=ongoing work/goals/platform/entry-point, "
                    "reference=external system pointers"
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Full memory content. "
                    "For feedback: lead with the rule, then **Why:** and **How to apply:** lines. "
                    "For project: include platform, language, entry point, constraints."
                ),
            },
        },
        "required": ["name", "description", "type", "body"],
    }

    def __init__(self, memory_manager: "MemoryManager") -> None:
        self._memory = memory_manager

    async def execute(self, name: str, description: str, type: str, body: str) -> ToolResult:
        try:
            mem_type = MemoryType(type)
        except ValueError:
            return ToolResult.error(
                f"Invalid memory type '{type}'. Use: user, feedback, project, reference"
            )
        record = self._memory.upsert(name, description, mem_type, body)
        action = "Updated" if record.updated_at != record.created_at else "Saved"
        return ToolResult.ok(
            f"{action} memory: {record.name} ({mem_type.value})",
            memory_name=record.name,
            memory_type=mem_type.value,
        )


class MemoryReadTool(Tool):
    name = "MemoryRead"
    description = (
        "Read the full body of a specific memory by name. "
        "Use this before updating a memory to see its current content, "
        "or to verify what was saved."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Memory name (slug) from the memory index.",
            },
        },
        "required": ["name"],
    }

    def __init__(self, memory_manager: "MemoryManager") -> None:
        self._memory = memory_manager

    async def execute(self, name: str) -> ToolResult:
        record = self._memory.get(name)
        if not record:
            # Try fuzzy — search for closest match
            results = self._memory.search(name)
            if results:
                suggestions = ", ".join(r.name for r in results[:3])
                return ToolResult.error(
                    f"Memory '{name}' not found. Similar: {suggestions}"
                )
            return ToolResult.error(f"Memory '{name}' not found.")
        lines = [
            f"**{record.name}** ({record.memory_type.value})",
            f"Description: {record.description}",
            f"Updated: {record.updated_at}",
            "",
            record.body,
        ]
        return ToolResult.ok("\n".join(lines))


class MemoryDeleteTool(Tool):
    name = "MemoryDelete"
    description = (
        "Delete a memory that is no longer accurate or relevant. "
        "Use this when a project changes direction, a preference is reversed, "
        "or a reference is outdated. Always prefer updating over deleting when the "
        "memory is still partially valid."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Memory name (slug) to delete.",
            },
            "reason": {
                "type": "string",
                "description": "Why this memory is being deleted (for audit trail).",
            },
        },
        "required": ["name"],
    }

    def __init__(self, memory_manager: "MemoryManager") -> None:
        self._memory = memory_manager

    async def execute(self, name: str, reason: str = "") -> ToolResult:
        record = self._memory.get(name)
        if not record:
            return ToolResult.error(f"Memory '{name}' not found.")
        self._memory.delete(name)
        msg = f"Deleted memory: {name}"
        if reason:
            msg += f" (reason: {reason})"
        return ToolResult.ok(msg)
