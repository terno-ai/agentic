"""Tests for the sandbox components."""

from __future__ import annotations

import pytest
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentic.core.config import SandboxConfig
from agentic.sandbox.docker_sandbox import _extract_sentinel, _quote, _sanitize_user_id, DockerSandbox
from agentic.sandbox.sandboxed_bash import SandboxedBashTool
from agentic.sandbox.sandboxed_file_tools import _remap, SandboxedWriteTool, SandboxedReadTool, SandboxedEditTool


# ---------------------------------------------------------------------------
# SandboxConfig defaults
# ---------------------------------------------------------------------------

def test_sandbox_config_defaults():
    cfg = SandboxConfig()
    assert cfg.enabled is False
    assert cfg.image == "agentic-sandbox:latest"
    assert cfg.memory_limit == "512m"
    assert cfg.cpu_limit == 1.0
    assert cfg.network == "bridge"
    assert cfg.auto_build is True

def test_sandbox_config_serialises():
    import json
    cfg = SandboxConfig(enabled=True, memory_limit="1g")
    data = json.loads(cfg.model_dump_json())
    assert data["enabled"] is True
    assert data["memory_limit"] == "1g"


# ---------------------------------------------------------------------------
# _extract_sentinel
# ---------------------------------------------------------------------------

def test_extract_sentinel_present():
    output = "hello\nworld\n__AGENTIC_PWD__/tmp/foo\n"
    clean, cwd = _extract_sentinel(output)
    assert cwd == "/tmp/foo"
    assert "__AGENTIC_PWD__" not in clean
    assert "hello" in clean

def test_extract_sentinel_absent():
    output = "just some output\n"
    clean, cwd = _extract_sentinel(output)
    assert cwd is None
    assert clean == output

def test_extract_sentinel_only_sentinel():
    output = "__AGENTIC_PWD__/workspace\n"
    clean, cwd = _extract_sentinel(output)
    assert cwd == "/workspace"
    assert clean.strip() == ""

def test_extract_sentinel_path_with_spaces():
    output = "ok\n__AGENTIC_PWD__/home/user/my project\n"
    _, cwd = _extract_sentinel(output)
    assert cwd == "/home/user/my project"


# ---------------------------------------------------------------------------
# _quote
# ---------------------------------------------------------------------------

def test_quote_simple():
    assert _quote("/workspace") == "'/workspace'"

def test_quote_with_spaces():
    assert _quote("/my dir/sub") == "'/my dir/sub'"

def test_quote_with_single_quote():
    # _quote uses shell's '\'' escape for single quotes inside single-quoted strings
    result = _quote("/it's here")
    # The result must start and end with a single quote
    assert result.startswith("'") and result.endswith("'")
    # The original path must not appear literally (the embedded ' must be escaped)
    assert result != f"'/it's here'"  # raw unescaped would be invalid shell
    # Shell escape sequence '\'' must be present
    assert "'\\''" in result


# ---------------------------------------------------------------------------
# Path remapping (_remap and sandboxed file tools)
# ---------------------------------------------------------------------------

def test_remap_workspace_root(tmp_path):
    assert _remap("/workspace", tmp_path) == str(tmp_path)

def test_remap_workspace_file(tmp_path):
    assert _remap("/workspace/game.py", tmp_path) == str(tmp_path / "game.py")

def test_remap_workspace_nested(tmp_path):
    assert _remap("/workspace/src/main.py", tmp_path) == str(tmp_path / "src" / "main.py")

def test_remap_non_workspace_path_unchanged(tmp_path):
    assert _remap("/tmp/other.py", tmp_path) == "/tmp/other.py"

def test_remap_relative_path_unchanged(tmp_path):
    assert _remap("game.py", tmp_path) == "game.py"


class TestSandboxedWriteTool:
    @pytest.mark.asyncio
    async def test_workspace_path_remapped(self, tmp_path):
        tool = SandboxedWriteTool(workspace=tmp_path)
        result = await tool.execute(
            file_path="/workspace/hello.py",
            content="print('hello')\n",
        )
        assert not result.is_error
        assert (tmp_path / "hello.py").exists()
        assert (tmp_path / "hello.py").read_text() == "print('hello')\n"

    @pytest.mark.asyncio
    async def test_host_path_unchanged(self, tmp_path):
        tool = SandboxedWriteTool(workspace=tmp_path)
        dest = tmp_path / "direct.py"
        result = await tool.execute(file_path=str(dest), content="x = 1\n")
        assert not result.is_error
        assert dest.exists()


class TestSandboxedReadTool:
    @pytest.mark.asyncio
    async def test_workspace_path_remapped(self, tmp_path):
        (tmp_path / "data.py").write_text("x = 42\n")
        tool = SandboxedReadTool(workspace=tmp_path)
        result = await tool.execute(file_path="/workspace/data.py")
        assert not result.is_error
        assert "42" in result.content

    @pytest.mark.asyncio
    async def test_nonexistent_workspace_file(self, tmp_path):
        tool = SandboxedReadTool(workspace=tmp_path)
        result = await tool.execute(file_path="/workspace/missing.py")
        assert result.is_error


