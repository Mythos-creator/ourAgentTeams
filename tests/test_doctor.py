"""Tests for `ouragentteams doctor` health checks."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_check_python_version_ok():
    from src.cli.doctor import _check_python_version

    r = _check_python_version()
    if sys.version_info >= (3, 11):
        assert r["status"] == "ok"
    else:
        assert r["status"] == "fail"


def test_check_config_exists_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    from src.cli.doctor import _check_config_exists

    r = _check_config_exists()
    assert r["status"] == "fail"


def test_check_config_exists_present(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("leader: {}\n")
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    from src.cli.doctor import _check_config_exists

    r = _check_config_exists()
    assert r["status"] == "ok"


def test_check_data_dir_writable(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.DATA_DIR", tmp_path / "datadir")
    from src.cli.doctor import _check_data_dir_writable

    r = _check_data_dir_writable()
    assert r["status"] == "ok"


def test_check_api_keys_configured_none(monkeypatch):
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    from src.cli.doctor import _check_api_keys_configured

    r = _check_api_keys_configured()
    assert r["status"] == "warn"


def test_check_api_keys_configured_some(monkeypatch):
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake")
    from src.cli.doctor import _check_api_keys_configured

    r = _check_api_keys_configured()
    assert r["status"] == "ok"
    assert "DEEPSEEK_API_KEY" in r["detail"]


def test_check_api_keys_connectivity_no_keys(monkeypatch):
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    from src.cli.doctor import _check_api_keys_connectivity

    rows = _check_api_keys_connectivity()
    assert rows == []


def test_check_ollama_running_unreachable(monkeypatch):
    """When Ollama can't be reached, surface a warn (not fail)."""
    from src.cli import doctor as doctor_mod

    class _FakeWorker:
        def __init__(self, *a, **kw): pass
        async def list_models(self):
            raise ConnectionError("nope")

    monkeypatch.setattr("src.models.local_model.OllamaWorker", _FakeWorker)
    r = doctor_mod._check_ollama_running()
    assert r["status"] == "warn"
