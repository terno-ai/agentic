"""Tests for the sandbox components."""

from __future__ import annotations

import pytest
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentic.core.config import SandboxConfig
from agentic.sandbox.docker_sandbox import _extract_sentinel, _quote, DockerSandbox
from agentic.sandbox.sandboxed_bash import SandboxedBashTool


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
    def _make_sandbox(self, tmp_path):
        cfg = SandboxConfig(auto_build=False)
        return DockerSandbox(cfg, workspace=tmp_path)

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
        sb = DockerSandbox(cfg, workspace=tmp_path)
        await sb.start()
        try:
            output, rc = await sb.run("echo hello from sandbox")
            assert rc == 0
            assert "hello from sandbox" in output
        finally:
            await sb.stop()

    @pytest.mark.asyncio
    async def test_cd_persists(self, tmp_path):
        cfg = SandboxConfig(auto_build=False, image="ubuntu:22.04")
        sb = DockerSandbox(cfg, workspace=tmp_path)
        await sb.start()
        try:
            await sb.run("cd /tmp")
            assert sb.current_dir == "/tmp"
            output, rc = await sb.run("pwd")
            assert "/tmp" in output
        finally:
            await sb.stop()

    @pytest.mark.asyncio
    async def test_nonzero_exit(self, tmp_path):
        cfg = SandboxConfig(auto_build=False, image="ubuntu:22.04")
        sb = DockerSandbox(cfg, workspace=tmp_path)
        await sb.start()
        try:
            _, rc = await sb.run("exit 42")
            assert rc == 42
        finally:
            await sb.stop()