class TestSandboxedEditTool:
    @pytest.mark.asyncio
    async def test_workspace_path_remapped(self, tmp_path):
        (tmp_path / "mod.py").write_text("x = 1\n")
        tool = SandboxedEditTool(workspace=tmp_path)
        result = await tool.execute(
            file_path="/workspace/mod.py",
            old_string="x = 1",
            new_string="x = 99",
        )
        assert not result.is_error
        assert (tmp_path / "mod.py").read_text() == "x = 99\n"


# ---------------------------------------------------------------------------
# SandboxedBashTool
# ---------------------------------------------------------------------------

class TestSandboxedBashTool:
    def _make_tool(self, run_return=("output", 0)):
        mock_sandbox = MagicMock()
        mock_sandbox.run = AsyncMock(return_value=run_return)
        mock_sandbox.current_dir = "/workspace"
        return SandboxedBashTool(sandbox=mock_sandbox), mock_sandbox

    @pytest.mark.asyncio
    async def test_success_result(self):
        tool, sandbox = self._make_tool(("hello world\n", 0))
        result = await tool.execute(command="echo hello world")
        assert not result.is_error
        assert "hello world" in result.content
        sandbox.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_nonzero_exit_is_error(self):
        tool, sandbox = self._make_tool(("error msg\n", 1))
        result = await tool.execute(command="false")
        assert result.is_error
        assert "error msg" in result.content

    @pytest.mark.asyncio
    async def test_sandbox_exception_becomes_error(self):
        mock_sandbox = MagicMock()
        mock_sandbox.run = AsyncMock(side_effect=RuntimeError("container died"))
        mock_sandbox.current_dir = "/workspace"
        tool = SandboxedBashTool(sandbox=mock_sandbox)
        result = await tool.execute(command="ls")
        assert result.is_error
        assert "Sandbox error" in result.content

    @pytest.mark.asyncio
    async def test_background_returns_immediately(self):
        tool, sandbox = self._make_tool()
        result = await tool.execute(command="sleep 10", run_in_background=True)
        assert not result.is_error
        assert "background" in result.content.lower()
        # sandbox.run was not awaited synchronously
        sandbox.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_propagated(self):
        tool, sandbox = self._make_tool()
        await tool.execute(command="ls", timeout=5000)
        # run(command, timeout_s) called with positional args
        args, _ = sandbox.run.call_args
        # timeout_s = 5000ms / 1000 = 5.0s
        assert args[1] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# DockerSandbox — unit tests (no real Docker)
# ---------------------------------------------------------------------------

