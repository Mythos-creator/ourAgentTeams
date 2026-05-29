"""Tests for the worker factory."""

from __future__ import annotations

from src.config import AppConfig, LeaderConfig, WorkerEntry
from src.models.api_model import APIModelWorker
from src.models.factory import (
    create_leader_worker, create_worker_for_model, create_worker_from_entry,
)
from src.models.local_model import OllamaWorker


def test_entry_ollama_returns_ollama_worker():
    entry = WorkerEntry(model="qwen2.5:7b", provider="ollama")
    cfg = AppConfig(leader=LeaderConfig(ollama_base_url="http://h:11434"))
    w = create_worker_from_entry(entry, cfg)
    assert isinstance(w, OllamaWorker)
    assert w.model == "qwen2.5:7b"
    assert w._base_url == "http://h:11434"


def test_entry_litellm_returns_api_worker():
    entry = WorkerEntry(model="gpt-4o-mini", provider="litellm", api_key="sk-x")
    w = create_worker_from_entry(entry)
    assert isinstance(w, APIModelWorker)
    assert w.model == "gpt-4o-mini"
    assert w._api_key == "sk-x"


def test_leader_respects_provider_ollama():
    cfg = AppConfig(leader=LeaderConfig(model="qwen2.5:14b", provider="ollama"))
    w = create_leader_worker(cfg)
    assert isinstance(w, OllamaWorker)


def test_leader_respects_provider_litellm():
    cfg = AppConfig(leader=LeaderConfig(model="gpt-4o-mini", provider="litellm"))
    w = create_leader_worker(cfg)
    assert isinstance(w, APIModelWorker)


def test_for_model_uses_cfg_to_pick_provider():
    cfg = AppConfig(
        workers_local=[WorkerEntry(model="local:7b", provider="ollama")],
        workers_api=[WorkerEntry(model="claude-3-haiku", provider="litellm")],
    )
    assert isinstance(create_worker_for_model("local:7b", cfg), OllamaWorker)
    assert isinstance(create_worker_for_model("claude-3-haiku", cfg), APIModelWorker)


def test_for_model_falls_back_to_ollama_when_unknown():
    cfg = AppConfig()
    w = create_worker_for_model("unknown:1b", cfg)
    assert isinstance(w, OllamaWorker)
    assert w.model == "unknown:1b"
