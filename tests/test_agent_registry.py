"""Tests for AgentRegistry and AgentProfile parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.leader.agent_registry import AgentProfile, AgentRegistry, parse_agent_md


VALID_AGENT_MD = """\
---
name: tester
description: just a test
preferred_models:
  - foo:7b
  - bar:13b
fallback_models:
  - baz:3b
required_skills:
  - coding
  - testing
temperature: 0.25
max_tokens: 1024
plan_mode: manual
---

You are a test agent.
"""


def test_parse_agent_md(tmp_path: Path):
    p = tmp_path / "tester.md"
    p.write_text(VALID_AGENT_MD, encoding="utf-8")
    ap = parse_agent_md(p)
    assert isinstance(ap, AgentProfile)
    assert ap.name == "tester"
    assert ap.preferred_models == ["foo:7b", "bar:13b"]
    assert ap.fallback_models == ["baz:3b"]
    assert ap.required_skills == ["coding", "testing"]
    assert ap.temperature == pytest.approx(0.25)
    assert ap.max_tokens == 1024
    assert ap.plan_mode == "manual"
    assert "test agent" in ap.system_prompt


def test_parse_missing_frontmatter_raises(tmp_path: Path):
    p = tmp_path / "broken.md"
    p.write_text("no frontmatter here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_agent_md(p)


def test_registry_loads_directory(tmp_path: Path):
    (tmp_path / "a.md").write_text(VALID_AGENT_MD, encoding="utf-8")
    (tmp_path / "b.md").write_text(
        VALID_AGENT_MD.replace("name: tester", "name: another"), encoding="utf-8"
    )
    reg = AgentRegistry(agents_dir=tmp_path)
    names = {a.name for a in reg.all()}
    assert names == {"tester", "another"}
    assert reg.get("tester") is not None
    assert reg.get("missing") is None


def test_registry_skips_invalid_files(tmp_path: Path, capsys):
    (tmp_path / "ok.md").write_text(VALID_AGENT_MD, encoding="utf-8")
    (tmp_path / "bad.md").write_text("totally not yaml frontmatter", encoding="utf-8")
    reg = AgentRegistry(agents_dir=tmp_path)
    assert {a.name for a in reg.all()} == {"tester"}


def test_match_score_prefers_overlap(tmp_path: Path):
    (tmp_path / "tester.md").write_text(VALID_AGENT_MD, encoding="utf-8")
    reg = AgentRegistry(agents_dir=tmp_path)
    score_match = reg.get("tester").match_score(["coding"])
    score_no_match = reg.get("tester").match_score(["something_else"])
    assert score_match > score_no_match


def test_describe_for_planner_lists_agents(tmp_path: Path):
    (tmp_path / "tester.md").write_text(VALID_AGENT_MD, encoding="utf-8")
    reg = AgentRegistry(agents_dir=tmp_path)
    text = reg.describe_for_planner()
    assert "tester" in text
    assert "coding" in text


def test_resolve_model_picks_available(tmp_path: Path, monkeypatch):
    (tmp_path / "tester.md").write_text(VALID_AGENT_MD, encoding="utf-8")
    reg = AgentRegistry(agents_dir=tmp_path)
    ap = reg.get("tester")

    from src.config import AppConfig, WorkerEntry

    cfg = AppConfig(
        workers_local=[WorkerEntry(model="bar:13b", provider="ollama")],
        workers_api=[],
    )
    assert ap.resolve_model(cfg) == "bar:13b"

    # over-budget mode prefers local-only candidates
    cfg2 = AppConfig(
        workers_local=[WorkerEntry(model="bar:13b", provider="ollama")],
        workers_api=[WorkerEntry(model="foo:7b", provider="litellm")],
    )
    assert ap.resolve_model(cfg2, over_budget=True) == "bar:13b"
