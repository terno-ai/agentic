"""
BenchmarkAgentLoop — AgentLoop subclass for SWE-bench runs.

Two feedback layers on top of the base agent loop:

  Layer 1 (always on): ValidatingEdit/WriteTool — py_compile after every
    Python file edit so syntax errors are caught in the same turn.

  Layer 2 (opt-in):  After each full LLM iteration, run the instance's
    failing tests and inject the result as a user message. The agent reads
    the failure and retries, up to max_feedback_rounds times.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from agentic.core.agent import AgentLoop
from agentic.core.config import ConfigManager
from benchmarks.swebench.validating_tools import ValidatingEditTool, ValidatingWriteTool


MAX_TEST_OUTPUT_CHARS = 3_000
TEST_TIMEOUT_S = 60


class BenchmarkAgentLoop(AgentLoop):
    def __init__(
        self,
        config: ConfigManager,
        fail_tests: list[str],
        repo_dir: Path,
        enable_test_feedback: bool = True,
        max_feedback_rounds: int = 3,
        **kwargs: Any,
    ):
        super().__init__(config=config, **kwargs)
        self._fail_tests = fail_tests
        self._repo_dir = repo_dir
        self._enable_test_feedback = enable_test_feedback
        self._max_feedback_rounds = max_feedback_rounds

    def _setup_tools(self) -> None:
        """Register all normal tools, then swap in the validating file tools."""
        super()._setup_tools()
        # Override standard Edit/Write with syntax-validating versions
        self._tool_registry.register(ValidatingEditTool())
        self._tool_registry.register(ValidatingWriteTool())

    async def run_once(self, prompt: str) -> str:
        """
        Override to add the test-feedback loop around the base agent loop.
        Layer 1 (py_compile) is transparent — it's baked into the tools.
        Layer 2 (test feedback) is applied here between LLM iterations.
        """
        self._conversation.add_user(prompt)

        for round_num in range(self._max_feedback_rounds):
            # Run the base inner loop for one LLM→tools→LLM pass
            result = await self._agent_loop()

            if not self._enable_test_feedback or not self._fail_tests:
                return result

            # Run the failing tests and see where we stand
            passed, feedback_msg = await self._run_failing_tests()

            if passed:
                return result  # done — tests green

            # Tests still failing: inject feedback and let the agent try again
            if round_num < self._max_feedback_rounds - 1:
                self._conversation.add_user(feedback_msg)
            # On the last round just return whatever we have

        return self._conversation.last_assistant_text()

    async def _run_failing_tests(self) -> tuple[bool, str]:
        """
        Run the instance's FAIL_TO_PASS tests.
        Returns (all_passed, feedback_message).
        """
        if not self._fail_tests:
            return True, ""

        # Try to detect test framework from the test IDs
        sample = self._fail_tests[0]
        if "::" in sample:
            runner, test_args = self._build_pytest_cmd(self._fail_tests)
        else:
            runner, test_args = self._build_unittest_cmd(self._fail_tests)

        cmd = f"{runner} {test_args} 2>&1 | tail -50"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=self._repo_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT_S)
            output = stdout.decode(errors="replace").strip()
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            return False, (
                "[Test feedback] Tests timed out after "
                f"{TEST_TIMEOUT_S}s. Your fix may have introduced an infinite loop "
                "or the test environment is slow. Reconsider your approach."
            )
        except Exception as e:
            return False, f"[Test feedback] Could not run tests: {e}"

        if len(output) > MAX_TEST_OUTPUT_CHARS:
            output = output[-MAX_TEST_OUTPUT_CHARS:]
            output = "(... truncated ...)\n" + output

        passed = exit_code == 0

        if passed:
            return True, ""

        msg = (
            "[Test feedback — tests still failing]\n\n"
            f"```\n{output}\n```\n\n"
            "The failing tests listed above did not pass after your edit. "
            "Read the error carefully, reconsider the root cause, and try again."
        )
        return False, msg

    @staticmethod
    def _build_pytest_cmd(tests: list[str]) -> tuple[str, str]:
        # Use first 3 failing tests to keep the run fast
        test_ids = " ".join(tests[:3])
        return "python -m pytest", f"{test_ids} -x --tb=short -q"

    @staticmethod
    def _build_unittest_cmd(tests: list[str]) -> tuple[str, str]:
        test_ids = " ".join(tests[:3])
        return "python -m unittest", test_ids
