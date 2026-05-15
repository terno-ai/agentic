"""Layered JSON settings: global → project → env vars."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


GLOBAL_CONFIG_DIR = Path.home() / ".agentic"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o"


def detect_provider(model: str) -> str:
    """Infer provider from model name prefix."""
    import re
    if model.startswith(("gpt-", "chatgpt-")) or re.match(r"^o\d", model):
        return "openai"
    return "anthropic"


class PermissionsConfig(BaseModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class MCPServerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    transport: str = "stdio"  # stdio | sse


class HookConfig(BaseModel):
    matcher: str = "*"
    command: str


class Settings(BaseModel):
    model: str = DEFAULT_MODEL
    max_tokens: int = 8192
    context_summarize_threshold: int = 80_000
    context_keep_recent: int = 10
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    hooks: dict[str, list[HookConfig]] = Field(default_factory=dict)
    skills_dirs: list[str] = Field(default_factory=list)
    theme: str = "dark"
    stream: bool = True
    auto_memory: bool = True
    plan_mode: bool = False
    max_tool_iterations: int = 50
    api_key: str = ""
    openai_api_key: str = ""
    provider: str = ""  # "anthropic" | "openai" — auto-detected from model name if blank

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(**data)
        except Exception:
            return cls()


class ConfigManager:
    """Layered config: global < project < env vars."""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.global_settings_path = GLOBAL_CONFIG_DIR / "settings.json"
        self.project_settings_path = self.project_dir / ".agentic" / "settings.json"
        self._settings: Settings | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = self._load_merged()
        return self._settings

    def _load_merged(self) -> Settings:
        global_s = Settings.load(self.global_settings_path)
        project_s = Settings.load(self.project_settings_path)

        # Merge: project overrides global
        merged = global_s.model_dump()
        project_data = {k: v for k, v in project_s.model_dump().items()
                        if v != Settings().model_dump().get(k)}
        merged.update(project_data)

        # Permissions: combine lists
        merged["permissions"]["allow"] = list(set(
            global_s.permissions.allow + project_s.permissions.allow
        ))
        merged["permissions"]["deny"] = list(set(
            global_s.permissions.deny + project_s.permissions.deny
        ))

        # Env var overrides
        if api_key := os.environ.get("ANTHROPIC_API_KEY"):
            merged["api_key"] = api_key
        if openai_key := os.environ.get("OPENAI_API_KEY"):
            merged["openai_api_key"] = openai_key
        if model := os.environ.get("AGENTIC_MODEL"):
            merged["model"] = model
        if provider := os.environ.get("AGENTIC_PROVIDER"):
            merged["provider"] = provider

        return Settings(**merged)

    def save_global(self, **kwargs: Any) -> None:
        s = Settings.load(self.global_settings_path)
        data = s.model_dump()
        data.update(kwargs)
        Settings(**data).save(self.global_settings_path)
        self._settings = None

    def save_project(self, **kwargs: Any) -> None:
        s = Settings.load(self.project_settings_path)
        data = s.model_dump()
        data.update(kwargs)
        Settings(**data).save(self.project_settings_path)
        self._settings = None

    def memory_dir(self) -> Path:
        project_hash = hashlib.md5(str(self.project_dir).encode()).hexdigest()[:12]
        return GLOBAL_CONFIG_DIR / "projects" / project_hash / "memory"

    def history_file(self) -> Path:
        return GLOBAL_CONFIG_DIR / "history"

    def reload(self) -> None:
        self._settings = None
