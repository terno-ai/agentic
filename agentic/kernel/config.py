"""Kernel configuration model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KernelConfig(BaseModel):
    enabled: bool = False
    memory_limit_mb: int = 512          # 0 = no limit
    default_timeout_s: int = 60         # 0 = no limit
    heartbeat_interval_s: int = 5
    watchdog_timeout_s: int = 30        # seconds without heartbeat → unresponsive
    auto_restart_on_oom: bool = True
    max_output_chars: int = 10_000      # truncate very long stdout/stderr
    startup_code: str = ""              # extra code run at kernel start
