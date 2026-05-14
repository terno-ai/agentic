#!/usr/bin/env python3
"""
Run the agentic agent on SWE-bench Lite and produce a predictions file
that can be scored with the official SWE-bench evaluator.

Usage:
  # Run all 300 instances (expensive — ~$100-300 in API calls)
  python benchmarks/run_swebench.py

  # Smoke-test with 5 instances
  python benchmarks/run_swebench.py --limit 5

  # Run specific instances
  python benchmarks/run_swebench.py --ids django__django-11099,sympy__sympy-20049

  # Use OpenAI instead of Anthropic
  python benchmarks/run_swebench.py --provider openai --model gpt-4o

  # Parallel workers (default 1 to avoid rate limits)
  python benchmarks/run_swebench.py --workers 4 --limit 20

After the run, score the predictions with the official evaluator:
  pip install swebench
  python -m swebench.harness.run_evaluation \\
    --dataset_name princeton-nlp/SWE-bench_Lite \\
    --predictions_path <output_dir>/predictions.jsonl \\
    --max_workers 4 \\
    --run_id agentic-run-1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# Load .env from repo root if present (handles `export KEY=value` syntax)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)
    except ImportError:
        # Fallback: parse manually
        for line in _env_file.read_text().splitlines():
            line = line.strip().removeprefix("export ").strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

# Allow running from repo root without installing
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.swebench.loader import load_instances, instance_summary
from benchmarks.swebench.runner import run_instance, InstanceResult
from benchmarks.swebench.report import print_summary, write_predictions_jsonl


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run agentic on SWE-bench Lite")
    p.add_argument("--limit", type=int, default=None,
                   help="Max instances to run (default: all 300)")
    p.add_argument("--ids", type=str, default=None,
                   help="Comma-separated list of instance IDs to run")
    p.add_argument("--model", type=str, default="claude-sonnet-4-6",
                   help="Model to use (default: claude-sonnet-4-6)")
    p.add_argument("--provider", type=str, default="",
                   help="Provider: anthropic or openai (auto-detected from model if blank)")
    p.add_argument("--workers", type=int, default=1,
                   help="Concurrent instances (default 1; increase carefully to avoid rate limits)")
    p.add_argument("--timeout", type=int, default=600,
                   help="Per-instance agent timeout in seconds (default 600)")
    p.add_argument("--output-dir", type=str, default=None,
                   help="Directory to write results (default: benchmarks/results/<timestamp>)")
    p.add_argument("--keep-repos", action="store_true",
                   help="Keep cloned repos after each run (default: delete to save disk)")
    p.add_argument("--resume", action="store_true",
                   help="Skip instances that already have a result file in output-dir")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would run without calling the API")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    # Resolve output directory
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_slug = args.model.replace("/", "-").replace(":", "-")
    output_dir = Path(args.output_dir or f"benchmarks/results/{ts}_{model_slug}")
    output_dir.mkdir(parents=True, exist_ok=True)
    repos_dir = output_dir / "repos"
    repos_dir.mkdir(exist_ok=True)

    # Load instances
    instance_ids = [i.strip() for i in args.ids.split(",")] if args.ids else None
    instances = load_instances(limit=args.limit, instance_ids=instance_ids)
    print(f"Loaded {len(instances)} instance(s) from SWE-bench Lite")

    # Filter already-done if resuming
    if args.resume:
        done = {f.stem for f in output_dir.glob("*.json")}
        instances = [i for i in instances if i["instance_id"] not in done]
        print(f"Resuming: {len(instances)} instance(s) remaining")

    if args.dry_run:
        print("\n[DRY RUN] Would process:")
        for inst in instances:
            print(f"  {instance_summary(inst)}")
        return

    # Warn about cost
    cost_note = _estimate_cost(len(instances), args.model)
    print(f"\n{cost_note}")
    if len(instances) > 5:
        answer = input("Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    print(f"\nOutput directory : {output_dir}")
    print(f"Model            : {args.model}  (provider: {args.provider or 'auto'})")
    print(f"Workers          : {args.workers}")
    print(f"Timeout          : {args.timeout}s per instance\n")

    results: list[InstanceResult] = []
    semaphore = asyncio.Semaphore(args.workers)

    async def run_with_semaphore(instance: dict) -> InstanceResult:
        async with semaphore:
            iid = instance["instance_id"]
            result_path = output_dir / f"{iid}.json"

            # Skip if already done
            if args.resume and result_path.exists():
                data = json.loads(result_path.read_text())
                return InstanceResult(**data)

            print(f"  → {iid}")
            result = await run_instance(
                instance=instance,
                repos_dir=repos_dir,
                model=args.model,
                provider=args.provider,
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
                timeout_s=args.timeout,
                keep_repo=args.keep_repos,
            )

            # Save individual result immediately (safe to resume)
            result_path.write_text(json.dumps(asdict(result), indent=2))

            status_icon = "✓" if result.model_patch.strip() else "○"
            err = f" ✗ {result.error[:60]}" if result.status == "error" else ""
            print(f"    {status_icon} {iid}  ({result.duration_s:.0f}s  {result.input_tokens:,}↑){err}")

            return result

    tasks = [run_with_semaphore(inst) for inst in instances]
    results = await asyncio.gather(*tasks)

    # Write predictions JSONL for the official evaluator
    predictions_path = output_dir / "predictions.jsonl"
    write_predictions_jsonl([asdict(r) for r in results], predictions_path)

    # Print summary
    print_summary([asdict(r) for r in results])

    # Print eval command
    run_id = f"agentic-{model_slug}-{ts}"
    print(f"""
To get the official resolved score, run the SWE-bench evaluator (requires Docker):

  pip install swebench
  python -m swebench.harness.run_evaluation \\
    --dataset_name princeton-nlp/SWE-bench_Lite \\
    --predictions_path {predictions_path} \\
    --max_workers 4 \\
    --run_id {run_id}

Results will be written to: logs/run_evaluation/{run_id}/
""")


def _estimate_cost(n: int, model: str) -> str:
    # Very rough estimates based on typical SWE-bench token usage
    if "gpt-4o" in model:
        cost_per = 0.35   # ~$0.35 per instance (input $2.50/M, output $10/M)
    elif "opus" in model:
        cost_per = 0.80
    elif "sonnet" in model:
        cost_per = 0.25
    elif "haiku" in model:
        cost_per = 0.04
    else:
        cost_per = 0.30

    total = n * cost_per
    return (
        f"Estimated cost: ~${total:.0f} ({n} instances × ~${cost_per:.2f} each)\n"
        f"Estimated time: ~{n * 5 / 60:.0f} min (5 min avg per instance)"
    )


if __name__ == "__main__":
    asyncio.run(main())
