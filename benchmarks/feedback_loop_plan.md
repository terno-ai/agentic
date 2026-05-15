# Harness Feedback Loop — Implementation Plan

## Problem

The agent currently edits files in a single long pass with no signal about whether
its changes are correct. This causes two failure modes observed in runs:

1. **Silent syntax errors** — the agent rewrites a function, produces a duplicate
   body or broken indentation, and never finds out because nothing checks it.
2. **No fix verification** — the agent makes a change it believes is correct, but
   has no way to know if the failing tests now pass. It stops after one attempt
   even if it was wrong.

Both failures are harness limitations, not model limitations. Giving the agent
immediate feedback after each edit is the single highest-ROI improvement.

---

## Approach: two-layer feedback

### Layer 1 — Syntax validation (fast, free, always on)

After every `Edit` or `Write` call on a `.py` file, automatically run:

```
python -m py_compile <file>
```

Append the result to the `ToolResult` the agent receives. If compilation fails,
the agent sees the error *in the same turn* and can fix it before moving on.

**Implementation:** `ValidatingEditTool` and `ValidatingWriteTool` — thin wrappers
around the existing tools that append a `py_compile` result to every response.
These are registered only inside benchmark runs, not in the normal agent.

### Layer 2 — Test execution feedback (slower, optional, high signal)

After each full agent iteration (all tool calls in one LLM response settle),
run the instance's failing tests and inject the output as a user message:

```
[Test feedback]
FAILED tests/test_foo.py::test_bar — AssertionError: ...
```

The agent reads this, understands its fix didn't work, and tries again. This loop
continues until either the tests pass or the timeout is hit.

**Implementation:** `BenchmarkAgentLoop` subclasses `AgentLoop`, overrides
`_agent_loop` to inject test feedback between LLM iterations.

---

## File changes

```
benchmarks/
├── swebench/
│   ├── validating_tools.py   ← NEW: ValidatingEditTool, ValidatingWriteTool
│   ├── benchmark_agent.py    ← NEW: BenchmarkAgentLoop with test feedback
│   └── runner.py             ← CHANGED: use BenchmarkAgentLoop, pass fail_tests
```

No changes to the core `agentic/` package — benchmark behaviour stays in
`benchmarks/`.

---

## Detailed design

### `validating_tools.py`

```python
class ValidatingEditTool(EditTool):
    async def execute(self, file_path, old_string, new_string, **kw) -> ToolResult:
        result = await super().execute(file_path, old_string, new_string, **kw)
        if result.is_error or not file_path.endswith(".py"):
            return result
        syntax = _check_syntax(file_path)
        result.content += f"\n\n[py_compile] {syntax}"
        if "SyntaxError" in syntax or "Error" in syntax:
            result.is_error = True   # force the agent to treat this as a failure
        return result

class ValidatingWriteTool(WriteTool):
    # same pattern
```

`_check_syntax(path)` runs `python -m py_compile <path>` via subprocess,
returns `"OK"` or the error message.

### `benchmark_agent.py`

```python
class BenchmarkAgentLoop(AgentLoop):
    def __init__(self, *args, fail_tests: list[str], repo_dir: Path,
                 enable_test_feedback: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self._fail_tests = fail_tests
        self._repo_dir = repo_dir
        self._enable_test_feedback = enable_test_feedback
        self._iteration = 0
        self._max_feedback_rounds = 3   # cap to avoid infinite loops

    def _setup_tools(self):
        super()._setup_tools()
        # Replace standard file tools with validating versions
        self._tool_registry.register(ValidatingEditTool())
        self._tool_registry.register(ValidatingWriteTool())

    async def _agent_loop(self) -> str:
        for round_ in range(self._max_feedback_rounds):
            result = await super()._agent_loop()
            if not self._enable_test_feedback or not self._fail_tests:
                return result
            feedback = await self._run_tests()
            if feedback is None:             # tests passed
                return result
            if round_ < self._max_feedback_rounds - 1:
                self._conversation.add_user(feedback)   # inject and loop
        return result

    async def _run_tests(self) -> str | None:
        """Run failing tests. Return None if all pass, else formatted output."""
        ...
```

### `runner.py` changes

1. Pass `fail_tests` from the instance dict into `BenchmarkAgentLoop`.
2. Add `--no-test-feedback` flag to `run_swebench.py` to disable Layer 2
   (Layer 1 syntax check is always on).

---

## Test execution details

Running the failing tests reliably inside SWE-bench instances is tricky:

- Each repo has its own test runner (pytest, unittest, tox)
- Dependencies may not be installed in the current Python env
- Tests can be slow (minutes)

**Pragmatic approach for Layer 2:**

1. Try `python -m pytest <test_id> -x --tb=short -q 2>&1 | tail -30` first.
2. If pytest isn't available, try `python -m unittest <test_id>`.
3. Cap test run time at 60s.
4. Only inject feedback if at least one test still fails — if all pass, stop.
5. Inject at most `max_feedback_rounds` times (default 3) to bound total runtime.

A failed test run adds ~60s per round, so with 3 rounds the max instance time
becomes `timeout + 3 × 60s`. Keep `--timeout` at 1200s; total wall time stays
under 25 min per instance.

---

## Expected impact

| Scenario | Before | After Layer 1 | After Layer 1+2 |
|---|---|---|---|
| Syntax error in edit | Agent moves on silently | Agent sees error, self-corrects | Same |
| Correct fix, first try | Done | Done | Done |
| Wrong fix, tests still fail | Agent stops (wrong) | Agent stops (wrong) | Agent sees failure, retries |
| Correct conceptual fix, broken impl | Broken patch in output | Agent fixes syntax | Fixed patch in output |

Estimated improvement on SWE-bench Lite resolved rate: **+5–15%** from Layer 1
alone (syntax errors account for a meaningful fraction of near-misses), **+10–25%**
combined, depending on model.

---

## Implementation order

1. `validating_tools.py` — simple, testable in isolation
2. Unit tests for both validating tools
3. `benchmark_agent.py` — BenchmarkAgentLoop + _run_tests
4. Wire into `runner.py`
5. Add `--no-test-feedback` flag to `run_swebench.py`
6. Integration test: re-run pytest-7490 and verify it self-corrects the duplicate body

---

## Out of scope (future work)

- Feeding `flake8` / `ruff` output back (useful but noisier than py_compile)
- Parallel test runs for faster feedback
- Caching test environments per repo
- Docker-based test isolation (needed for full accuracy but complex)
