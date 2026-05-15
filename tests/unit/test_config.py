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
    config = ConfigManager(project_dir=tmp_path, user_id="alice")
    mem_dir = config.memory_dir()
    assert "memory" in str(mem_dir)
    assert "alice" in str(mem_dir)

def test_memory_dir_isolated_per_user(tmp_path):
    alice = ConfigManager(project_dir=tmp_path, user_id="alice")
    bob   = ConfigManager(project_dir=tmp_path, user_id="bob")
    # Same project, different users → different memory dirs
    assert alice.memory_dir() != bob.memory_dir()
    assert "alice" in str(alice.memory_dir())
    assert "bob"   in str(bob.memory_dir())

def test_memory_dir_same_user_same_project(tmp_path):
    c1 = ConfigManager(project_dir=tmp_path, user_id="alice")
    c2 = ConfigManager(project_dir=tmp_path, user_id="alice")
    assert c1.memory_dir() == c2.memory_dir()

def test_history_file_isolated_per_user(tmp_path):
    alice = ConfigManager(project_dir=tmp_path, user_id="alice")
    bob   = ConfigManager(project_dir=tmp_path, user_id="bob")
    assert alice.history_file() != bob.history_file()


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
