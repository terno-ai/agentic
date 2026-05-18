"""Tests for built-in tools."""

import pytest
import tempfile
from pathlib import Path

from agentic.tools.file_tools import ReadTool, WriteTool, EditTool
from agentic.tools.bash import BashTool
from agentic.tools.task_tools import TaskStore, TaskCreateTool, TaskListTool, TaskUpdateTool


@pytest.fixture
def task_store():
    return TaskStore()


class TestReadTool:
    @pytest.fixture
    def tmp_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\nline 2\nline 3\n")
        return f

    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_file):
        tool = ReadTool()
        result = await tool.execute(file_path=str(tmp_file))
        assert not result.is_error
        assert "line 1" in result.content
        assert "1\t" in result.content

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        tool = ReadTool()
        result = await tool.execute(file_path="/nonexistent/path/file.txt")
        assert result.is_error

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tmp_file):
        tool = ReadTool()
        result = await tool.execute(file_path=str(tmp_file), offset=2, limit=1)
        assert not result.is_error
        assert "line 2" in result.content
        assert "line 1" not in result.content


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path):
        tool = WriteTool()
        path = tmp_path / "new.txt"
        result = await tool.execute(file_path=str(path), content="hello world")
        assert not result.is_error
        assert path.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_directories(self, tmp_path):
        tool = WriteTool()
        path = tmp_path / "deep" / "nested" / "file.txt"
        result = await tool.execute(file_path=str(path), content="content")
        assert not result.is_error
        assert path.exists()


class TestEditTool:
    @pytest.fixture
    def tmp_file(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello world\ngoodbye world\n")
        return f

    @pytest.mark.asyncio
    async def test_simple_replace(self, tmp_file):
        tool = EditTool()
        result = await tool.execute(
            file_path=str(tmp_file),
            old_string="hello world",
            new_string="hi world",
        )
        assert not result.is_error
        assert "hi world" in tmp_file.read_text()

    @pytest.mark.asyncio
    async def test_replace_not_found(self, tmp_file):
        tool = EditTool()
        result = await tool.execute(
            file_path=str(tmp_file),
            old_string="nonexistent text",
            new_string="replacement",
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_replace_all(self, tmp_file):
        tmp_file.write_text("a b a b a\n")
        tool = EditTool()
        result = await tool.execute(
            file_path=str(tmp_file),
            old_string="a",
            new_string="X",
            replace_all=True,
        )
        assert not result.is_error
        assert tmp_file.read_text() == "X b X b X\n"

    @pytest.mark.asyncio
    async def test_ambiguous_fails_without_replace_all(self, tmp_file):
        tmp_file.write_text("world world\n")
        tool = EditTool()
        result = await tool.execute(
            file_path=str(tmp_file),
            old_string="world",
            new_string="earth",
        )
        assert result.is_error
        assert "replace_all" in result.content


class TestBashTool:
    @pytest.mark.asyncio
    async def test_simple_command(self):
        tool = BashTool()
        result = await tool.execute(command="echo hello")
        assert not result.is_error
        assert "hello" in result.content

    @pytest.mark.asyncio
    async def test_failing_command(self):
        tool = BashTool()
        result = await tool.execute(command="false")
        assert result.is_error

    @pytest.mark.asyncio
    async def test_stderr_captured(self):
        tool = BashTool()
        result = await tool.execute(command="echo error >&2; exit 0")
        assert "error" in result.content

    @pytest.mark.asyncio
    async def test_timeout(self):
        tool = BashTool()
        result = await tool.execute(command="sleep 10", timeout=100)
        assert result.is_error
        assert "timed out" in result.content.lower()


class TestTaskTools:
    @pytest.mark.asyncio
    async def test_create_and_list(self, task_store):
        create = TaskCreateTool(task_store)
        list_tool = TaskListTool(task_store)

        await create.execute(description="Test task")
        result = await list_tool.execute()
        assert "Test task" in result.content

    @pytest.mark.asyncio
    async def test_update_status(self, task_store):
        create = TaskCreateTool(task_store)
        update = TaskUpdateTool(task_store)

        create_result = await create.execute(description="Task to update")
        task_id = create_result.content.split()[2].rstrip(":")  # "Created task <id>: ..."

        update_result = await update.execute(task_id=task_id, status="completed")
        assert not update_result.is_error
        assert task_store.get(task_id).status == "completed"