class TestDockerSandboxUnit:
    def _make_sandbox(self, tmp_path, user_id="testuser"):
        cfg = SandboxConfig(auto_build=False)
        return DockerSandbox(cfg, user_id=user_id, workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_run_raises_if_not_started(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        with pytest.raises(RuntimeError, match="not started"):
            await sb.run("ls")

    @pytest.mark.asyncio
    async def test_run_updates_cwd_via_sentinel(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        sb._container_id = "fake-container"

        # Patch asyncio.create_subprocess_exec to return fake output
        fake_output = b"listed files\n__AGENTIC_PWD__/tmp/new\n"
        proc_mock = AsyncMock()
        proc_mock.communicate = AsyncMock(return_value=(fake_output, b""))
        proc_mock.returncode = 0

        with patch("agentic.sandbox.docker_sandbox.asyncio.create_subprocess_exec",
                   return_value=proc_mock):
            output, rc = await sb.run("ls && cd /tmp/new")

        assert rc == 0
        assert sb.current_dir == "/tmp/new"
        assert "__AGENTIC_PWD__" not in output
        assert "listed files" in output

    @pytest.mark.asyncio
    async def test_run_cwd_unchanged_on_no_sentinel(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        sb._container_id = "fake-container"
        sb._current_dir = "/workspace"

        fake_output = b"some output\n"
        proc_mock = AsyncMock()
        proc_mock.communicate = AsyncMock(return_value=(fake_output, b""))
        proc_mock.returncode = 0

        with patch("agentic.sandbox.docker_sandbox.asyncio.create_subprocess_exec",
                   return_value=proc_mock):
            await sb.run("echo hi")

        assert sb.current_dir == "/workspace"  # unchanged


# ---------------------------------------------------------------------------
# Multi-user sandbox — user_id, per-user workspace, container naming
# ---------------------------------------------------------------------------

def test_sanitize_user_id_simple():
    assert _sanitize_user_id("alice") == "alice"

def test_sanitize_user_id_email():
    assert _sanitize_user_id("alice@example.com") == "alice-example-com"

def test_sanitize_user_id_uppercase():
    assert _sanitize_user_id("BobSmith") == "bobsmith"

def test_sanitize_user_id_special_chars():
    # strip("-") removes leading/trailing dashes so trailing ! becomes dropped
    assert _sanitize_user_id("user name!") == "user-name"

def test_sanitize_user_id_empty_falls_back():
    assert _sanitize_user_id("") == "default"


def test_per_user_workspace_path(tmp_path):
    cfg = SandboxConfig(auto_build=False, users_workspace_root=str(tmp_path))
    sb = DockerSandbox(cfg, user_id="alice")
    assert sb.workspace == tmp_path / "alice" / "workspace"

def test_per_user_workspace_different_users(tmp_path):
    cfg = SandboxConfig(auto_build=False, users_workspace_root=str(tmp_path))
    sb_alice = DockerSandbox(cfg, user_id="alice")
    sb_bob   = DockerSandbox(cfg, user_id="bob")
    assert sb_alice.workspace != sb_bob.workspace
    assert "alice" in str(sb_alice.workspace)
    assert "bob"   in str(sb_bob.workspace)

def test_container_name_is_deterministic(tmp_path):
    cfg = SandboxConfig(auto_build=False, users_workspace_root=str(tmp_path))
    sb1 = DockerSandbox(cfg, user_id="alice")
    sb2 = DockerSandbox(cfg, user_id="alice")
    # Same user always gets the same container name
    assert sb1.container_name == sb2.container_name == "agentic-user-alice"

def test_container_name_differs_per_user(tmp_path):
    cfg = SandboxConfig(auto_build=False, users_workspace_root=str(tmp_path))
    sb_alice = DockerSandbox(cfg, user_id="alice")
    sb_bob   = DockerSandbox(cfg, user_id="bob")
    assert sb_alice.container_name != sb_bob.container_name

def test_explicit_workspace_overrides_user_path(tmp_path):
    explicit = tmp_path / "custom"
    cfg = SandboxConfig(auto_build=False)
    sb = DockerSandbox(cfg, user_id="alice", workspace=explicit)
    assert sb.workspace == explicit.resolve()

def test_workspace_created_on_start_not_at_init(tmp_path):
    cfg = SandboxConfig(auto_build=False, users_workspace_root=str(tmp_path))
    sb = DockerSandbox(cfg, user_id="newuser")
    # Workspace dir should NOT exist yet (no start() called)
    assert not sb.workspace.exists()


# ---------------------------------------------------------------------------
# Integration test (skipped if Docker unavailable)
# ---------------------------------------------------------------------------

docker_available = subprocess.run(
    ["docker", "info"], capture_output=True, timeout=5
).returncode == 0

@pytest.mark.skipif(not docker_available, reason="Docker not running")
class TestDockerSandboxIntegration:
    @pytest.mark.asyncio
    async def test_echo_command(self, tmp_path):
        cfg = SandboxConfig(auto_build=False, image="ubuntu:22.04")
        sb = DockerSandbox(cfg, user_id="test-echo", workspace=tmp_path)
        await sb.start()
        try:
            output, rc = await sb.run("echo hello from sandbox")
            assert rc == 0
            assert "hello from sandbox" in output
        finally:
            await sb.destroy()

    @pytest.mark.asyncio
    async def test_cd_persists(self, tmp_path):
        cfg = SandboxConfig(auto_build=False, image="ubuntu:22.04")
        sb = DockerSandbox(cfg, user_id="test-cd", workspace=tmp_path)
        await sb.start()
        try:
            await sb.run("cd /tmp")
            assert sb.current_dir == "/tmp"
            output, rc = await sb.run("pwd")
            assert "/tmp" in output
        finally:
            await sb.destroy()

    @pytest.mark.asyncio
    async def test_nonzero_exit(self, tmp_path):
        cfg = SandboxConfig(auto_build=False, image="ubuntu:22.04")
        sb = DockerSandbox(cfg, user_id="test-exit", workspace=tmp_path)
        await sb.start()
        try:
            _, rc = await sb.run("exit 42")
            assert rc == 42
        finally:
            await sb.destroy()

    @pytest.mark.asyncio
    async def test_container_reuse_same_user(self, tmp_path):
        """Two DockerSandbox instances for the same user share the same container."""
        cfg = SandboxConfig(auto_build=False, image="ubuntu:22.04")
        sb1 = DockerSandbox(cfg, user_id="test-reuse", workspace=tmp_path)
        sb2 = DockerSandbox(cfg, user_id="test-reuse", workspace=tmp_path)
        await sb1.start()
        try:
            # Starting sb2 should attach to the already-running container
            await sb2.start()
            assert sb1.container_name == sb2.container_name
            out, _ = await sb2.run("echo reused")
            assert "reused" in out
        finally:
            await sb1.destroy()
