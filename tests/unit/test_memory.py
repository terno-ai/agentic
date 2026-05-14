"""Tests for the memory system."""

import pytest
from pathlib import Path
import tempfile

from agentic.memory.manager import MemoryManager
from agentic.memory.types import MemoryType


@pytest.fixture
def memory_dir(tmp_path):
    return tmp_path / "memory"


@pytest.fixture
def manager(memory_dir):
    return MemoryManager(memory_dir)


def test_create_and_get(manager):
    record = manager.create(
        name="test-user",
        description="Test user memory",
        memory_type=MemoryType.USER,
        body="The user is a senior Python developer.",
    )
    assert record.name == "test-user"

    fetched = manager.get("test-user")
    assert fetched is not None
    assert fetched.body == "The user is a senior Python developer."


def test_update(manager):
    manager.create("my-memory", "desc", MemoryType.FEEDBACK, "original body")
    updated = manager.update("my-memory", body="updated body")
    assert updated is True
    record = manager.get("my-memory")
    assert record.body == "updated body"


def test_delete(manager):
    manager.create("to-delete", "desc", MemoryType.PROJECT, "body")
    assert manager.get("to-delete") is not None
    manager.delete("to-delete")
    assert manager.get("to-delete") is None


def test_list_all(manager):
    manager.create("a", "desc a", MemoryType.USER, "body a")
    manager.create("b", "desc b", MemoryType.FEEDBACK, "body b")
    records = manager.list_all()
    assert len(records) == 2


def test_upsert_creates_if_not_exists(manager):
    record = manager.upsert("new-mem", "desc", MemoryType.REFERENCE, "body")
    assert manager.get("new-mem") is not None


def test_upsert_updates_if_exists(manager):
    manager.create("existing", "desc", MemoryType.USER, "original")
    manager.upsert("existing", "new desc", MemoryType.USER, "updated body")
    record = manager.get("existing")
    assert record.body == "updated body"


def test_search(manager):
    manager.create("python-mem", "python related", MemoryType.USER, "The user loves Python.")
    manager.create("java-mem", "java related", MemoryType.USER, "The user knows Java.")
    results = manager.search("Python")
    assert len(results) == 1
    assert results[0].name == "python-mem"


def test_memory_index_created(manager, memory_dir):
    manager.create("idx-test", "test", MemoryType.USER, "body")
    index_path = memory_dir / "MEMORY.md"
    assert index_path.exists()
    content = index_path.read_text()
    assert "idx-test" in content


def test_list_by_type(manager):
    manager.create("u1", "u", MemoryType.USER, "user body")
    manager.create("f1", "f", MemoryType.FEEDBACK, "feedback body")
    user_mems = manager.list_by_type(MemoryType.USER)
    assert len(user_mems) == 1
    assert user_mems[0].name == "u1"
