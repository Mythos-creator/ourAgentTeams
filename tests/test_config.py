"""Tests for configuration loading, env-var interpolation, and save/reload."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.config import (
    _interpolate_env,
    _walk_interpolate,
    load_config,
    save_config,
    load_user_profile,
    save_user_profile,
    CONFIG_DIR,
)


def test_interpolate_env_simple():
    os.environ["TEST_VAR_123"] = "hello_world"
    assert _interpolate_env("${TEST_VAR_123}") == "hello_world"
    del os.environ["TEST_VAR_123"]


def test_interpolate_env_missing():
    result = _interpolate_env("${NONEXISTENT_VAR_XYZ}")
    assert result == "${NONEXISTENT_VAR_XYZ}"


def test_walk_interpolate_nested():
    os.environ["WI_TEST"] = "val"
    data = {"a": "${WI_TEST}", "b": ["${WI_TEST}", "plain"], "c": {"d": "${WI_TEST}"}}
    result = _walk_interpolate(data)
    assert result == {"a": "val", "b": ["val", "plain"], "c": {"d": "val"}}
    del os.environ["WI_TEST"]


def test_load_config_defaults():
    cfg = load_config()
    assert cfg.leader.model
    assert cfg.leader.provider == "ollama"
    assert isinstance(cfg.workers_local, list)
    assert cfg.monitor.timeout_threshold_s > 0
    assert cfg.cost.monthly_budget_usd > 0
    assert cfg.privacy.enabled is True


def test_save_and_reload(tmp_path):
    cfg = load_config()
    cfg.leader.model = "test-model:99b"
    out_path = tmp_path / "test_config.yaml"
    cfg._config_path = out_path
    save_config(cfg, out_path)

    cfg2 = load_config(out_path)
    assert cfg2.leader.model == "test-model:99b"


def test_user_profile_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.DATA_DIR", tmp_path)
    profile = load_user_profile()
    assert profile["natural_language_summary"] == ""

    profile["natural_language_summary"] = "Test user"
    save_user_profile(profile)

    loaded = load_user_profile()
    assert loaded["natural_language_summary"] == "Test user"
