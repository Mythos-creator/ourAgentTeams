"""Tests for first-run setup wizard helpers (path resolution, template copy)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_resolve_user_home_env_var(tmp_path, monkeypatch):
    from src.config import _resolve_user_home

    monkeypatch.setenv("OURAGENTTEAMS_HOME", str(tmp_path))
    assert _resolve_user_home() == tmp_path.resolve()


def test_resolve_user_home_xdg(tmp_path, monkeypatch):
    from src.config import _resolve_user_home

    monkeypatch.delenv("OURAGENTTEAMS_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert _resolve_user_home() == tmp_path / "ouragentteams"


def test_resolve_user_home_default(tmp_path, monkeypatch):
    from src.config import _resolve_user_home

    monkeypatch.delenv("OURAGENTTEAMS_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert _resolve_user_home() == tmp_path / ".config" / "ouragentteams"


def test_is_initialized_false(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    from src.config import is_initialized

    assert is_initialized() is False


def test_is_initialized_true(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("leader: {}\n")
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    from src.config import is_initialized

    assert is_initialized() is True


def test_load_config_raises_not_initialized(tmp_path, monkeypatch):
    """When the default config doesn't exist, raise our typed exception."""
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    from src.config import ConfigNotInitializedError, load_config

    with pytest.raises(ConfigNotInitializedError):
        load_config()


def test_parse_csv_int():
    from src.cli.setup_wizard import _parse_csv_int

    assert _parse_csv_int("1,2,3", max_val=4) == [1, 2, 3]
    assert _parse_csv_int(" 2 , 4 ", max_val=4) == [2, 4]
    assert _parse_csv_int("0,5", max_val=4) == []  # Out of range
    assert _parse_csv_int("1,1,2", max_val=4) == [1, 2]  # Dedup
    assert _parse_csv_int("", max_val=4) == []
    assert _parse_csv_int("abc,2", max_val=4) == [2]


def test_default_cloud_leader():
    from src.cli.setup_wizard import _default_cloud_leader

    assert _default_cloud_leader({"ANTHROPIC_API_KEY": "x"}) == "claude-3-5-sonnet-latest"
    assert _default_cloud_leader({"OPENAI_API_KEY": "x"}) == "gpt-4o-mini"
    assert _default_cloud_leader({"DEEPSEEK_API_KEY": "x"}) == "deepseek-chat"
    assert _default_cloud_leader({}) is None


def test_model_for_env():
    from src.cli.setup_wizard import _model_for_env

    assert _model_for_env("OPENAI_API_KEY") == "gpt-4o-mini"
    assert _model_for_env("UNKNOWN_KEY") is None


def test_template_files_present():
    """Bundled templates must be importable via importlib.resources."""
    from importlib import resources

    files = resources.files("src._templates")
    expected = {"config.yaml.tpl", "privacy_rules.yaml.tpl", "env.tpl"}
    names = {p.name for p in files.iterdir()}
    assert expected.issubset(names), f"Missing templates: {expected - names}"

    agents = resources.files("src._templates.agents")
    md_files = [p.name for p in agents.iterdir() if p.name.endswith(".md")]
    assert md_files, "No bundled agent .md templates"


def test_write_config_files_local_mode(tmp_path):
    """End-to-end: writing config files for local mode populates the home dir."""
    from src.cli.setup_wizard import WizardChoices, _write_config_files

    choices = WizardChoices(
        mode="local",
        leader_model="qwen2.5:7b",
        pulled_models=["qwen2.5:7b"],
        api_keys={},
        monthly_budget_usd=10.0,
    )
    _write_config_files(choices, tmp_path)

    assert (tmp_path / "config.yaml").exists()
    assert (tmp_path / "privacy_rules.yaml").exists()
    assert (tmp_path / ".env").exists()
    assert (tmp_path / "agents").is_dir()

    import yaml
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert cfg["leader"]["model"] == "qwen2.5:7b"
    assert any(w["model"] == "qwen2.5:7b" for w in cfg["workers"]["local"])
    assert cfg["cost"]["monthly_budget_usd"] == 10.0


def test_write_config_files_hybrid_with_keys(tmp_path):
    from src.cli.setup_wizard import WizardChoices, _write_config_files

    choices = WizardChoices(
        mode="hybrid",
        leader_model="qwen2.5:7b",
        pulled_models=["qwen2.5:7b"],
        api_keys={"DEEPSEEK_API_KEY": "sk-test"},
        monthly_budget_usd=20.0,
    )
    _write_config_files(choices, tmp_path)

    env_text = (tmp_path / ".env").read_text()
    assert "DEEPSEEK_API_KEY=sk-test" in env_text

    import yaml
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    api_models = [w["model"] for w in cfg["workers"]["api"]]
    assert "deepseek-chat" in api_models
