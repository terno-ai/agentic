"""Tests for the Python kernel components."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic.kernel.config import KernelConfig
from agentic.kernel.result import KernelResult, KernelVariable, KernelInspectResult
from agentic.kernel.tool import KernelTool, _format_result, _format_inspect


# ---------------------------------------------------------------------------
# KernelConfig defaults
# ---------------------------------------------------------------------------

def test_kernel_config_defaults():
    cfg = KernelConfig()
    assert cfg.enabled is False
    assert cfg.memory_limit_mb == 512
    assert cfg.default_timeout_s == 60
    assert cfg.watchdog_timeout_s == 30
    assert cfg.max_output_chars == 10_000


# ---------------------------------------------------------------------------
# KernelResult.from_msg
# ---------------------------------------------------------------------------

def test_result_from_success_msg():
    msg = {
        "type": "result",
        "id": "e1",
        "stdout": "hello\n",
        "stderr": "",
        "result": "42",
        "execution_count": 1,
        "duration_ms": 10,
        "memory_mb": 20.5,
    }
    r = KernelResult.from_msg(msg)
    assert r.kind == "result"
    assert r.stdout == "hello\n"
    assert r.result_repr == "42"
    assert r.execution_count == 1
    assert r.memory_mb == 20.5


def test_result_from_error_msg():
    msg = {
        "type": "error",
        "id": "e2",
        "stdout": "",
        "stderr": "",
        "ename": "ZeroDivisionError",
        "evalue": "division by zero",
        "traceback": ["Traceback:", "  line 1", "ZeroDivisionError: division by zero"],
        "execution_count": 2,
        "duration_ms": 5,
        "memory_mb": 18.0,
    }
    r = KernelResult.from_msg(msg)
    assert r.kind == "error"
    assert r.error_name == "ZeroDivisionError"
    assert r.error_value == "division by zero"
    assert len(r.traceback) == 3


def test_result_includes_warnings():
    msg = {"type": "result", "id": "e3", "stdout": "", "stderr": "",
           "result": None, "execution_count": 1, "duration_ms": 5, "memory_mb": 450.0}
    warnings = [{"type": "memory_warning", "message": "Memory at 90%"}]
    r = KernelResult.from_msg(msg, warnings)
    assert r.warnings == ["Memory at 90%"]


def test_result_from_timeout_msg():
    msg = {"type": "timeout", "id": "e4", "timeout_s": 60,
           "stdout": "", "stderr": "", "execution_count": 3,
           "duration_ms": 60000, "memory_mb": 30.0, "message": "Timed out."}
    r = KernelResult.from_msg(msg)
    assert r.kind == "timeout"
    assert r.message == "Timed out."


def test_result_from_oom_msg():
    msg = {"type": "oom_error", "id": "e5", "memory_mb": 520.0,
           "limit_mb": 512, "execution_count": 4, "duration_ms": 0,
           "message": "Out of memory."}
    r = KernelResult.from_msg(msg)
    assert r.kind == "oom_error"


# ---------------------------------------------------------------------------
# _format_result
# ---------------------------------------------------------------------------

def test_format_result_success():
    r = KernelResult(kind="result", stdout="42\n", result_repr=None,
                     execution_count=1, duration_ms=10, memory_mb=20.0)
    text = _format_result(r)
    assert "[stdout]" in text
    assert "42" in text
    assert "[memory]" in text
    assert "exec #1" in text


def test_format_result_with_return_value():
    r = KernelResult(kind="result", stdout="", result_repr="DataFrame(2x3)",
                     execution_count=2, duration_ms=5, memory_mb=30.0)
    text = _format_result(r)
    assert "[result]" in text
    assert "DataFrame(2x3)" in text


def test_format_result_error():
    r = KernelResult(
        kind="error", stdout="", stderr="",
        error_name="ValueError", error_value="bad value",
        traceback=["  File x.py line 1", "ValueError: bad value"],
        execution_count=1, duration_ms=2, memory_mb=15.0,
    )
    text = _format_result(r)
    assert "[error]" in text
    assert "ValueError" in text
    assert "bad value" in text


def test_format_result_timeout():
    r = KernelResult(kind="timeout", message="Execution timed out after 60s.")
    text = _format_result(r)
    assert "timed out" in text.lower()
    assert "interrupt" in text.lower()


def test_format_result_oom():
    r = KernelResult(kind="oom_error", message="Out of memory (520 MB). Kernel restarted.")
    text = _format_result(r)
    assert "💥" in text
    assert "del" in text or "delete" in text.lower()


def test_format_result_memory_warning_shown():
    r = KernelResult(kind="result", stdout="ok", execution_count=1,
                     duration_ms=5, memory_mb=450.0,
                     warnings=["Memory at 88%"])
    text = _format_result(r)
    assert "⚠" in text
    assert "88%" in text


def test_format_result_unresponsive():
    r = KernelResult(kind="unresponsive", message="Kernel not responding.")
    text = _format_result(r)
    assert "🔴" in text


# ---------------------------------------------------------------------------
# _format_inspect
# ---------------------------------------------------------------------------

def test_format_inspect_empty():
    r = KernelInspectResult(variables=[], memory_mb=10.0, execution_count=3,
                             python_version="3.11")
    text = _format_inspect(r)
    assert "empty" in text.lower()
    assert "10.0" in text


def test_format_inspect_with_vars():
    r = KernelInspectResult(
        variables=[
            KernelVariable(name="df", type="DataFrame", repr="shape=(100, 5)", size_mb=0.4),
            KernelVariable(name="x", type="int", repr="42", size_mb=0.0),
        ],
        memory_mb=25.0,
        execution_count=5,
        python_version="3.11",
    )
    text = _format_inspect(r)
    assert "df" in text
    assert "DataFrame" in text
    assert "x" in text
    assert "[memory]" in text


# ---------------------------------------------------------------------------
# KernelTool (mocked manager)
# ---------------------------------------------------------------------------

class TestKernelTool:
    def _make_tool(self, result: KernelResult) -> tuple[KernelTool, MagicMock]:
        mock_kernel = MagicMock()
        mock_kernel.execute = AsyncMock(return_value=result)
        mock_kernel.restart = AsyncMock(return_value="Kernel restarted.")
        mock_kernel.interrupt = AsyncMock(return_value="Interrupt sent.")
        mock_kernel.inspect = AsyncMock(return_value=KernelInspectResult(
            variables=[], memory_mb=10.0, execution_count=1, python_version="3.11"
        ))
        tool = KernelTool(kernel=mock_kernel)
        return tool, mock_kernel

    @pytest.mark.asyncio
    async def test_execute_success(self):
        r = KernelResult(kind="result", stdout="hello\n", execution_count=1,
                         duration_ms=5, memory_mb=20.0)
        tool, kernel = self._make_tool(r)
        result = await tool.execute(action="execute", code="print('hello')")
        assert not result.is_error
        assert "hello" in result.content
        kernel.execute.assert_called_once_with("print('hello')", timeout=None, stdin_lines=None)

    @pytest.mark.asyncio
    async def test_execute_error_marks_is_error(self):
        r = KernelResult(kind="error", error_name="ValueError",
                         error_value="bad", traceback=[], execution_count=1,
                         duration_ms=2, memory_mb=15.0)
        tool, kernel = self._make_tool(r)
        result = await tool.execute(action="execute", code="raise ValueError('bad')")
        assert result.is_error

    @pytest.mark.asyncio
    async def test_execute_timeout_marks_is_error(self):
        r = KernelResult(kind="timeout", message="Timed out.")
        tool, kernel = self._make_tool(r)
        result = await tool.execute(action="execute", code="while True: pass", timeout=1)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_restart(self):
        tool, kernel = self._make_tool(KernelResult(kind="result"))
        result = await tool.execute(action="restart")
        assert not result.is_error
        assert "restarted" in result.content.lower()

    @pytest.mark.asyncio
    async def test_inspect(self):
        tool, kernel = self._make_tool(KernelResult(kind="result"))
        result = await tool.execute(action="inspect")
        assert not result.is_error
        assert "empty" in result.content.lower()

    @pytest.mark.asyncio
    async def test_interrupt(self):
        tool, kernel = self._make_tool(KernelResult(kind="result"))
        result = await tool.execute(action="interrupt")
        assert not result.is_error
        assert "interrupt" in result.content.lower()

    @pytest.mark.asyncio
    async def test_empty_code_returns_error(self):
        tool, kernel = self._make_tool(KernelResult(kind="result"))
        result = await tool.execute(action="execute", code="   ")
        assert result.is_error
        assert "No code" in result.content

    @pytest.mark.asyncio
    async def test_stdin_passed_to_manager(self):
        r = KernelResult(kind="result", stdout="Alice\n", execution_count=1,
                         duration_ms=5, memory_mb=10.0)
        tool, kernel = self._make_tool(r)
        await tool.execute(action="execute", code="name=input()", stdin=["Alice"])
        _, kwargs = kernel.execute.call_args
        assert kwargs["stdin_lines"] == ["Alice"]


# ---------------------------------------------------------------------------
# Live worker smoke test (real subprocess, optional)
# ---------------------------------------------------------------------------

_kernel_available = True  # worker has no external deps beyond stdlib

@pytest.mark.skipif(not _kernel_available, reason="kernel worker unavailable")
class TestKernelWorkerLive:
    """
    Start the worker subprocess directly and speak the JSON-line protocol.
    Verifies end-to-end execution without KernelManager.
    """

    def _start_worker(self):
        worker_path = Path(__file__).parent.parent.parent / "agentic" / "kernel" / "worker.py"
        proc = subprocess.Popen(
            [sys.executable, str(worker_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return proc

    def _send(self, proc, msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def _recv(self, proc, timeout=10) -> dict:
        proc.stdout._CHUNK_SIZE = 1
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line:
                return json.loads(line)
        raise TimeoutError("No message from worker")

    def _recv_until(self, proc, type_: str, timeout=10) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._recv(proc, timeout=deadline - time.time())
            if msg.get("type") == type_:
                return msg
        raise TimeoutError(f"Never received {type_!r}")

    def test_ready_message(self):
        proc = self._start_worker()
        try:
            msg = self._recv_until(proc, "ready", timeout=10)
            assert "python_version" in msg
            assert "pid" in msg
        finally:
            proc.terminate()

    def test_execute_print(self):
        proc = self._start_worker()
        try:
            self._recv_until(proc, "ready")
            self._send(proc, {"type": "execute", "id": "t1", "code": "print(2 + 2)", "timeout": 10, "stdin_lines": []})
            msg = self._recv_until(proc, "result")
            assert msg["stdout"].strip() == "4"
            assert msg["execution_count"] == 1
        finally:
            proc.terminate()

    def test_execute_return_value(self):
        proc = self._start_worker()
        try:
            self._recv_until(proc, "ready")
            self._send(proc, {"type": "execute", "id": "t2", "code": "1 + 1", "timeout": 10, "stdin_lines": []})
            msg = self._recv_until(proc, "result")
            assert msg["result"] == "2"
        finally:
            proc.terminate()

    def test_variable_persistence(self):
        proc = self._start_worker()
        try:
            self._recv_until(proc, "ready")
            self._send(proc, {"type": "execute", "id": "t3", "code": "x = 42", "timeout": 10, "stdin_lines": []})
            self._recv_until(proc, "result")
            self._send(proc, {"type": "execute", "id": "t4", "code": "x * 2", "timeout": 10, "stdin_lines": []})
            msg = self._recv_until(proc, "result")
            assert msg["result"] == "84"
        finally:
            proc.terminate()

    def test_error_reporting(self):
        proc = self._start_worker()
        try:
            self._recv_until(proc, "ready")
            self._send(proc, {"type": "execute", "id": "t5", "code": "1/0", "timeout": 10, "stdin_lines": []})
            msg = self._recv_until(proc, "error")
            assert msg["ename"] == "ZeroDivisionError"
            assert len(msg["traceback"]) > 0
        finally:
            proc.terminate()

    def test_restart_clears_variables(self):
        proc = self._start_worker()
        try:
            self._recv_until(proc, "ready")
            self._send(proc, {"type": "execute", "id": "t6", "code": "y = 99", "timeout": 10, "stdin_lines": []})
            self._recv_until(proc, "result")
            self._send(proc, {"type": "restart", "id": "r1"})
            self._recv_until(proc, "restarted")
            self._send(proc, {"type": "execute", "id": "t7", "code": "y", "timeout": 10, "stdin_lines": []})
            msg = self._recv_until(proc, "error")
            assert msg["ename"] == "NameError"
        finally:
            proc.terminate()

    def test_stdin_pre_supplied(self):
        proc = self._start_worker()
        try:
            self._recv_until(proc, "ready")
            self._send(proc, {
                "type": "execute", "id": "t8",
                "code": "name = input('Name: ')\nprint(f'Hello {name}')",
                "timeout": 10,
                "stdin_lines": ["Alice"],
            })
            msg = self._recv_until(proc, "result")
            assert "Hello Alice" in msg["stdout"]
        finally:
            proc.terminate()

    def test_inspect(self):
        proc = self._start_worker()
        try:
            self._recv_until(proc, "ready")
            self._send(proc, {"type": "execute", "id": "t9", "code": "z = [1, 2, 3]", "timeout": 10, "stdin_lines": []})
            self._recv_until(proc, "result")
            self._send(proc, {"type": "inspect", "id": "i1"})
            msg = self._recv_until(proc, "inspect_result")
            names = [v["name"] for v in msg["variables"]]
            assert "z" in names
        finally:
            proc.terminate()
