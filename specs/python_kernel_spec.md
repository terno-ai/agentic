# Python Kernel Feature — Detailed Specification

## Overview

Add a persistent Python kernel to agentic — a long-running interpreter that retains
variables and state across executions, captures stdout/stderr separately, handles stdin,
detects memory overflow, and recovers from hangs. Modelled after IPython/Jupyter kernels
but implemented as a lightweight subprocess with a JSON-line protocol, so it works with
or without Jupyter and integrates cleanly with the sandbox.

---

## Motivation

Without a kernel, the agent must:
1. Write code to a `.py` file
2. Run it with `Bash`
3. Lose all computed state after the process exits
4. Repeat for every iteration

This is painful for data science work where:
- DataFrames take seconds to load — reloading on each cell is wasteful
- Intermediate results (models, arrays) need to survive between steps
- Interactive exploration requires tight read-eval-print loops
- Matplotlib figures should be captured, not just printed

A persistent kernel solves all of this.

---

## Architecture

```
AgentLoop
    │
    ▼
KernelTool  (new Tool)
    │   action: execute / restart / inspect / interrupt
    │
    ▼
KernelManager  (agentic/kernel/manager.py)
    │   owns the kernel process, handles protocol, tracks state
    │
    ├── start()   → spawns KernelProcess
    ├── execute() → sends code, waits for result
    ├── restart() → kills + respawns KernelProcess
    └── stop()    → graceful shutdown
    │
    ▼
KernelProcess  (agentic/kernel/worker.py — runs as subprocess)
    │   persistent Python interpreter loop
    │   persistent globals dict (survives between executions)
    │   captures stdout / stderr per execution
    │   monitors memory
    │   handles stdin requests
    └── communicates via JSON-line messages on stdin/stdout
```

### Why subprocess, not embedding?

- **Isolation** — a crash or infinite loop in user code can't kill the agent process
- **Restart** — just kill and respawn the subprocess; no GC headaches
- **Memory limits** — easy to apply `ulimit` or Docker memory limits to the subprocess
- **Sandbox compatibility** — can run inside the Docker sandbox the same way

---

## Wire Protocol

Newline-delimited JSON on the subprocess's stdin (agent → kernel) and stdout
(kernel → agent). Stderr of the subprocess is reserved for protocol errors only.

### Agent → Kernel

#### Execute request
```json
{
  "type": "execute",
  "id": "exec-001",
  "code": "import pandas as pd\ndf = pd.read_csv('data.csv')\ndf.head()",
  "timeout": 60,
  "stdin_lines": []
}
```

- `id` — unique per execution (for correlation)
- `timeout` — seconds before the kernel sends a timeout error (0 = no limit)
- `stdin_lines` — pre-supplied stdin answers (used when the agent already knows the input)

#### Stdin response (when kernel requests input mid-execution)
```json
{"type": "stdin_reply", "id": "exec-001", "text": "42\n"}
```

#### Restart request
```json
{"type": "restart", "id": "restart-001"}
```

#### Interrupt request
```json
{"type": "interrupt", "id": "int-001"}
```

#### Inspect request
```json
{"type": "inspect", "id": "insp-001"}
```

#### Shutdown request
```json
{"type": "shutdown"}
```

---

### Kernel → Agent

#### Execute result (success)
```json
{
  "type": "result",
  "id": "exec-001",
  "stdout": "   col1  col2\n0     1     a\n1     2     b\n",
  "stderr": "",
  "result": "<class 'pandas.core.frame.DataFrame'>",
  "result_repr": "   col1  col2\n0     1     a\n",
  "execution_count": 3,
  "duration_ms": 142,
  "memory_mb": 48.2
}
```

- `result` — `repr()` of the last expression value (None if statement)
- `result_repr` — pretty-printed version (e.g. DataFrame HTML stripped to text)
- `memory_mb` — RSS of the kernel process after execution

#### Execute result (error)
```json
{
  "type": "error",
  "id": "exec-001",
  "stdout": "",
  "stderr": "",
  "ename": "ZeroDivisionError",
  "evalue": "division by zero",
  "traceback": [
    "Traceback (most recent call last):",
    "  File \"<kernel>\", line 1, in <module>",
    "    1/0",
    "ZeroDivisionError: division by zero"
  ],
  "execution_count": 3,
  "duration_ms": 5,
  "memory_mb": 22.1
}
```

#### Stdin request (kernel asking for input mid-execution)
```json
{
  "type": "stdin_request",
  "id": "exec-001",
  "prompt": "Enter a number: "
}
```

