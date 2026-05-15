"""Tests for the benchmark feedback-loop harness."""

from __future__ import annotations

import textwrap
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from benchmarks.swebench.validating_tools import (
    ValidatingEditTool,
    ValidatingWriteTool,
    _check_syntax,
    _is_python,
)


# ---------------------------------------------------------------------------
# _check_syntax
# ---------------------------------------------------------------------------

def test_check_syntax_valid_file(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n")
    ok, msg = _check_syntax(str(f))
    assert ok
    assert "OK" in msg


def test_check_syntax_invalid_file(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(\n")  # unclosed paren
    ok, msg = _check_syntax(str(f))
    assert not ok
    assert "FAILED" in msg
    assert "SyntaxError" in msg or "Error" in msg


def test_check_syntax_nonexistent_file():
    ok, msg = _check_syntax("/nonexistent/path/file.py")
    # Should not raise; treats missing file as a skipped check
    assert isinstance(ok, bool)


# ---------------------------------------------------------------------------
# _is_python
# ---------------------------------------------------------------------------

def test_is_python_true():
    assert _is_python("foo/bar.py") is True

def test_is_python_false_js():
    assert _is_python("foo/bar.js") is False

def test_is_python_false_no_ext():
    assert _is_python("Makefile") is False


# ---------------------------------------------------------------------------
# ValidatingEditTool
# ---------------------------------------------------------------------------

class TestValidatingEditTool:
    @pytest.mark.asyncio
    async def test_valid_edit_appends_ok(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\ny = 2\n")
        tool = ValidatingEditTool()
        result = await tool.execute(
            file_path=str(f),
            old_string="x = 1",
            new_string="x = 10",
        )
        assert not result.is_error
        assert "py_compile" in result.content
        assert "OK" in result.content

    @pytest.mark.asyncio
    async def test_syntax_error_edit_marks_as_error(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        tool = ValidatingEditTool()
        # Replace with syntactically broken code
        result = await tool.execute(
            file_path=str(f),
            old_string="x = 1",
            new_string="def broken(\n",
        )
        assert result.is_error
        assert "FAILED" in result.content

    @pytest.mark.asyncio
    async def test_non_python_file_skips_check(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text('{"key": "value"}')
        tool = ValidatingEditTool()
        result = await tool.execute(
            file_path=str(f),
            old_string='"value"',
            new_string='"new_value"',
        )
        assert not result.is_error
        assert "py_compile" not in result.content

    @pytest.mark.asyncio
    async def test_underlying_edit_error_not_double_checked(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        tool = ValidatingEditTool()
        # old_string not in file → EditTool returns error, no py_compile
        result = await tool.execute(
            file_path=str(f),
            old_string="nonexistent string",
            new_string="anything",
        )
        assert result.is_error
        assert "py_compile" not in result.content


# ---------------------------------------------------------------------------
# ValidatingWriteTool
# ---------------------------------------------------------------------------

class TestValidatingWriteTool:
    @pytest.mark.asyncio
    async def test_valid_write_appends_ok(self, tmp_path):
        f = tmp_path / "new.py"
        tool = ValidatingWriteTool()
        result = await tool.execute(
            file_path=str(f),
            content="def hello():\n    return 42\n",
        )
        assert not result.is_error
        assert "py_compile" in result.content
        assert "OK" in result.content

    @pytest.mark.asyncio
    async def test_syntax_error_write_marks_as_error(self, tmp_path):
        f = tmp_path / "bad.py"
        tool = ValidatingWriteTool()
        result = await tool.execute(
            file_path=str(f),
            content="def broken(\n",   # invalid Python
        )
        assert result.is_error
        assert "FAILED" in result.content

    @pytest.mark.asyncio
    async def test_non_python_file_skips_check(self, tmp_path):
        f = tmp_path / "data.txt"
        tool = ValidatingWriteTool()
        result = await tool.execute(file_path=str(f), content="hello world")
        assert not result.is_error
        assert "py_compile" not in result.content


# ---------------------------------------------------------------------------
# BenchmarkAgentLoop — test feedback injection
# ---------------------------------------------------------------------------

class TestBenchmarkAgentLoop:
    """
    Test that the feedback loop injects test output and retries when tests fail,
    and stops early when tests pass.
    """

    def _make_agent(self, tmp_path, fail_tests=None, enable_test_feedback=True):
        from agentic.core.config import ConfigManager, Settings
        from benchmarks.swebench.benchmark_agent import BenchmarkAgentLoop

        config = ConfigManager(project_dir=tmp_path)
        config._settings = Settings(
            model="gpt-4o",
            provider="openai",
            openai_api_key="sk-fake",
            auto_memory=False,
        )

        # Use explicit None check so callers can pass an empty list
        tests = ["tests/test_foo.py::test_bar"] if fail_tests is None else fail_tests

        agent = BenchmarkAgentLoop(
            config=config,
            fail_tests=tests,
            repo_dir=tmp_path,
            enable_test_feedback=enable_test_feedback,
            max_feedback_rounds=2,
            is_subagent=True,
        )
        return agent

    @pytest.mark.asyncio
    async def test_stops_early_when_tests_pass(self, tmp_path):
        agent = self._make_agent(tmp_path)

        # Patch _agent_loop to return immediately and _run_failing_tests to pass
        agent._agent_loop = AsyncMock(return_value="fixed it")
        agent._run_failing_tests = AsyncMock(return_value=(True, ""))

        result = await agent.run_once("fix the bug")

        assert agent._agent_loop.call_count == 1  # only one round needed
        assert agent._run_failing_tests.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_when_tests_fail_then_pass(self, tmp_path):
        agent = self._make_agent(tmp_path)

        agent._agent_loop = AsyncMock(return_value="attempt")
        # First call fails, second call passes
        agent._run_failing_tests = AsyncMock(
            side_effect=[(False, "[Test feedback] still failing"), (True, "")]
        )

        await agent.run_once("fix the bug")

        assert agent._agent_loop.call_count == 2
        assert agent._run_failing_tests.call_count == 2

        # Feedback message should have been injected into conversation
        messages = agent._conversation.messages
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("[Test feedback]" in str(m["content"]) for m in user_msgs)

    @pytest.mark.asyncio
    async def test_respects_max_feedback_rounds(self, tmp_path):
        agent = self._make_agent(tmp_path)

        agent._agent_loop = AsyncMock(return_value="attempt")
        # Always fails
        agent._run_failing_tests = AsyncMock(
            return_value=(False, "[Test feedback] still failing")
        )

        await agent.run_once("fix the bug")

        # max_feedback_rounds=2, so agent_loop called at most 2 times
        assert agent._agent_loop.call_count <= 2

    @pytest.mark.asyncio
    async def test_no_test_feedback_runs_once(self, tmp_path):
        agent = self._make_agent(tmp_path, enable_test_feedback=False)

        agent._agent_loop = AsyncMock(return_value="done")
        agent._run_failing_tests = AsyncMock(return_value=(False, ""))

        await agent.run_once("fix the bug")

        assert agent._agent_loop.call_count == 1
        agent._run_failing_tests.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_fail_tests_runs_once(self, tmp_path):
        agent = self._make_agent(tmp_path, fail_tests=[])

        agent._agent_loop = AsyncMock(return_value="done")
        agent._run_failing_tests = AsyncMock()

        await agent.run_once("fix the bug")

        assert agent._agent_loop.call_count == 1
        agent._run_failing_tests.assert_not_called()
