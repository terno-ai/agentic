"""Task tracking tools: TaskCreate, TaskGet, TaskList, TaskUpdate, TaskStop."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from agentic.tools.base import Tool, ToolResult


class TaskRecord(BaseModel):
    id: str
    description: str
    status: str = "pending"  # pending | in_progress | completed | failed | stopped
    created_at: str = ""
    updated_at: str = ""
    output: str = ""
    metadata: dict[str, Any] = {}


class TaskStore:
    """Per-instance task store — each AgentLoop (including sub-agents) gets its own."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    def create(self, description: str, **meta: Any) -> TaskRecord:
        task = TaskRecord(
            id=str(uuid.uuid4())[:8],
            description=description,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            metadata=meta,
        )
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def update(self, task_id: str, **kwargs: Any) -> TaskRecord | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        data = task.model_dump()
        data.update(kwargs)
        data["updated_at"] = datetime.now().isoformat()
        self._tasks[task_id] = TaskRecord(**data)
        return self._tasks[task_id]

    def list_all(self) -> list[TaskRecord]:
        return list(self._tasks.values())


class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = "Create a new task to track work. Returns the task ID."
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Task description"},
        },
        "required": ["description"],
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, description: str) -> ToolResult:
        task = self._store.create(description)
        return ToolResult.ok(f"Created task {task.id}: {description}")


class TaskGetTool(Tool):
    name = "TaskGet"
    description = "Get details about a specific task by ID."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID"},
        },
        "required": ["task_id"],
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, task_id: str) -> ToolResult:
        task = self._store.get(task_id)
        if not task:
            return ToolResult.error(f"Task not found: {task_id}")
        lines = [
            f"Task: {task.id}",
            f"Status: {task.status}",
            f"Description: {task.description}",
            f"Created: {task.created_at}",
            f"Updated: {task.updated_at}",
        ]
        if task.output:
            lines.append(f"Output:\n{task.output}")
        return ToolResult.ok("\n".join(lines))


class TaskListTool(Tool):
    name = "TaskList"
    description = "List all tasks and their statuses."
    input_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter by status (optional)"},
        },
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, status: str | None = None) -> ToolResult:
        tasks = self._store.list_all()
        if status:
            tasks = [t for t in tasks if t.status == status]
        if not tasks:
            return ToolResult.ok("No tasks found.")
        lines = [f"[{t.id}] {t.status:12} {t.description}" for t in tasks]
        return ToolResult.ok("\n".join(lines))


class TaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = "Update a task's status or output."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID"},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "failed", "stopped"],
                "description": "New status",
            },
            "output": {"type": "string", "description": "Task output or notes"},
        },
        "required": ["task_id"],
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, task_id: str, status: str | None = None, output: str | None = None) -> ToolResult:
        kwargs: dict[str, Any] = {}
        if status:
            kwargs["status"] = status
        if output:
            kwargs["output"] = output
        task = self._store.update(task_id, **kwargs)
        if not task:
            return ToolResult.error(f"Task not found: {task_id}")
        return ToolResult.ok(f"Updated task {task_id}: status={task.status}")


class TaskStopTool(Tool):
    name = "TaskStop"
    description = "Stop a running task."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID to stop"},
        },
        "required": ["task_id"],
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, task_id: str) -> ToolResult:
        task = self._store.update(task_id, status="stopped")
        if not task:
            return ToolResult.error(f"Task not found: {task_id}")
        return ToolResult.ok(f"Stopped task {task_id}")


class TaskOutputTool(Tool):
    name = "TaskOutput"
    description = "Get the output of a task."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID"},
        },
        "required": ["task_id"],
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, task_id: str) -> ToolResult:
        task = self._store.get(task_id)
        if not task:
            return ToolResult.error(f"Task not found: {task_id}")
        return ToolResult.ok(task.output or "(no output yet)")