Agent must reply with a `stdin_reply` message before the kernel can continue.

#### Memory warning
```json
{
  "type": "memory_warning",
  "id": "exec-001",
  "memory_mb": 480.0,
  "limit_mb": 512.0,
  "message": "Kernel memory usage at 94% of limit (480 MB / 512 MB). Consider deleting large variables or restarting the kernel."
}
```

Emitted mid-execution when usage crosses 80% of limit. Execution continues.

#### Out-of-memory error
```json
{
  "type": "oom_error",
  "id": "exec-001",
  "memory_mb": 515.0,
  "limit_mb": 512.0,
  "message": "Kernel killed: out of memory (515 MB used, 512 MB limit). The kernel has been restarted. All variables have been lost."
}
```

Kernel auto-restarts after OOM. Agent receives this message on the next read.

#### Timeout error
```json
{
  "type": "timeout",
  "id": "exec-001",
  "timeout_s": 60,
  "message": "Execution timed out after 60s. The cell was interrupted. Use kernel.interrupt() to cancel, or kernel.restart() to reset."
}
```

#### Inspect result
```json
{
  "type": "inspect_result",
  "id": "insp-001",
  "variables": [
    {"name": "df", "type": "DataFrame", "repr": "(1000, 5)", "size_mb": 0.4},
    {"name": "model", "type": "RandomForestClassifier", "repr": "RandomForestClassifier()", "size_mb": 12.1},
    {"name": "X_train", "type": "ndarray", "repr": "shape=(800, 5), dtype=float64", "size_mb": 0.03}
  ],
  "memory_mb": 48.2,
  "execution_count": 7,
  "python_version": "3.11.4"
}
```

#### Restart complete
```json
{"type": "restarted", "id": "restart-001", "message": "Kernel restarted. All variables cleared."}
```

#### Heartbeat (emitted every 5s while idle)
```json
{"type": "heartbeat", "ts": 1715702400.0, "memory_mb": 12.1}
```

Used by `KernelManager` to detect unresponsive kernels.

---

## KernelProcess (worker.py)

The kernel subprocess. A minimal Python interpreter loop.

### Startup
- Creates a persistent `_globals` dict seeded with useful imports:
  ```python
  import os, sys, json, math, re, datetime
  # if available:
  import numpy as np
  import pandas as pd
  import matplotlib
  matplotlib.use("Agg")  # non-interactive backend
  import matplotlib.pyplot as plt
  ```
- Installs a custom `input()` builtin that sends a `stdin_request` message
  and blocks until a `stdin_reply` arrives
- Starts a background memory monitor thread (checks every 2s)

### Execution loop
```python
for line in sys.stdin:
    msg = json.loads(line)
    if msg["type"] == "execute":
        _handle_execute(msg)
    elif msg["type"] == "restart":
        _handle_restart(msg)
    ...
```

### `_handle_execute(msg)`
1. Redirect `sys.stdout` and `sys.stderr` to `StringIO` buffers
2. Record start time and initial memory
3. `exec(compile(code, "<kernel>", "exec"), _globals)` wrapped in try/except
4. Capture the value of the last expression (if it is one) using `ast.parse`
   to detect expression statements
5. Flush buffers, measure memory delta
6. Send `result` or `error` message
7. Clear matplotlib figure queue if any plots were created

### Stdin handling
The custom `input()` override:
```python
def _kernel_input(prompt=""):
    send({"type": "stdin_request", "id": current_id, "prompt": prompt})
    # Block until stdin_reply arrives on a side channel
    reply = _stdin_queue.get(timeout=300)  # 5-minute timeout
    return reply
```

`_stdin_queue` is a `queue.Queue` fed by the main protocol reader thread.

### Memory monitoring
Background thread using `psutil`:
```python
while True:
    rss = psutil.Process().memory_info().rss / 1e6
    if rss > limit_mb:
        send({"type": "oom_error", ...})
        os.kill(os.getpid(), signal.SIGTERM)  # kernel self-terminates
    elif rss > limit_mb * 0.8:
        send({"type": "memory_warning", ...})
    time.sleep(2)
```

### Timeout enforcement
Each execution runs in a thread. The main thread uses `threading.Event` with a timeout:
```python
t = threading.Thread(target=_run_code, args=(code,))
t.start()
if not done_event.wait(timeout=timeout_s):
    # Send SIGINT to interrupt the running thread
    ctypes.pythonapi.PyThreadState_SetAsyncExc(t.ident, ctypes.py_object(KeyboardInterrupt))
    send({"type": "timeout", ...})
```

