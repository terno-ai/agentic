"""Tool base classes and result types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = {}

    @classmethod
    def ok(cls, content: str, **meta: Any) -> "ToolResult":
        return cls(content=content, metadata=meta)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True)


class Tool(ABC):
    """Abstract base for all agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        ...

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
