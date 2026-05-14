"""Tests for the configuration system."""

import pytest
import json
from pathlib import Path

from agentic.core.config import ConfigManager, Settings


def test_default_settings():
    s = Settings()
    assert s.model == "claude-sonnet-4-6"
    assert s.max_tokens == 8192
    assert s.stream is True


def test_load_settings_from_file(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"model": "claude-opus-4-7", "max_tokens": 4096}))
    s = Settings.load(settings_file)
    assert s.model == "claude-opus-4-7"
    assert s.max_tokens == 4096


def test_save_and_reload(tmp_path):
    settings_file = tmp_path / "settings.json"
    s = Settings(model="claude-haiku-4-5-20251001")
    s.save(settings_file)
    loaded = Settings.load(settings_file)
    assert loaded.model == "claude-haiku-4-5-20251001"


def test_config_manager_memory_dir(tmp_path):
    config = ConfigManager(project_dir=tmp_path)
    mem_dir = config.memory_dir()
    assert "memory" in str(mem_dir)
    # Same project dir → same memory dir
    config2 = ConfigManager(project_dir=tmp_path)
    assert config.memory_dir() == config2.memory_dir()


def test_config_manager_merge(tmp_path):
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Write global settings
    global_settings = tmp_path / "global" / "settings.json"
    global_settings.write_text(json.dumps({"model": "claude-opus-4-7", "max_tokens": 4096}))

    # Write project settings
    project_config_dir = project_dir / ".agentic"
    project_config_dir.mkdir()
    (project_config_dir / "settings.json").write_text(json.dumps({"max_tokens": 2048}))

    config = ConfigManager(project_dir=project_dir)
    # Override to use our test global dir
    config.global_settings_path = global_settings
    config._settings = None  # Force reload

    settings = config.settings
    # model comes from global, max_tokens overridden by project
    assert settings.max_tokens == 2048
