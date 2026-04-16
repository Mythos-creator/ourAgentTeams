"""Tests for the capability store (model performance memory)."""

import json
import tempfile
from pathlib import Path

import pytest

from src.memory import capability_store
from src.config import CONFIG_DIR


@pytest.fixture(autouse=True)
def clean_profiles(tmp_path, monkeypatch):
    """Redirect models_profile.json to a temp file."""
    temp_file = tmp_path / "models_profile.json"
    temp_file.write_text("{}")
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("src.memory.capability_store.load_models_profile",
                        lambda: json.loads(temp_file.read_text()))

    def _save(data):
        temp_file.write_text(json.dumps(data, indent=2))

    monkeypatch.setattr("src.memory.capability_store.save_models_profile", _save)
    yield


def test_record_first_task():
    result = capability_store.record_task_result(
        "test-model",
        quality_score=8.0,
        elapsed_s=10.0,
        tokens_used=1000,
        cost_usd=0.01,
        passed_review=True,
        is_local=False,
    )
    assert result["performance"]["total_tasks"] == 1
    assert result["performance"]["completed"] == 1
    assert result["performance"]["quality"]["avg_score"] == 8.0


def test_record_declining_trend():
    for score in [9, 8, 7, 6, 5]:
        capability_store.record_task_result(
            "declining-model",
            quality_score=score,
            elapsed_s=5.0,
            tokens_used=500,
            cost_usd=0.005,
            passed_review=True,
            is_local=False,
        )

    from src.config import CONFIG_DIR
    profile = capability_store.get_profile("declining-model")
    assert profile["performance"]["quality"]["score_trend"] == "declining"


def test_verdict_not_worth_paying():
    for _ in range(6):
        capability_store.record_task_result(
            "bad-paid-model",
            quality_score=3.0,
            elapsed_s=20.0,
            tokens_used=2000,
            cost_usd=0.05,
            passed_review=False,
            is_local=False,
        )
    profile = capability_store.get_profile("bad-paid-model")
    assert profile["verdict"]["status"] in ("not_worth_paying", "consider_replacing")
