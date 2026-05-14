"""Scheduling manager — cron and interval jobs using APScheduler."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Awaitable

from pydantic import BaseModel


class ScheduledJob(BaseModel):
    id: str
    name: str
    prompt: str
    schedule: str  # cron expression or 'interval:Xs'
    created_at: str = ""
    last_run: str = ""
    next_run: str = ""
    enabled: bool = True


class ScheduleManager:
    """Manages scheduled agent runs with APScheduler."""

    def __init__(
        self,
        jobs_file: Path,
        run_fn: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.jobs_file = jobs_file
        self._run_fn = run_fn
        self._jobs: dict[str, ScheduledJob] = {}
        self._scheduler = None
        self._load_jobs()

    def _load_jobs(self) -> None:
        if self.jobs_file.exists():
            try:
                data = json.loads(self.jobs_file.read_text())
                for item in data:
                    job = ScheduledJob(**item)
                    self._jobs[job.id] = job
            except Exception:
                pass

    def _save_jobs(self) -> None:
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        data = [j.model_dump() for j in self._jobs.values()]
        self.jobs_file.write_text(json.dumps(data, indent=2))

    def create_job(self, name: str, prompt: str, schedule: str) -> ScheduledJob:
        job = ScheduledJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            prompt=prompt,
            schedule=schedule,
            created_at=datetime.now().isoformat(),
        )
        self._jobs[job.id] = job
        self._save_jobs()
        if self._scheduler:
            self._schedule_job(job)
        return job

    def delete_job(self, job_id: str) -> bool:
        if job_id not in self._jobs:
            return False
        del self._jobs[job_id]
        self._save_jobs()
        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        return True

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self._jobs.values())

    def start(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self._scheduler = AsyncIOScheduler()
            for job in self._jobs.values():
                if job.enabled:
                    self._schedule_job(job)
            self._scheduler.start()
        except ImportError:
            pass

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)

    def _schedule_job(self, job: ScheduledJob) -> None:
        if not self._scheduler or not self._run_fn:
            return

        import asyncio

        async def run_job():
            self._jobs[job.id].last_run = datetime.now().isoformat()
            self._save_jobs()
            await self._run_fn(job.prompt)

        schedule = job.schedule.strip()

        if schedule.startswith("interval:"):
            interval_str = schedule.removeprefix("interval:").strip()
            seconds = self._parse_interval(interval_str)
            self._scheduler.add_job(
                run_job,
                "interval",
                seconds=seconds,
                id=job.id,
                replace_existing=True,
            )
        else:
            # Treat as cron expression
            parts = schedule.split()
            if len(parts) == 5:
                minute, hour, day, month, dow = parts
                self._scheduler.add_job(
                    run_job,
                    "cron",
                    minute=minute, hour=hour, day=day, month=month, day_of_week=dow,
                    id=job.id,
                    replace_existing=True,
                )

    @staticmethod
    def _parse_interval(s: str) -> int:
        """Parse '30s', '5m', '2h' to seconds."""
        s = s.strip().lower()
        if s.endswith("s"):
            return int(s[:-1])
        elif s.endswith("m"):
            return int(s[:-1]) * 60
        elif s.endswith("h"):
            return int(s[:-1]) * 3600
        return int(s)
