"""Tests for the permission system."""

import pytest
from agentic.core.config import PermissionsConfig
from agentic.permissions.manager import PermissionManager


@pytest.fixture
def manager():
    config = PermissionsConfig(
        allow=["Read", "Bash(git *)", "Bash(ls *)"],
        deny=["Bash(rm -rf *)"],
    )
    return PermissionManager(config=config)


@pytest.mark.asyncio
async def test_explicitly_allowed(manager):
    allowed, reason = await manager.check("Read", {"file_path": "/any/path"})
    assert allowed

@pytest.mark.asyncio
async def test_glob_allowed(manager):
    allowed, reason = await manager.check("Bash", {"command": "git status"})
    assert allowed

@pytest.mark.asyncio
async def test_glob_denied(manager):
    allowed, reason = await manager.check("Bash", {"command": "rm -rf /"})
    assert not allowed
    assert "denied" in reason

@pytest.mark.asyncio
async def test_unmatched_defaults_to_allow():
    config = PermissionsConfig(allow=[], deny=[])
    manager = PermissionManager(config=config)
    allowed, reason = await manager.check("Write", {"file_path": "/tmp/x"})
    assert allowed

@pytest.mark.asyncio
async def test_deny_takes_precedence():
    config = PermissionsConfig(
        allow=["Bash"],
        deny=["Bash(rm *)"],
    )
    manager = PermissionManager(config=config)
    # rm should be denied even though Bash is allowed
    allowed, _ = await manager.check("Bash", {"command": "rm file.txt"})
    assert not allowed

@pytest.mark.asyncio
async def test_session_allow(manager):
    manager._session_allows.add("Write")
    allowed, reason = await manager.check("Write", {"file_path": "/tmp/x"})
    assert allowed
    assert "session" in reason

@pytest.mark.asyncio
async def test_add_allow(manager):
    manager.add_allow("Write")
    allowed, _ = await manager.check("Write", {"file_path": "/tmp/x"})
    assert allowed
