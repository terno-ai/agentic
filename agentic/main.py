"""CLI entry point for agentic."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="agentic",
    help="Autonomous coding agent with memory, skills, MCP, and context summarization.",
    add_completion=True,
)
console = Console()


def _get_config(project_dir: Path | None = None, user_id: str | None = None):
    import getpass
    from agentic.core.config import ConfigManager
    uid = user_id or os.environ.get("AGENTIC_USER") or getpass.getuser()
    return ConfigManager(project_dir or Path.cwd(), user_id=uid)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to use"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider: anthropic or openai"),
    project_dir: Optional[Path] = typer.Option(None, "--dir", "-d", help="Project directory"),
    plan_mode: bool = typer.Option(False, "--plan", "-p", help="Start in plan mode"),
    sandbox: bool = typer.Option(False, "--sandbox", "-s", help="Run commands in Docker sandbox"),
    kernel: bool = typer.Option(False, "--kernel", "-k", help="Enable persistent Python kernel"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User ID for sandbox isolation (default: system username)"),
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
):
    """Start the interactive agent REPL."""
    if version:
        from agentic import __version__
        console.print(f"agentic v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    asyncio.run(_run_repl(model=model, provider=provider, project_dir=project_dir,
                          plan_mode=plan_mode, sandbox=sandbox, kernel=kernel, user_id=user))


async def _run_repl(
    model: str | None = None,
    provider: str | None = None,
    project_dir: Path | None = None,
    plan_mode: bool = False,
    sandbox: bool = False,
    kernel: bool = False,
    user_id: str | None = None,
) -> None:
    import getpass
    from agentic.core.agent import AgentLoop
    from agentic.core.config import ConfigManager, detect_provider
    from agentic.hooks.events import HookEvent
    from agentic.ui.renderer import Renderer
    from agentic.ui.repl import REPL

    # Resolve user identity — explicit flag > env var > system username
    resolved_user = user_id or os.environ.get("AGENTIC_USER") or getpass.getuser()

    config = ConfigManager(project_dir or Path.cwd(), user_id=resolved_user)

    if model:
        config.save_global(model=model)
    if provider:
        config.save_global(provider=provider)
    if plan_mode:
        config.save_project(plan_mode=True)

    renderer = Renderer(theme=config.settings.theme)

    # Start Docker sandbox if requested
    sandbox_instance = None
    if sandbox or config.settings.sandbox.enabled:
        from agentic.sandbox.docker_sandbox import DockerSandbox, DockerNotAvailable
        sb_cfg = config.settings.sandbox
        sandbox_instance = DockerSandbox(sb_cfg, user_id=resolved_user)
        try:
            renderer.print_system(
                f"Starting sandbox for user '{sandbox_instance.user_id}' "
                f"(workspace: {sandbox_instance.workspace}) ..."
            )
            await sandbox_instance.start()
            renderer.print_system(
                f"Sandbox ready  container={sandbox_instance.container_name}  "
                f"mem={sb_cfg.memory_limit}  net={sb_cfg.network}"
            )
        except DockerNotAvailable as e:
            renderer.print_error(str(e))
            renderer.print_system("Falling back to host execution.")
            sandbox_instance = None
        except Exception as e:
            renderer.print_error(f"Sandbox failed to start: {e}")
            sandbox_instance = None

    # Start Python kernel if requested
    kernel_instance = None
    if kernel or config.settings.kernel.enabled:
        from agentic.kernel.manager import KernelManager
        kernel_instance = KernelManager(
            config=config.settings.kernel,
            sandbox=sandbox_instance,
        )
        try:
            renderer.print_system("Starting Python kernel...")
            await kernel_instance.start()
            renderer.print_system(
                f"Kernel ready  mem_limit={config.settings.kernel.memory_limit_mb}MB  "
                f"timeout={config.settings.kernel.default_timeout_s}s"
            )
        except Exception as e:
            renderer.print_error(f"Kernel failed to start: {e}")
            kernel_instance = None

    agent = AgentLoop(
        config=config,
        model=model,
        renderer=renderer,
        sandbox=sandbox_instance,
        kernel=kernel_instance,
        user_id=resolved_user,
    )

    await agent._hook_mgr.fire(HookEvent.AGENT_START, {"project_dir": str(Path.cwd())})

    # Start MCP servers
    await agent.start_mcp_servers()

    repl = REPL(
        agent=agent,
        renderer=renderer,
        history_file=config.history_file(),
    )

    try:
        await repl.run()
    except SystemExit:
        pass
    finally:
        await agent.shutdown()


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Prompt to run non-interactively"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to use"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider: anthropic or openai"),
    project_dir: Optional[Path] = typer.Option(None, "--dir", help="Project directory"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="User ID for sandbox isolation"),
):
    """Run a single prompt non-interactively."""
    asyncio.run(_run_once(prompt, model=model, provider=provider, project_dir=project_dir, user_id=user))


async def _run_once(
    prompt: str,
    model: str | None = None,
    provider: str | None = None,
    project_dir: Path | None = None,
    user_id: str | None = None,
) -> None:
    import getpass
    from agentic.core.agent import AgentLoop
    from agentic.core.config import ConfigManager
    from agentic.ui.renderer import Renderer

    resolved_user = user_id or os.environ.get("AGENTIC_USER") or getpass.getuser()
    config = ConfigManager(project_dir or Path.cwd(), user_id=resolved_user)
    if model:
        config.save_global(model=model)
    if provider:
        config.save_global(provider=provider)

    renderer = Renderer()
    agent = AgentLoop(config=config, model=model, renderer=renderer, user_id=resolved_user)
    await agent.start_mcp_servers()
    await agent.run_turn(prompt)
    await agent.shutdown()


@app.command()
def skills(
    action: str = typer.Argument("list", help="Action: list | add <file>"),
    path: Optional[Path] = typer.Argument(None, help="Skill YAML file to add"),
):
    """Manage skills."""
    config = _get_config()
    from agentic.skills.manager import SkillManager
    sm = SkillManager(extra_dirs=config.settings.skills_dirs)

    if action == "list":
        all_skills = sm.list_all()
        if not all_skills:
            console.print("No skills found.")
        for s in all_skills:
            console.print(f"/{s.name:20} — {s.description}")
    elif action == "add" and path:
        skill = sm.add_from_file(path)
        if skill:
            console.print(f"Added skill: /{skill.name}")
        else:
            console.print(f"Failed to load skill from: {path}", style="red")
    else:
        console.print("Usage: agentic skills [list|add <file>]")


@app.command()
def memory(
    action: str = typer.Argument("list", help="Action: list | search <query> | delete <name>"),
    query: Optional[str] = typer.Argument(None, help="Query or memory name"),
):
    """Manage agent memories."""
    config = _get_config()
    from agentic.memory.manager import MemoryManager
    mm = MemoryManager(config.memory_dir())

    if action == "list":
        records = mm.list_all()
        if not records:
            console.print("No memories found.")
        for r in records:
            console.print(f"[{r.memory_type.value:10}] {r.name}: {r.description}")
    elif action == "search" and query:
        results = mm.search(query)
        for r in results:
            console.print(f"[{r.memory_type.value}] {r.name}\n{r.body[:200]}\n")
    elif action == "delete" and query:
        if mm.delete(query):
            console.print(f"Deleted memory: {query}")
        else:
            console.print(f"Memory not found: {query}", style="red")
    else:
        console.print("Usage: agentic memory [list|search <query>|delete <name>]")


@app.command()
def schedule(
    action: str = typer.Argument("list", help="Action: list | add | delete"),
    name: Optional[str] = typer.Option(None, help="Job name"),
    prompt: Optional[str] = typer.Option(None, help="Prompt to run"),
    cron: Optional[str] = typer.Option(None, help="Cron expression or 'interval:30s'"),
    job_id: Optional[str] = typer.Option(None, help="Job ID to delete"),
):
    """Manage scheduled agent jobs."""
    config = _get_config()
    from agentic.scheduling.manager import ScheduleManager
    jobs_file = Path.home() / ".agentic" / "scheduled_jobs.json"
    sm = ScheduleManager(jobs_file)

    if action == "list":
        jobs = sm.list_jobs()
        if not jobs:
            console.print("No scheduled jobs.")
        for j in jobs:
            status = "✓" if j.enabled else "✗"
            console.print(f"[{j.id}] {status} {j.name} — {j.schedule}")
    elif action == "add" and name and prompt and cron:
        job = sm.create_job(name, prompt, cron)
        console.print(f"Created job [{job.id}]: {job.name} ({job.schedule})")
    elif action == "delete" and job_id:
        if sm.delete_job(job_id):
            console.print(f"Deleted job: {job_id}")
        else:
            console.print(f"Job not found: {job_id}", style="red")
    else:
        console.print(
            "Usage:\n"
            "  agentic schedule list\n"
            "  agentic schedule add --name='daily-review' --prompt='...' --cron='0 9 * * *'\n"
            "  agentic schedule delete --job-id=<id>"
        )


@app.command()
def config(
    key: Optional[str] = typer.Argument(None, help="Setting key to show or set"),
    value: Optional[str] = typer.Argument(None, help="Value to set"),
    global_: bool = typer.Option(False, "--global", "-g", help="Edit global config"),
):
    """View or modify configuration."""
    cfg = _get_config()
    if key and value:
        if global_:
            cfg.save_global(**{key: value})
        else:
            cfg.save_project(**{key: value})
        console.print(f"Set {key} = {value}")
    elif key:
        settings = cfg.settings
        val = getattr(settings, key, None)
        console.print(f"{key} = {val}")
    else:
        console.print(cfg.settings.model_dump_json(indent=2))


if __name__ == "__main__":
    app()
