"""Load SWE-bench Lite instances from HuggingFace."""

from __future__ import annotations

from typing import Any


DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
SPLIT = "test"


def load_instances(
    limit: int | None = None,
    instance_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Load SWE-bench Lite test instances.

    Args:
        limit: Max number of instances (None = all 300).
        instance_ids: Load only these specific instance IDs.

    Returns:
        List of instance dicts, each with keys:
          instance_id, repo, base_commit, problem_statement,
          hints_text, FAIL_TO_PASS, PASS_TO_PASS, test_patch, patch
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit(
            "Missing dependency: pip install 'agentic[benchmark]'\n"
            "or: pip install datasets swebench tqdm tabulate"
        )

    print(f"Loading {DATASET_NAME} ({SPLIT} split)...")
    ds = load_dataset(DATASET_NAME, split=SPLIT)

    instances: list[dict[str, Any]] = list(ds)

    if instance_ids:
        instances = [i for i in instances if i["instance_id"] in instance_ids]

    if limit:
        instances = instances[:limit]

    return instances


def instance_summary(instance: dict[str, Any]) -> str:
    repo = instance["repo"]
    iid = instance["instance_id"]
    fail = len(instance.get("FAIL_TO_PASS", []))
    return f"{iid} ({repo}, {fail} failing test(s))"
