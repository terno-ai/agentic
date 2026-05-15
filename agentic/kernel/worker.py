#!/usr/bin/env python3
"""
Agentic Python Kernel Worker.

Self-contained subprocess: NO imports from the agentic package.
Communicates with KernelManager via JSON-line messages on stdin/stdout.
Subprocess stderr is reserved for fatal protocol errors only.

Protocol summary (→ = agent sends, ← = worker sends):
  → {"type":"execute", "id":"...", "code":"...", "timeout":60, "stdin_lines":[]}
  ← {"type":"result"|"error"|"timeout", "id":"...", "stdout":"...", ...}
  ← {"type":"stdin_request", "id":"...", "prompt":"..."}   (mid-execution)
  → {"type":"stdin_reply", "id":"...", "text":"..."}
  ← {"type":"memory_warning", ...}   (from monitor thread, mid-execution)
  ← {"type":"oom_error", ...}        (kernel self-terminates after this)
  ← {"type":"heartbeat", "ts":..., "memory_mb":...}
  → {"type":"restart"}  → ← {"type":"restarted"}
  → {"type":"inspect"}  → ← {"type":"inspect_result"}
  → {"type":"interrupt"}
  → {"type":"config", "settings":{...}}
  → {"type":"shutdown"}
"""

from __future__ import annotations

import ast
import builtins
import ctypes
import io
import json
import os
import queue
import signal
import sys
import threading
import time
import traceback as tb_module

# ── Optional psutil for memory monitoring ────────────────────────────────────
try:
    import psutil as _psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

# ── Global state ─────────────────────────────────────────────────────────────
_globals: dict = {}                         # persistent kernel namespace
_execution_count: int = 0
_current_exec_id: str | None = None
_stdin_queue: queue.Queue = queue.Queue()   # pre-supplied + interactive stdin
_config: dict = {
    "memory_limit_mb": 512,
    "heartbeat_interval_s": 5,
    "max_output_chars": 10_000,
}
_exec_thread: threading.Thread | None = None
_done_event: threading.Event = threading.Event()
_lock = threading.Lock()


# ── Output helpers ────────────────────────────────────────────────────────────

_real_stdout = sys.stdout
_real_stderr = sys.stderr


def _send(msg: dict) -> None:
    line = json.dumps(msg, default=str) + "\n"
    _real_stdout.write(line)
    _real_stdout.flush()


def _memory_mb() -> float:
    if _PSUTIL:
        try:
            return _psutil.Process().memory_info().rss / 1_000_000
        except Exception:
            pass
    return 0.0


# ── Custom input() that routes through the protocol ──────────────────────────

def _kernel_input(prompt="") -> str:
    _send({"type": "stdin_request", "id": _current_exec_id, "prompt": str(prompt)})
    try:
        text = _stdin_queue.get(timeout=300)
        return text.rstrip("\n")
    except queue.Empty:
        raise TimeoutError("stdin: no reply after 300 s")


builtins.input = _kernel_input


# ── AST-based result capture ──────────────────────────────────────────────────

def _exec_capturing_result(code: str, globs: dict):
    """
    Execute code. If the last statement is an expression, capture its value
    by rewriting the AST (avoids double-evaluation and side effects).

    Returns (result_value, has_result).
    """
    tree = ast.parse(code, "<kernel>", "exec")

    has_result = False
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        # Replace last expression with  __kr__ = <expr>
        assign = ast.Assign(
            targets=[ast.Name(id="__kr__", ctx=ast.Store())],
            value=tree.body[-1].value,
        )
        ast.copy_location(assign, tree.body[-1])
        ast.fix_missing_locations(assign)
        tree.body[-1] = assign
        has_result = True

    exec(compile(tree, "<kernel>", "exec"), globs)  # noqa: S102

    if has_result:
        val = globs.pop("__kr__", None)
        return val, True
    return None, False


# ── Execution handler ─────────────────────────────────────────────────────────

