"""
KernelManager — async supervisor for the Python kernel subprocess.

Owns the worker process, reads JSON-line messages, correlates responses
to pending futures, runs a watchdog for unresponsive detection, and
handles sandbox (Docker) integration.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agentic.kernel.config import KernelConfig
from agentic.kernel.result import KernelResult, KernelInspectResult, KernelVariable

if TYPE_CHECKING:
    from agentic.sandbox.docker_sandbox import DockerSandbox

_WORKER_PATH = Path(__file__).parent / "worker.py"


class KernelNotRunning(RuntimeError):
    pass


class KernelBusy(RuntimeError):
    pass


class KernelManager:
    def __init__(
        self,
        config: KernelConfig,
        sandbox: "DockerSandbox | None" = None,
    ):
        self._config = config
        self._sandbox = sandbox
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}          # id → Future[KernelResult]
        self._warnings: dict[str, list[dict]] = {}             # id → [warning msgs]
        self._last_heartbeat: float = 0.0
        self._worker_pid: int | None = None
        self._busy: bool = False
        self._execution_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the kernel worker process."""
        if self._sandbox:
            await self._start_in_sandbox()
        else:
            await self._start_local()

        self._last_heartbeat = time.monotonic()
        self._reader_task = asyncio.create_task(self._read_loop(), name="kernel-reader")
        self._watchdog_task = asyncio.create_task(self._watchdog(), name="kernel-watchdog")

        # Wait for "ready" message (up to 30s)
        ready_future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending["__ready__"] = ready_future
        try:
            await asyncio.wait_for(asyncio.shield(ready_future), timeout=30)
        except asyncio.TimeoutError:
            raise RuntimeError("Kernel failed to start within 30s")

        # Send configuration
        await self._send({
            "type": "config",
            "settings": {
                "memory_limit_mb": self._config.memory_limit_mb,
                "heartbeat_interval_s": self._config.heartbeat_interval_s,
                "max_output_chars": self._config.max_output_chars,
                "default_timeout_s": self._config.default_timeout_s,
                "startup_code": self._config.startup_code,
            },
        })

    async def _start_local(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "python", str(_WORKER_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _start_in_sandbox(self) -> None:
        """Copy worker into the container and run it via docker exec."""
        sandbox = self._sandbox
        container = sandbox.container_name

        # Copy worker script into the container
        result = subprocess.run(
            ["docker", "cp", str(_WORKER_PATH), f"{container}:/tmp/agentic_kernel_worker.py"],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to copy kernel worker to sandbox: {result.stderr.decode()}")

        self._proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", container,
            "python3", "/tmp/agentic_kernel_worker.py",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stop(self) -> None:
        """Gracefully shut down the kernel."""
        if self._proc and self._proc.stdin:
            try:
                await self._send({"type": "shutdown"})
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                pass

        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

        for task in (self._reader_task, self._watchdog_task):
            if task:
                task.cancel()

        # Fail all pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(KernelNotRunning("Kernel stopped"))
        self._pending.clear()

    async def restart(self) -> str:
        """Restart the kernel: clear all variables, preserve the process."""
        exec_id = f"restart-{uuid.uuid4().hex[:8]}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[exec_id] = future
        await self._send({"type": "restart", "id": exec_id})
        result = await asyncio.wait_for(future, timeout=15)
        self._busy = False
        self._execution_count = 0
        return result.message

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        code: str,
        timeout: int | None = None,
        stdin_lines: list[str] | None = None,
    ) -> KernelResult:
        if not self._proc or self._proc.returncode is not None:
            raise KernelNotRunning("Kernel is not running. Call start() first.")
        if self._busy:
            raise KernelBusy("Kernel is already executing code. Wait or interrupt.")

        exec_id = f"exec-{uuid.uuid4().hex[:8]}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[exec_id] = future
        self._warnings[exec_id] = []
        self._busy = True

        try:
            await self._send({
                "type": "execute",
                "id": exec_id,
                "code": code,
                "timeout": timeout if timeout is not None else self._config.default_timeout_s,
                "stdin_lines": stdin_lines or [],
            })
            # Wait with a slightly longer wall-clock timeout than the kernel timeout
            wall_timeout = (timeout or self._config.default_timeout_s or 0)
            wall_timeout = (wall_timeout + 10) if wall_timeout else None
            result = await asyncio.wait_for(future, timeout=wall_timeout)
            self._execution_count = result.execution_count
            return result
        except asyncio.TimeoutError:
            self._pending.pop(exec_id, None)
            return KernelResult(
                kind="timeout",
                message=f"Wall-clock timeout waiting for kernel response.",
            )
        finally:
            self._busy = False

    async def interrupt(self) -> str:
        """Send KeyboardInterrupt to the currently running execution."""
        exec_id = f"int-{uuid.uuid4().hex[:8]}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[exec_id] = future
        await self._send({"type": "interrupt", "id": exec_id})
        try:
            result = await asyncio.wait_for(future, timeout=5)
            return result.message
        except asyncio.TimeoutError:
            return "Interrupt sent (no confirmation received)."

    async def inspect(self) -> KernelInspectResult:
        """List all variables currently in the kernel namespace."""
        exec_id = f"insp-{uuid.uuid4().hex[:8]}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[exec_id] = future
        await self._send({"type": "inspect", "id": exec_id})
        raw = await asyncio.wait_for(future, timeout=10)
        return raw  # returned as-is from _dispatch

    # ------------------------------------------------------------------
    # Protocol reader
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            async for line in self._proc.stdout:
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                await self._dispatch(msg)
        except Exception:
            pass

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")
        exec_id = msg.get("id")

        if msg_type == "ready":
            self._worker_pid = msg.get("pid")
            self._last_heartbeat = time.monotonic()
            fut = self._pending.pop("__ready__", None)
            if fut and not fut.done():
                fut.set_result(msg)

        elif msg_type == "heartbeat":
            self._last_heartbeat = time.monotonic()

        elif msg_type == "memory_warning":
            if exec_id and exec_id in self._warnings:
                self._warnings[exec_id].append(msg)

        elif msg_type in ("result", "error", "timeout", "oom_error"):
            if exec_id and exec_id in self._pending:
                warnings = self._warnings.pop(exec_id, [])
                result = KernelResult.from_msg(msg, warnings)
                fut = self._pending.pop(exec_id)
                if not fut.done():
                    fut.set_result(result)

        elif msg_type in ("restarted", "interrupted"):
            if exec_id and exec_id in self._pending:
                result = KernelResult(kind=msg_type, message=msg.get("message", ""))
                fut = self._pending.pop(exec_id)
                if not fut.done():
                    fut.set_result(result)

        elif msg_type == "inspect_result":
            if exec_id and exec_id in self._pending:
                variables = [
                    KernelVariable(
                        name=v["name"], type=v["type"],
                        repr=v["repr"], size_mb=v["size_mb"],
                    )
                    for v in msg.get("variables", [])
                ]
                result = KernelInspectResult(
                    variables=variables,
                    memory_mb=msg.get("memory_mb", 0.0),
                    execution_count=msg.get("execution_count", 0),
                    python_version=msg.get("python_version", ""),
                )
                fut = self._pending.pop(exec_id)
                if not fut.done():
                    fut.set_result(result)

        elif msg_type == "stdin_request":
            # No pre-supplied stdin and code called input() — return timeout error
            # so the agent learns to always pre-supply stdin.
            if exec_id and exec_id in self._pending:
                # Send empty newline to unblock the kernel
                await self._send({"type": "stdin_reply", "id": exec_id, "text": "\n"})

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    async def _watchdog(self) -> None:
        timeout = self._config.watchdog_timeout_s
        while True:
            await asyncio.sleep(timeout // 2 or 5)
            elapsed = time.monotonic() - self._last_heartbeat
            if elapsed > timeout and self._pending:
                # Unresponsive — fail all pending futures and restart
                unresponsive_msg = KernelResult(
                    kind="unresponsive",
                    message=(
                        f"Kernel unresponsive (no heartbeat for {elapsed:.0f}s). "
                        "It has been restarted automatically. All variables are lost."
                    ),
                )
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_result(unresponsive_msg)
                self._pending.clear()
                self._warnings.clear()
                self._busy = False
                await self._force_restart()

    async def _force_restart(self) -> None:
        """Kill and respawn the worker process."""
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._reader_task:
            self._reader_task.cancel()

        try:
            if self._sandbox:
                await self._start_in_sandbox()
            else:
                await self._start_local()
            self._last_heartbeat = time.monotonic()
            self._reader_task = asyncio.create_task(self._read_loop(), name="kernel-reader")
            # Wait briefly for ready
            await asyncio.sleep(2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send(self, msg: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise KernelNotRunning("Kernel not running")
        line = (json.dumps(msg) + "\n").encode()
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def execution_count(self) -> int:
        return self._execution_count
