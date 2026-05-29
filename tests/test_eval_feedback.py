"""Tests for eval.feedback writing ELO back to models_profile.json."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.elo import EloTable


@pytest.fixture
def patch_profile_paths(tmp_path, monkeypatch):
    """Redirect models_profile.json to a tmp file for isolation."""
    pfile = tmp_path / "models_profile.json"
    pfile.write_text("{}", encoding="utf-8")

    import src.config as cfg
    import src.memory.capability_store as caps
    import eval.feedback as fb

    def _load():
        import json
        return json.loads(pfile.read_text(encoding="utf-8"))

    def _save(d):
        import json
        pfile.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    monkeypatch.setattr(cfg, "load_models_profile", _load)
    monkeypatch.setattr(cfg, "save_models_profile", _save)
    monkeypatch.setattr(caps, "load_models_profile", _load)
    monkeypatch.setattr(caps, "save_models_profile", _save)
    monkeypatch.setattr(fb, "load_models_profile", _load)
    monkeypatch.setattr(fb, "save_models_profile", _save)
    return pfile


def test_write_elo_creates_blocks(patch_profile_paths: Path):
    elo = EloTable()
    elo.update("alpha", "beta", 1.0)
    elo.update("alpha", "gamma", 1.0)

    from eval.feedback import write_elo_to_profiles
    profiles = write_elo_to_profiles(elo, benchmark="coding-v1")

    assert "alpha" in profiles
    block = profiles["alpha"]["eval_elo"]
    assert "benchmarks" in block
    assert block["benchmarks"]["coding-v1"]["rank"] == 1
    assert block["best"]["benchmark"] == "coding-v1"


def test_top_strengths_added(patch_profile_paths: Path):
    elo = EloTable()
    elo.update("alpha", "beta", 1.0)
    elo.update("alpha", "gamma", 1.0)
    elo.update("beta", "gamma", 1.0)

    from eval.feedback import write_elo_to_profiles
    profiles = write_elo_to_profiles(elo, benchmark="b1", top_strength_count=2)

    assert "eval:b1" in profiles["alpha"].get("strengths", [])
    assert "eval:b1" in profiles["beta"].get("strengths", [])
    assert "eval:b1" not in profiles["gamma"].get("strengths", [])


def test_read_eval_rank(patch_profile_paths: Path):
    elo = EloTable()
    elo.update("a", "b", 1.0)
    from eval.feedback import read_eval_rank, write_elo_to_profiles
    write_elo_to_profiles(elo, benchmark="bx")
    assert read_eval_rank("a", "bx") == 1
    assert read_eval_rank("a", "nonexistent") is None
    assert read_eval_rank("ghost", "bx") is None


def test_best_updates_to_higher_rating(patch_profile_paths: Path):
    elo1 = EloTable()
    elo1.update("a", "b", 1.0)
    elo2 = EloTable()
    elo2.update("a", "b", 1.0)
    elo2.update("a", "c", 1.0)  # higher rating after more wins

    from eval.feedback import write_elo_to_profiles
    write_elo_to_profiles(elo1, benchmark="b1")
    profiles = write_elo_to_profiles(elo2, benchmark="b2")
    assert profiles["a"]["eval_elo"]["best"]["benchmark"] == "b2"
