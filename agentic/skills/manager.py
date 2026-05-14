"""Skill discovery and loading from YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtin"
GLOBAL_SKILLS_DIR = Path.home() / ".agentic" / "skills"


class SkillDefinition(BaseModel):
    name: str
    description: str
    prompt: str
    tools_allowed: list[str] = Field(default_factory=list)
    read_only: bool = False
    args_description: str = ""

    def format_prompt(self, args: str = "") -> str:
        return self.prompt.replace("{{args}}", args).replace("{{ args }}", args)


class SkillManager:
    """Discovers and loads skills from built-in, global, and project directories."""

    def __init__(self, project_dir: Path | None = None, extra_dirs: list[str] | None = None):
        self._project_dir = project_dir or Path.cwd()
        self._extra_dirs = [Path(d) for d in (extra_dirs or [])]
        self._skills: dict[str, SkillDefinition] = {}
        self._load_all()

    def _load_all(self) -> None:
        search_dirs = [
            BUILTIN_SKILLS_DIR,
            GLOBAL_SKILLS_DIR,
            self._project_dir / ".agentic" / "skills",
            *self._extra_dirs,
        ]
        for d in search_dirs:
            if d.exists():
                self._load_dir(d)

    def _load_dir(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
            skill = self._load_file(path)
            if skill:
                self._skills[skill.name] = skill

    def _load_file(self, path: Path) -> SkillDefinition | None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not data or not isinstance(data, dict):
                return None
            if "name" not in data:
                data["name"] = path.stem
            return SkillDefinition(**data)
        except Exception as e:
            return None

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)

    def list_all(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def names(self) -> list[str]:
        return list(self._skills.keys())

    def add_from_file(self, path: Path) -> SkillDefinition | None:
        skill = self._load_file(path)
        if skill:
            self._skills[skill.name] = skill
        return skill

    def reload(self) -> None:
        self._skills = {}
        self._load_all()