def _handle_execute(msg: dict) -> None:
    global _execution_count, _current_exec_id, _exec_thread, _done_event

    exec_id = msg["id"]
    code = msg.get("code", "")
    timeout = msg.get("timeout", _config.get("default_timeout_s", 60))
    stdin_lines = msg.get("stdin_lines", [])
    max_chars = _config.get("max_output_chars", 10_000)

    with _lock:
        _current_exec_id = exec_id
        _execution_count += 1
        count = _execution_count

    # Pre-supply stdin
    for line in stdin_lines:
        _stdin_queue.put(line if line.endswith("\n") else line + "\n")

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    result_container: list = [None, False, None]  # [value, has_result, exc]
    _done_event = threading.Event()

    def _run():
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout_buf, stderr_buf
        try:
            val, has = _exec_capturing_result(code, _globals)
            result_container[0] = val
            result_container[1] = has
        except Exception as exc:
            result_container[2] = exc
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            _done_event.set()

    _exec_thread = threading.Thread(target=_run, name="kernel-exec", daemon=True)
    t0 = time.monotonic()
    _exec_thread.start()

    timed_out = False
    if timeout and timeout > 0:
        if not _done_event.wait(timeout=timeout):
            timed_out = True
            # Inject KeyboardInterrupt into the execution thread
            _interrupt_thread(_exec_thread)
            _done_event.wait(timeout=5)
    else:
        _done_event.wait()

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    mem = _memory_mb()

    stdout_text, stderr_text = _truncate_combined(
        stdout_buf.getvalue(), stderr_buf.getvalue(), max_chars
    )

    if timed_out:
        _send({
            "type": "timeout",
            "id": exec_id,
            "timeout_s": timeout,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "execution_count": count,
            "duration_ms": elapsed_ms,
            "memory_mb": mem,
            "message": f"Execution timed out after {timeout}s. Use interrupt or restart.",
        })
        return

    exc = result_container[2]
    if exc is not None:
        lines = tb_module.format_exception(type(exc), exc, exc.__traceback__)
        _send({
            "type": "error",
            "id": exec_id,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "ename": type(exc).__name__,
            "evalue": str(exc),
            "traceback": "".join(lines).splitlines(),
            "execution_count": count,
            "duration_ms": elapsed_ms,
            "memory_mb": mem,
        })
    else:
        val = result_container[0]
        has = result_container[1]
        _send({
            "type": "result",
            "id": exec_id,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "result": _safe_repr(val) if has else None,
            "execution_count": count,
            "duration_ms": elapsed_ms,
            "memory_mb": mem,
        })


def _interrupt_thread(thread: threading.Thread) -> None:
    try:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread.ident),
            ctypes.py_object(KeyboardInterrupt),
        )
    except Exception:
        pass


