"""Persistent file-based memory system with MEMORY.md index."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentic.memory.types import MemoryType

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
MAX_INDEX_LINES = 200

# Per-type character budgets when loading memories into context.
# Feedback and user prefs are always most important; project is next.
_CONTEXT_BUDGET: dict[str, int] = {
    "feedback":  6_000,
    "user":      3_000,
    "project":   4_000,
    "reference": 2_000,
}
_TOTAL_CONTEXT_BUDGET = 12_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MemoryRecord:
    def __init__(
        self,
        name: str,
        description: str,
        memory_type: MemoryType,
        body: str,
        path: Path,
        created_at: str = "",
        updated_at: str = "",
    ):
        self.name = name
        self.description = description
        self.memory_type = memory_type
        self.body = body
        self.path = path
        self.created_at = created_at or _now_iso()
        self.updated_at = updated_at or _now_iso()

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def to_markdown(self) -> str:
        frontmatter = {
            "name": self.name,
            "description": self.description,
            "metadata": {
                "type": self.memory_type.value,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            },
        }
        fm = yaml.dump(frontmatter, default_flow_style=False).strip()
        return f"---\n{fm}\n---\n\n{self.body}\n"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.to_markdown(), encoding="utf-8")

    def age_days(self) -> float:
        try:
            dt = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        except Exception:
            return 0.0


class MemoryManager:
    """CRUD for agent memories, with MEMORY.md index and full-body context loading."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.index_path = memory_dir / "MEMORY.md"

    def _ensure_dir(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def load_index(self) -> str:
        """Return the MEMORY.md index (names + descriptions only)."""
        if not self.index_path.exists():
            return ""
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[:MAX_INDEX_LINES])

    def load_for_context(self) -> str:
        """Return full memory bodies suitable for injecting into the system prompt.

        Memories are grouped by type, sorted by recency, and trimmed to fit
        within a total character budget so large memory banks don't flood context.
        Feedback and user preferences always get priority.
        """
        records = self.list_all()
        if not records:
            return ""

        # Group by type, newest first
        by_type: dict[str, list[MemoryRecord]] = {}
        for r in records:
            by_type.setdefault(r.memory_type.value, []).append(r)
        for t in by_type:
            by_type[t].sort(key=lambda r: r.updated_at, reverse=True)

        sections: list[str] = []
        total_chars = 0
        type_order = ["feedback", "user", "project", "reference"]

        for t in type_order:
            recs = by_type.get(t, [])
            if not recs:
                continue
            budget = _CONTEXT_BUDGET.get(t, 2_000)
            remaining = min(budget, _TOTAL_CONTEXT_BUDGET - total_chars)
            if remaining <= 0:
                break

            type_lines: list[str] = [f"### {t.capitalize()} memories\n"]
            type_chars = len(type_lines[0])

            for rec in recs:
                entry = f"**{rec.name}** — {rec.description}\n{rec.body}\n"
                if type_chars + len(entry) > remaining:
                    # Truncate this entry to fit
                    allowed = remaining - type_chars - 30
                    if allowed > 100:
                        entry = f"**{rec.name}** — {rec.description}\n{rec.body[:allowed]}…\n"
                    else:
                        break
                type_lines.append(entry)
                type_chars += len(entry)
                if type_chars >= remaining:
                    break

            if len(type_lines) > 1:
                sections.append("\n".join(type_lines))
                total_chars += type_chars

        return "\n".join(sections) if sections else ""

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
            for rec in sorted(recs, key=lambda r: r.updated_at, reverse=True):
                age = f" ({rec.age_days():.0f}d ago)" if rec.age_days() > 1 else ""
                hook = rec.description[:120]
                lines.append(f"- [{rec.name}]({rec.path.name}) — {hook}{age}")
            lines.append("")

        self.index_path.write_text("\n".join(lines), encoding="utf-8")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        memory_type: MemoryType,
        body: str,
    ) -> MemoryRecord:
        self._ensure_dir()
        slug = re.sub(r"[^a-z0-9_-]", "_", name.lower()).strip("_")
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
        record.touch()
        record.save()
        self._save_index()
        return True

    def delete(self, name: str) -> bool:
        record = self.get(name)
        if not record:
            return False
        record.path.unlink(missing_ok=True)
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
            meta = fm.get("metadata", {})
            body = text[m.end():].strip()
            return MemoryRecord(
                name=fm.get("name", path.stem),
                description=fm.get("description", ""),
                memory_type=MemoryType(meta.get("type", "user")),
                body=body,
                path=path,
                created_at=meta.get("created_at", ""),
                updated_at=meta.get("updated_at", ""),
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
            existing.touch()
            existing.save()
            self._save_index()
            return existing
        return self.create(name, description, memory_type, body)

    # ------------------------------------------------------------------
    # Search — word-level with relevance scoring
    # ------------------------------------------------------------------

    def search(self, query: str) -> list[MemoryRecord]:
        """Search memories by relevance score (word overlap + recency bonus)."""
        words = set(re.findall(r"\w+", query.lower()))
        if not words:
            return []

        scored: list[tuple[float, MemoryRecord]] = []
        for record in self.list_all():
            haystack = (record.body + " " + record.description + " " + record.name).lower()
            hay_words = set(re.findall(r"\w+", haystack))
            overlap = len(words & hay_words)
            if overlap == 0:
                continue
            # Boost exact phrase match
            phrase_bonus = 2.0 if query.lower() in haystack else 0.0
            # Recency bonus (decays over 30 days)
            recency = max(0.0, 1.0 - record.age_days() / 30)
            score = overlap + phrase_bonus + recency * 0.5
            scored.append((score, record))

        return [r for _, r in sorted(scored, key=lambda x: x[0], reverse=True)]

    # ------------------------------------------------------------------
    # Staleness detection
    # ------------------------------------------------------------------

    def stale_project_memories(self, threshold_days: float = 30) -> list[MemoryRecord]:
        """Return project memories not updated in threshold_days."""
        return [
            r for r in self.list_by_type(MemoryType.PROJECT)
            if r.age_days() > threshold_days
        ]
