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

    # Build a short test command hint
    if fail_tests:
        sample = fail_tests[0]
        # Convert unittest dotted path to pytest -k expression
        parts = sample.rsplit(".", 1)
        test_hint = f"python -m pytest {parts[-1] if len(parts) > 1 else sample} -x 2>&1 | tail -30"
    else:
        test_hint = "python -m pytest <test_path> -x 2>&1 | tail -30"

    prompt = f"""You are fixing a bug in the `{repo}` repository.
The repository is already checked out and ready at: {repo_dir}

## Issue
{problem}{hint_section}

## Failing tests (must pass after your fix)
{test_cmds or '(see issue above)'}

## Step-by-step instructions

1. **Read the issue** carefully — understand the reported traceback or behaviour.

2. **Locate the bug** in the library source code using grep/find:
   `grep -r "keyword" {repo_dir}/src_package/ --include="*.py" -l`

3. **Read** the relevant source files around the reported traceback line numbers.

4. **Optionally reproduce** the failure (skip if it would take long to set up):
   `cd {repo_dir} && {test_hint}`

5. **Fix the bug** — edit the minimum number of existing source lines needed.

6. **Verify syntax** after each edit:
   `python -m py_compile <edited_file.py> && echo OK`
   If it fails, fix the syntax error before moving on.

7. **Stop** and report what you changed and why.

## Hard constraints — read carefully
- **ONLY edit existing library/source files.** The fix is always in the library code.
- **Do NOT create any new files** — not settings files, not conftest.py, not helpers.
- **Do NOT modify or create test files** — the tests themselves are correct.
- **Do NOT install packages** or change dependencies.
- **Do NOT add imports or functions** unless strictly required by the fix.
- Keep the diff as small as possible. One focused change is almost always enough.
- After every Edit call, verify the file still parses: `python -m py_compile <file>`
"""
    return prompt
