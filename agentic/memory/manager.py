"""Persistent file-based memory system with MEMORY.md index."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agentic.memory.types import MemoryType


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
MAX_INDEX_LINES = 200


class MemoryRecord:
    def __init__(self, name: str, description: str, memory_type: MemoryType, body: str, path: Path):
        self.name = name
        self.description = description
        self.memory_type = memory_type
        self.body = body
        self.path = path

    def to_markdown(self) -> str:
        frontmatter = {
            "name": self.name,
            "description": self.description,
            "metadata": {"type": self.memory_type.value},
        }
        fm = yaml.dump(frontmatter, default_flow_style=False).strip()
        return f"---\n{fm}\n---\n\n{self.body}\n"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.to_markdown(), encoding="utf-8")


class MemoryManager:
    """CRUD for agent memories, with MEMORY.md index."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.index_path = memory_dir / "MEMORY.md"

    def _ensure_dir(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    # --- Index management ---

    def load_index(self) -> str:
        if not self.index_path.exists():
            return ""
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[:MAX_INDEX_LINES])

    def _save_index(self) -> None:
        records = self.list_all()
        if not records:
            if self.index_path.exists():
                self.index_path.write_text("", encoding="utf-8")
            return

        lines = ["# Memory Index\n"]
        by_type: dict[str, list[MemoryRecord]] = {}
        for r in records:
            by_type.setdefault(r.memory_type.value, []).append(r)

        for t, recs in sorted(by_type.items()):
            lines.append(f"## {t.capitalize()}\n")
            for rec in recs:
                fname = rec.path.name
                hook = rec.description[:120]
                lines.append(f"- [{rec.name}]({fname}) — {hook}")
            lines.append("")

        self.index_path.write_text("\n".join(lines), encoding="utf-8")

    # --- CRUD ---

    def create(
        self,
        name: str,
        description: str,
        memory_type: MemoryType,
        body: str,
    ) -> MemoryRecord:
        self._ensure_dir()
        slug = re.sub(r"[^a-z0-9_-]", "_", name.lower())
        path = self.memory_dir / f"{memory_type.value}_{slug}.md"

        record = MemoryRecord(name, description, memory_type, body, path)
        record.save()
        self._save_index()
        return record

    def get(self, name: str) -> MemoryRecord | None:
        for path in self.memory_dir.glob("*.md"):
            if path.name == "MEMORY.md":
                continue
            record = self._load_file(path)
            if record and record.name == name:
                return record
        return None

    def update(self, name: str, body: str | None = None, description: str | None = None) -> bool:
        record = self.get(name)
        if not record:
            return False
        if body is not None:
            record.body = body
        if description is not None:
            record.description = description
        record.save()
        self._save_index()
        return True

    def delete(self, name: str) -> bool:
        record = self.get(name)
        if not record:
            return False
        record.path.unlink()
        self._save_index()
        return True

    def list_all(self) -> list[MemoryRecord]:
        if not self.memory_dir.exists():
            return []
        records = []
        for path in sorted(self.memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            record = self._load_file(path)
            if record:
                records.append(record)
        return records

    def list_by_type(self, memory_type: MemoryType) -> list[MemoryRecord]:
        return [r for r in self.list_all() if r.memory_type == memory_type]

    def _load_file(self, path: Path) -> MemoryRecord | None:
        try:
            text = path.read_text(encoding="utf-8")
            m = FRONTMATTER_RE.match(text)
            if not m:
                return None
            fm = yaml.safe_load(m.group(1))
            body = text[m.end():]
            return MemoryRecord(
                name=fm.get("name", path.stem),
                description=fm.get("description", ""),
                memory_type=MemoryType(fm.get("metadata", {}).get("type", "user")),
                body=body.strip(),
                path=path,
            )
        except Exception:
            return None

    def upsert(
        self,
        name: str,
        description: str,
        memory_type: MemoryType,
        body: str,
    ) -> MemoryRecord:
        existing = self.get(name)
        if existing:
            existing.body = body
            existing.description = description
            existing.save()
            self._save_index()
            return existing
        return self.create(name, description, memory_type, body)

    def search(self, query: str) -> list[MemoryRecord]:
        """Simple text search across memory bodies."""
        query_lower = query.lower()
        results = []
        for record in self.list_all():
            if query_lower in record.body.lower() or query_lower in record.description.lower():
                results.append(record)
        return results
