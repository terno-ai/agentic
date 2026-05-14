"""Git helpers: clone, checkout, diff."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path


GITHUB_BASE = "https://github.com"


async def clone_at_commit(repo: str, commit: str, dest: Path) -> None:
    """
    Clone `repo` (e.g. 'django/django') and check out `commit`.
    Uses a shallow fetch so only the history needed is downloaded.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{GITHUB_BASE}/{repo}.git"

    # Full clone is more reliable than shallow for arbitrary commits
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--quiet", url, str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed for {repo}:\n{stderr.decode()}")

    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(dest), "checkout", "--quiet", commit,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git checkout {commit} failed:\n{stderr.decode()}")


def get_diff(repo_dir: Path) -> str:
    """Return `git diff HEAD` — all changes made since the base commit."""
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def get_diff_cached(repo_dir: Path) -> str:
    """Include both staged and unstaged changes."""
    result = subprocess.run(
        ["git", "diff"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def reset_to_base(repo_dir: Path, commit: str) -> None:
    """Hard reset back to the base commit (for retry / cleanup)."""
    subprocess.run(
        ["git", "-C", str(repo_dir), "checkout", "--quiet", commit],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "reset", "--hard", "HEAD"],
        check=True,
    )