### Heartbeat
Idle timer — send heartbeat every 5s when not executing.

---

## KernelManager (manager.py)

Owns and supervises the kernel subprocess.

```python
class KernelManager:
    def __init__(self, config: KernelConfig, sandbox: DockerSandbox | None = None):
        self._config = config
        self._sandbox = sandbox   # if set, kernel runs inside Docker
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._execution_count = 0
        self._last_heartbeat = 0.0
        self._stdin_waiters: dict[str, asyncio.Queue] = {}

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def restart(self) -> str: ...
    async def execute(self, code: str, timeout: int = 60, stdin_lines: list[str] = []) -> KernelResult: ...
    async def interrupt(self) -> None: ...
    async def inspect(self) -> KernelInspectResult: ...
    async def provide_stdin(self, exec_id: str, text: str) -> None: ...
    async def is_alive(self) -> bool: ...
    async def _watchdog(self) -> None: ...  # restarts if heartbeat stops
```

### Sandbox integration
When a `DockerSandbox` is provided, the kernel runs inside the container:
```python
if self._sandbox:
    cmd = ["docker", "exec", "-i", sandbox.container_name,
           "python", "/agentic_kernel_worker.py"]
else:
    cmd = [sys.executable, worker_script_path]
```

The worker script is copied into the container on first start.

### Watchdog
`_watchdog` is a background task that:
1. Checks `_last_heartbeat` every 10s
2. If more than 30s since last heartbeat → kernel is unresponsive
3. Sends `KernelResult` with `type=unresponsive` to all pending futures
4. Calls `restart()`

---

## KernelTool (tools/kernel_tool.py)

A single tool with an `action` discriminator. This keeps the tool count low and
lets the LLM reason about the kernel as a unified resource.

```python
class KernelTool(Tool):
    name = "PythonKernel"
    description = """
Execute Python code in a persistent kernel that retains variables between calls.
Use this instead of Bash for Python/data-science work — no file writing needed.

actions:
  execute  — run Python code; stdout, stderr, and the result are returned separately
  restart  — reset the kernel (clears all variables); use after OOM or when state is corrupted
  inspect  — list all variables currently in scope with their types and sizes
  interrupt — cancel a long-running execution (sends KeyboardInterrupt)
"""
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["execute", "restart", "inspect", "interrupt"],
            },
            "code": {
                "type": "string",
                "description": "Python code to execute (required for action=execute)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds for execute (default 60, 0 = no limit)",
                "default": 60,
            },
            "stdin": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Pre-supplied stdin lines for code that calls input()",
            },
        },
        "required": ["action"],
    }
```

### Tool result format

The `ToolResult.content` is structured text:

**Success:**
```
[stdout]
   col1  col2
0     1     a

[result]
<class 'pandas.core.frame.DataFrame'>

[memory] 48.2 MB  [exec #3]  [142ms]
```

**Error:**
```
[stdout]
(empty)

[stderr]
(empty)

[error] ZeroDivisionError: division by zero
  File "<kernel>", line 1, in <module>
    1/0
ZeroDivisionError: division by zero

[memory] 22.1 MB  [exec #3]  [5ms]
```

**Memory warning (included in successful result):**
```
⚠ Memory warning: 480 MB / 512 MB (94%). Consider del large_var or kernel.restart().

[stdout]
...
```

**OOM:**
```
💥 Kernel out of memory (515 MB). All variables lost. Kernel has been restarted.
Reduce data size, delete unused variables (del df), or process data in chunks.
```

**Timeout:**
```
⏱ Execution timed out after 60s. The kernel is still running other code.
Use action="interrupt" to cancel, or action="restart" to reset everything.
```

**Unresponsive:**
```
🔴 Kernel is not responding (no heartbeat for 30s). It has been restarted automatically.
All variables have been lost. This may have been caused by an infinite loop or a crash.
```

---

## KernelConfig

```python
class KernelConfig(BaseModel):
    enabled: bool = False
    memory_limit_mb: int = 512
    default_timeout_s: int = 60
    heartbeat_interval_s: int = 5
    watchdog_timeout_s: int = 30     # unresponsive threshold
    auto_restart_on_oom: bool = True
    startup_code: str = ""           # code run at kernel start (e.g. imports)
    max_output_chars: int = 10_000   # truncate very long stdout
```

Added to `Settings`:
```python
kernel: KernelConfig = Field(default_factory=KernelConfig)
```

---

## AgentLoop integration

When `kernel.enabled` or `--kernel` flag is set:
1. `KernelManager` is created alongside `DockerSandbox`
2. `KernelTool` is registered, `BashTool` still available (for shell ops)
3. System prompt gains a **Kernel environment** section:

