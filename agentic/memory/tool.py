"""MemoryWriteTool — lets the model save memories via a direct tool call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic.memory.types import MemoryType
from agentic.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from agentic.memory.manager import MemoryManager


class MemoryWriteTool(Tool):
    name = "MemoryWrite"
    description = (
        "Save a persistent memory that will be available in future sessions. "
        "Use this instead of embedding <memory_save> tags in your response text. "
        "Call this proactively when you learn something worth remembering."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short kebab-case slug (e.g. 'user-prefers-tabs')",
            },
            "description": {
                "type": "string",
                "description": "One-line summary — used to decide relevance in future sessions",
            },
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "Memory type: user=preferences, feedback=corrections, project=work context, reference=external pointers",
            },
            "body": {
                "type": "string",
                "description": "Memory content. For feedback/project: lead with the rule/fact, then Why: and How to apply: lines.",
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
            return ToolResult.error(f"Invalid memory type '{type}'. Use: user, feedback, project, reference")
        record = self._memory.upsert(name, description, mem_type, body)
        return ToolResult.ok(f"Memory saved: {record.name} ({mem_type.value})")
