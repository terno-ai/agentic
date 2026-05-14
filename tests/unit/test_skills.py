"""Tests for the skills system."""

import pytest
import yaml
from pathlib import Path

from agentic.skills.manager import SkillManager, SkillDefinition
from agentic.skills.runner import SkillRunner


@pytest.fixture
def skills_dir(tmp_path):
    """Create a temporary skills directory with a test skill."""
    skill_data = {
        "name": "test-skill",
        "description": "A test skill",
        "prompt": "Do something with: {{args}}",
    }
    skill_file = tmp_path / "test-skill.yaml"
    skill_file.write_text(yaml.dump(skill_data))
    return tmp_path


@pytest.fixture
def manager(skills_dir):
    return SkillManager(extra_dirs=[str(skills_dir)])


def test_load_skill(manager):
    skill = manager.get("test-skill")
    assert skill is not None
    assert skill.description == "A test skill"


def test_builtin_skills_loaded():
    manager = SkillManager()
    names = manager.names()
    assert "init" in names
    assert "review" in names
    assert "simplify" in names
    assert "security-review" in names


def test_format_prompt_with_args(manager):
    skill = manager.get("test-skill")
    formatted = skill.format_prompt("my arg")
    assert "my arg" in formatted
    assert "{{args}}" not in formatted


def test_format_prompt_without_args(manager):
    skill = manager.get("test-skill")
    formatted = skill.format_prompt()
    assert "{{args}}" not in formatted


class TestSkillRunner:
    def test_parse_slash_command(self):
        result = SkillRunner.parse_slash_command("/review main")
        assert result == ("review", "main")

    def test_parse_slash_command_no_args(self):
        result = SkillRunner.parse_slash_command("/init")
        assert result == ("init", "")

    def test_parse_non_slash(self):
        result = SkillRunner.parse_slash_command("not a command")
        assert result is None

    def test_parse_bash_shortcut_not_skill(self):
        result = SkillRunner.parse_slash_command("/! ls -la")
        assert result is None

    def test_build_prompt(self, manager):
        skill = manager.get("test-skill")
        prompt = SkillRunner.build_prompt(skill, "hello")
        assert "hello" in prompt