```
## Kernel environment
You have a persistent Python kernel. Use PythonKernel(action="execute") for Python code.
Variables survive between calls — define df once, use it in subsequent cells.

Prefer the kernel over Bash for:
- Any Python code
- Data loading and processing (pandas, numpy, sklearn)
- Plotting (matplotlib — figures captured as text summaries)
- Iterative exploration

Use Bash for: shell commands, file management, git, curl, package installation.

If you get an OOM error: delete large variables with `del var` or restart the kernel.
If execution hangs: use action="interrupt", then action="restart" if still unresponsive.
```

4. On agent shutdown: `KernelManager.stop()` is called.

---

## System prompt additions

When kernel is active, the system prompt also explains stdin:

```
## Kernel stdin
If your code calls input(), provide expected answers in the `stdin` parameter:
  PythonKernel(action="execute", code="name = input('Name: ')\nprint(name)", stdin=["Alice"])
If you don't supply stdin and the code calls input(), you'll receive a stdin_request
and can reply with another tool call.
```

---

## CLI flags

```bash
# Enable kernel
agentic --kernel

# Kernel with sandbox (recommended — kernel runs inside the container)
agentic --sandbox --kernel

# Set memory limit
agentic --kernel --kernel-memory 2048   # 2 GB

# Non-interactive with kernel
agentic run --kernel "load data.csv and show summary statistics"
```

---

## File layout

```
agentic/
└── kernel/
    ├── __init__.py
    ├── config.py          # KernelConfig model
    ├── manager.py         # KernelManager — async supervisor
    ├── worker.py          # Subprocess interpreter (self-contained, no agentic imports)
    ├── result.py          # KernelResult, KernelInspectResult dataclasses
    └── tool.py            # KernelTool (Tool subclass)

tests/unit/
└── test_kernel.py         # Unit tests (mock subprocess)

tests/integration/
└── test_kernel_live.py    # Live kernel tests (real subprocess, skipped in CI without flag)
```

---

## Edge cases and failure modes

| Scenario | Detection | Agent message | Recovery |
|---|---|---|---|
| OOM during execution | Memory monitor thread kills process | `oom_error` with MB info | Auto-restart; agent told to `del` vars |
| Infinite loop / hang | Watchdog: no heartbeat for 30s | `unresponsive` error | Auto-restart |
| Syntax error in code | `SyntaxError` from `compile()` | `error` with traceback | Agent fixes code |
| Unhandled exception | `exec()` raises | `error` with traceback | Agent debugs |
| Execution timeout | `threading.Event.wait()` expires | `timeout` error | Agent can interrupt or restart |
| Kernel process crash (SIGSEGV, etc.) | `asyncio.subprocess` returncode | `crash` error with exit code | Auto-restart |
| stdin deadlock (input() with no reply) | 5-minute stdin timeout in worker | `stdin_timeout` error | Kernel continues without input |
| Docker container stops | Sandbox `stop()` | Next execute raises RuntimeError | Agent told sandbox is down |
| Very large stdout (>10k chars) | Truncation in manager | Truncation notice appended | Agent uses smaller output |
| Import error at startup | Caught in startup code | Warning in first result | Non-fatal; kernel still runs |

---

## Implementation phases

### Phase 1 — Core kernel (MVP)
- `worker.py`: execute loop, stdout/stderr capture, persistent globals, basic error handling
- `manager.py`: start/stop/execute, JSON protocol reader, pending futures
- `KernelTool`: execute and restart actions
- Unit tests with mocked subprocess

### Phase 2 — Robustness
- Heartbeat + watchdog
- Memory monitoring and OOM handling  
- Timeout enforcement (threading)
- `KernelTool`: inspect and interrupt actions

### Phase 3 — Stdin and sandbox
- Custom `input()` override in worker
- `stdin_request` / `stdin_reply` protocol
- Sandbox integration (kernel inside Docker container)
- `--kernel` CLI flag and settings

### Phase 4 — Polish
- Rich output capture (matplotlib figure → ASCII summary via `matplotlib.figure`)
- Startup code configuration
- Integration tests
- System prompt tuning based on real agent usage

---

## Dependencies

| Package | Use | Already in deps? |
|---|---|---|
| `psutil` | Memory monitoring in worker | No — add to optional `kernel` extras |
| `ipython` | Optional: richer reprs | No — optional |

No new required dependencies for Phase 1-2. `psutil` needed for Phase 2.
`ipython` can be used optionally for prettier output but is not required.
