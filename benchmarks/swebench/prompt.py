"""Build the agent prompt for a SWE-bench instance."""

from __future__ import annotations

import json
from typing import Any


def build_prompt(instance: dict[str, Any], repo_dir: str) -> str:
    """
    Construct the task prompt for the agent from a SWE-bench instance.
    The prompt guides the agent through: understand → locate → fix → verify.
    """
    repo = instance["repo"]
    problem = instance["problem_statement"].strip()
    hints = instance.get("hints_text", "").strip()
    fail_tests = instance.get("FAIL_TO_PASS", [])

    # Format failing tests for the agent to run
    if isinstance(fail_tests, str):
        try:
            fail_tests = json.loads(fail_tests)
        except Exception:
            fail_tests = [fail_tests]

    test_cmds = "\n".join(f"  - {t}" for t in fail_tests[:5])
    if len(fail_tests) > 5:
        test_cmds += f"\n  ... ({len(fail_tests) - 5} more)"

    hint_section = f"\n## Hints\n{hints}" if hints else ""

    prompt = f"""You are fixing a bug in the `{repo}` repository.

## Issue
{problem}{hint_section}

## Failing tests (must pass after your fix)
{test_cmds or '(see issue above)'}

## Instructions
Work in the repository at: {repo_dir}

Follow this process:
1. **Read the issue** carefully to understand what is broken.
2. **Explore** the repository structure (`ls`, `find`, `grep`) to locate relevant files.
3. **Read** the relevant source files to understand the code.
4. **Run the failing tests** to confirm you can reproduce the failure:
   `python -m pytest {' '.join(fail_tests[:2]) if fail_tests else '<test_path>'} -x 2>&1 | head -50`
5. **Fix the bug** by editing only the source files (do NOT modify test files).
6. **Run the failing tests again** to confirm they now pass.
7. **Stop** — do not add unrelated changes.

Important constraints:
- Edit only library/source files. Do NOT create or modify test files.
- Do not install packages or change dependencies.
- Keep changes minimal and targeted to the bug described in the issue.
- If a test still fails after your fix, reconsider your approach.
"""
    return prompt
