"""Result types returned by KernelManager.execute()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class KernelResult:
    kind: str                           # result | error | timeout | oom | unresponsive | interrupted | restarted
    stdout: str = ""
    stderr: str = ""
    result_repr: str | None = None      # repr() of last expression, if any
    error_name: str | None = None
    error_value: str | None = None
    traceback: list[str] = field(default_factory=list)
    execution_count: int = 0
    duration_ms: int = 0
    memory_mb: float = 0.0
    warnings: list[str] = field(default_factory=list)  # memory warnings
    message: str = ""                   # human-readable status for non-result kinds

    @classmethod
    def from_msg(cls, msg: dict[str, Any], warnings: list[dict] | None = None) -> "KernelResult":
        kind = msg.get("type", "error")
        warn_texts = [w.get("message", "") for w in (warnings or [])]

        if kind == "result":
            return cls(
                kind="result",
                stdout=msg.get("stdout", ""),
                stderr=msg.get("stderr", ""),
                result_repr=msg.get("result"),
                execution_count=msg.get("execution_count", 0),
                duration_ms=msg.get("duration_ms", 0),
                memory_mb=msg.get("memory_mb", 0.0),
                warnings=warn_texts,
            )
        elif kind == "error":
            return cls(
                kind="error",
                stdout=msg.get("stdout", ""),
                stderr=msg.get("stderr", ""),
                error_name=msg.get("ename"),
                error_value=msg.get("evalue"),
                traceback=msg.get("traceback", []),
                execution_count=msg.get("execution_count", 0),
                duration_ms=msg.get("duration_ms", 0),
                memory_mb=msg.get("memory_mb", 0.0),
                warnings=warn_texts,
            )
        else:
            return cls(
                kind=kind,
                stdout=msg.get("stdout", ""),
                stderr=msg.get("stderr", ""),
                execution_count=msg.get("execution_count", 0),
                duration_ms=msg.get("duration_ms", 0),
                memory_mb=msg.get("memory_mb", 0.0),
                message=msg.get("message", ""),
                warnings=warn_texts,
            )


@dataclass
class KernelVariable:
    name: str
    type: str
    repr: str
    size_mb: float


@dataclass
class KernelInspectResult:
    variables: list[KernelVariable]
    memory_mb: float
    execution_count: int
    python_version: str
