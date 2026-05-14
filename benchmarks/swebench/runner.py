"""Run the agent against a single SWE-bench instance and capture its patch."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from benchmarks.swebench.git_utils import clone_at_commit, get_diff, reset_to_base
from benchmarks.swebench.prompt import build_prompt


@dataclass
class InstanceResult:
    instance_id: str
    model_patch: str        # git diff produced by the agent (empty = no change)
    model_name_or_path: str
    status: str             # "success" | "error" | "skipped"
    error: str = ""
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_prediction(self) -> dict[str, Any]:
        """Format expected by the SWE-bench evaluation harness."""
        return {
            "instance_id": self.instance_id,
            "model_patch": self.model_patch,
            "model_name_or_path": self.model_name_or_path,
        }


async def run_instance(
    instance: dict[str, Any],
    repos_dir: Path,
    model: str,
    provider: str = "",
    api_key: str = "",
    openai_api_key: str = "",
    timeout_s: int = 600,
    keep_repo: bool = False,
) -> InstanceResult:
    """
    Run the agent on one SWE-bench instance.

    Clones the repo at base_commit, runs AgentLoop with the issue prompt,
    captures the resulting git diff, then (optionally) cleans up.
    """
    instance_id = instance["instance_id"]
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    model_tag = f"agentic-{provider or 'auto'}-{model}"

    repo_dir = repos_dir / instance_id
    t0 = time.monotonic()

    # Clone if not already present
    if not repo_dir.exists():
        try:
            await clone_at_commit(repo, base_commit, repo_dir)
        except Exception as e:
            return InstanceResult(
                instance_id=instance_id,
                model_patch="",
                model_name_or_path=model_tag,
                status="error",
                error=f"Clone failed: {e}",
                duration_s=time.monotonic() - t0,
            )
    else:
        # Repo exists — reset to the base commit in case of a previous run
        try:
            reset_to_base(repo_dir, base_commit)
        except Exception:
            pass

    prompt = build_prompt(instance, str(repo_dir))

    try:
        patch, tokens_in, tokens_out = await asyncio.wait_for(
            _run_agent(prompt, repo_dir, model, provider, api_key, openai_api_key),
            timeout=timeout_s,
        )
        status = "success"
        error = ""
    except asyncio.TimeoutError:
        patch = get_diff(repo_dir)
        status = "error"
        error = f"Agent timed out after {timeout_s}s"
        tokens_in = tokens_out = 0
    except Exception as e:
        patch = get_diff(repo_dir)
        status = "error"
        error = traceback.format_exc()
        tokens_in = tokens_out = 0

    if not keep_repo and repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)

    return InstanceResult(
        instance_id=instance_id,
        model_patch=patch,
        model_name_or_path=model_tag,
        status=status,
        error=error,
        duration_s=time.monotonic() - t0,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )


async def _run_agent(
    prompt: str,
    repo_dir: Path,
    model: str,
    provider: str,
    api_key: str,
    openai_api_key: str,
) -> tuple[str, int, int]:
    """Run AgentLoop and return (patch, input_tokens, output_tokens)."""
    import os

    original_cwd = os.getcwd()
    os.chdir(repo_dir)

    try:
        from agentic.core.config import ConfigManager, Settings
        from agentic.core.agent import AgentLoop

        # Load config normally (picks up ~/.agentic/settings.json and env vars for API keys),
        # then override only benchmark-specific settings.
        config = ConfigManager(project_dir=repo_dir)
        base = config.settings

        # Always resolve provider explicitly from model name so benchmark runs
        # are deterministic and don't inherit whatever the user last set globally.
        from agentic.core.config import detect_provider
        resolved_provider = provider or detect_provider(model)

        overrides: dict = {
            "model": model,
            "provider": resolved_provider,
            "auto_memory": False,       # Don't persist memories across benchmark runs
            "max_tool_iterations": 80,  # Allow more iterations for complex bugs
            "context_summarize_threshold": 60_000,
        }
        # Explicit keys passed to runner win; otherwise fall through to what
        # the base config already loaded from env vars / settings files.
        if api_key:
            overrides["api_key"] = api_key
        if openai_api_key:
            overrides["openai_api_key"] = openai_api_key

        merged = {**base.model_dump(), **overrides}
        config._settings = Settings(**merged)

        agent = AgentLoop(
            config=config,
            model=model,
            is_subagent=True,   # Suppress REPL output
        )

        await agent.run_once(prompt)

        tokens_in = agent._llm.total_input_tokens
        tokens_out = agent._llm.total_output_tokens

    finally:
        os.chdir(original_cwd)

    patch = get_diff(repo_dir)
    return patch, tokens_in, tokens_out
