"""Tests for dry-run planner.

We pass cfg=None so it tolerates missing config; AgentRegistry uses real agents/.
"""

from __future__ import annotations


from src.cli.dry_run import dry_run


def test_dry_run_short_query_classifies_single():
    rep = dry_run("帮我把这段代码加注释", cfg=None)
    assert rep.mode in {"single", "team"}  # should not be unknown
    assert rep.query.startswith("帮我")


def test_dry_run_with_subtasks_marks_team_and_orders():
    subtasks = [
        {"id": "a", "description": "first", "depends_on": []},
        {"id": "b", "description": "second", "depends_on": ["a"]},
        {"id": "c", "description": "third", "depends_on": ["b"]},
    ]
    rep = dry_run("multi-step", cfg=None, subtasks=subtasks)
    assert rep.mode == "team"
    assert rep.sorted_order == ["a", "b", "c"]
    assert rep.issues == []


def test_dry_run_detects_cycle():
    subtasks = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    rep = dry_run("oops", cfg=None, subtasks=subtasks)
    assert any("Circular" in i or "cycle" in i.lower() for i in rep.issues)


def test_dry_run_to_text_contains_basics():
    rep = dry_run("hello world", cfg=None)
    txt = rep.to_text()
    assert "Query:" in txt
    assert "Mode:" in txt


def test_dry_run_to_json_parses():
    import json as _j
    rep = dry_run("hi", cfg=None)
    parsed = _j.loads(rep.to_json())
    assert parsed["query"] == "hi"
