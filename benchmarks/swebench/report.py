"""Generate a readable report from benchmark results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_results(results_dir: Path) -> list[dict[str, Any]]:
    results = []
    for f in sorted(results_dir.glob("*.json")):
        if f.name == "predictions.jsonl":
            continue
        try:
            results.append(json.loads(f.read_text()))
        except Exception:
            pass
    return results


def print_summary(results: list[dict[str, Any]], resolved_ids: set[str] | None = None) -> None:
    total = len(results)
    if total == 0:
        print("No results yet.")
        return

    success = [r for r in results if r["status"] == "success"]
    errors  = [r for r in results if r["status"] == "error"]
    patched = [r for r in results if r["model_patch"].strip()]

    total_in  = sum(r.get("input_tokens",  0) for r in results)
    total_out = sum(r.get("output_tokens", 0) for r in results)
    total_dur = sum(r.get("duration_s",    0) for r in results)

    print("\n" + "=" * 60)
    print("SWE-bench Lite — Agentic Results")
    print("=" * 60)
    print(f"  Instances run    : {total}")
    print(f"  Agent succeeded  : {len(success)} ({pct(len(success), total)})")
    print(f"  Produced a patch : {len(patched)} ({pct(len(patched), total)})")
    print(f"  Errors / timeouts: {len(errors)}")
    print(f"  Total tokens     : {total_in:,} in  /  {total_out:,} out")
    print(f"  Total wall time  : {total_dur / 60:.1f} min")

    if resolved_ids is not None:
        resolved = len(resolved_ids)
        print(f"\n  ✓ Resolved       : {resolved} / {total}  ({pct(resolved, total)})")
    else:
        print(
            "\n  Resolved rate: run the official evaluator to get the final score.\n"
            "  See the eval command printed at the end of the run."
        )

    if errors:
        print(f"\n  Failed instances:")
        for r in errors[:10]:
            print(f"    {r['instance_id']}: {r['error'][:80]}")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more")

    print("=" * 60)


def load_eval_results(eval_output_dir: Path) -> set[str]:
    """Parse the swebench evaluation output to get resolved instance IDs."""
    resolved = set()
    report_file = eval_output_dir / "results.json"
    if report_file.exists():
        data = json.loads(report_file.read_text())
        resolved = set(data.get("resolved", []))
    return resolved


def pct(n: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{n / total * 100:.1f}%"


def write_predictions_jsonl(results: list[dict[str, Any]], output_path: Path) -> None:
    """Write the predictions file consumed by the SWE-bench evaluator."""
    lines = []
    for r in results:
        lines.append(json.dumps({
            "instance_id": r["instance_id"],
            "model_patch": r["model_patch"],
            "model_name_or_path": r["model_name_or_path"],
        }))
    output_path.write_text("\n".join(lines) + "\n")
    print(f"Predictions written to: {output_path}")