def _truncate(text: str, limit: int) -> str:
    """Keep the TAIL of text — the most recent output is almost always more useful."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return f"... ({dropped:,} chars omitted) ...\n" + text[-limit:]


def _truncate_combined(stdout: str, stderr: str, limit: int) -> tuple[str, str]:
    """
    Apply a single character budget across stdout + stderr, keeping the tail of each.
    Allocates up to half the budget per stream; if one is short the other gets the rest.
    """
    if len(stdout) + len(stderr) <= limit:
        return stdout, stderr

    half = limit // 2
    # First pass: cap each at half
    out = _truncate(stdout, half)
    err = _truncate(stderr, half)

    # Second pass: donate unused budget from the shorter stream to the longer
    unused = half - len(err)
    if unused > 0 and len(stdout) > half:
        out = _truncate(stdout, half + unused)

    unused = half - len(out)
    if unused > 0 and len(stderr) > half:
        err = _truncate(stderr, half + unused)

    return out, err


def _safe_repr(val) -> str:
    try:
        r = repr(val)
        limit = 1_000
        if len(r) <= limit:
            return r
        dropped = len(r) - limit
        return f"... ({dropped:,} chars omitted) ...\n" + r[-limit:]
    except Exception:
        return f"<{type(val).__name__}>"


# ── Restart ───────────────────────────────────────────────────────────────────

def _handle_restart(msg: dict) -> None:
    global _execution_count
    _globals.clear()
    _stdin_queue.queue.clear()
    _execution_count = 0
    _init_globals()
    _send({"type": "restarted", "id": msg.get("id"), "message": "Kernel restarted. All variables cleared."})


# ── Inspect ───────────────────────────────────────────────────────────────────

def _handle_inspect(msg: dict) -> None:
    variables = []
    skip = {"__builtins__", "__name__", "__doc__", "__package__",
             "__loader__", "__spec__", "__kr__"}
    for name, val in list(_globals.items()):
        if name in skip or name.startswith("__"):
            continue
        type_name = type(val).__name__
        try:
            if hasattr(val, "shape"):
                r = f"shape={val.shape}, dtype={getattr(val, 'dtype', '?')}"
            elif hasattr(val, "__len__"):
                r = f"{type_name}(len={len(val)})"
            else:
                r = repr(val)[:120]
        except Exception:
            r = f"<{type_name}>"
        try:
            size_mb = round(sys.getsizeof(val) / 1_000_000, 4)
        except Exception:
            size_mb = 0.0
        variables.append({"name": name, "type": type_name, "repr": r, "size_mb": size_mb})

    _send({
        "type": "inspect_result",
        "id": msg.get("id"),
        "variables": variables,
        "memory_mb": _memory_mb(),
        "execution_count": _execution_count,
        "python_version": sys.version,
    })


# ── Background threads ────────────────────────────────────────────────────────

def _heartbeat_loop() -> None:
    interval = _config.get("heartbeat_interval_s", 5)
    while True:
        time.sleep(interval)
        _send({"type": "heartbeat", "ts": time.time(), "memory_mb": _memory_mb()})


def _memory_monitor_loop() -> None:
    warned = False
    while True:
        time.sleep(2)
        limit = _config.get("memory_limit_mb", 0)
        if not limit:
            continue
        mem = _memory_mb()
        if mem > limit:
            _send({
                "type": "oom_error",
                "id": _current_exec_id,
                "memory_mb": mem,
                "limit_mb": limit,
                "message": (
                    f"Kernel out of memory ({mem:.0f} MB used, {limit} MB limit). "
                    "Kernel is terminating. All variables will be lost. "
                    "Use smaller data, del unused variables, or increase memory_limit_mb."
                ),
            })
            # Give manager time to read the message
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)
        elif mem > limit * 0.80 and not warned:
            warned = True
            _send({
                "type": "memory_warning",
                "id": _current_exec_id,
                "memory_mb": mem,
                "limit_mb": limit,
                "message": (
                    f"Memory at {mem:.0f} MB / {limit} MB "
                    f"({int(mem / limit * 100)}%). "
                    "Consider `del large_variable` or restarting the kernel."
                ),
            })
        elif mem < limit * 0.70:
            warned = False


# ── Globals initialisation ────────────────────────────────────────────────────

def _init_globals() -> None:
    """Seed the persistent namespace with common imports."""
    safe_exec = lambda code: exec(code, _globals)  # noqa: E731
    safe_exec("import os, sys, math, json, re, datetime, collections, itertools, functools")
    for pkg in [
        "import numpy as np",
        "import pandas as pd",
        "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt",
        "import scipy",
        "import sklearn",
    ]:
        try:
            safe_exec(pkg)
        except Exception:
            pass

    startup = _config.get("startup_code", "")
    if startup:
        try:
            safe_exec(startup)
        except Exception as e:
            _real_stderr.write(f"startup_code error: {e}\n")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    _init_globals()

    # Background threads
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    if _PSUTIL:
        threading.Thread(target=_memory_monitor_loop, daemon=True).start()

    _send({
        "type": "ready",
        "pid": os.getpid(),
        "python_version": sys.version,
        "memory_mb": _memory_mb(),
        "psutil": _PSUTIL,
    })

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as e:
            _real_stderr.write(f"protocol error: {e}\n")
            continue

        msg_type = msg.get("type")

        if msg_type == "execute":
            _handle_execute(msg)
        elif msg_type == "restart":
            _handle_restart(msg)
        elif msg_type == "inspect":
            _handle_inspect(msg)
        elif msg_type == "interrupt":
            if _exec_thread and _exec_thread.is_alive():
                _interrupt_thread(_exec_thread)
            _send({"type": "interrupted", "id": msg.get("id"), "message": "Interrupt sent."})
        elif msg_type == "stdin_reply":
            _stdin_queue.put(msg.get("text", "\n"))
        elif msg_type == "config":
            _config.update(msg.get("settings", {}))
        elif msg_type == "shutdown":
            sys.exit(0)


if __name__ == "__main__":
    main()
