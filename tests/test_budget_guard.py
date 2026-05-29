"""Tests for monthly budget guard."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from src.cost.budget_guard import (
    add_monthly_spent, check_budget, load_monthly_spent,
)


def test_no_budget_always_allows():
    d = check_budget(monthly_budget_usd=0.0, upcoming_cost_usd=999.0, spent_usd=0.0)
    assert d.allow and d.reason == "no_budget_set"


def test_under_budget_ok():
    d = check_budget(monthly_budget_usd=10.0, upcoming_cost_usd=1.0, spent_usd=2.0)
    assert d.allow and d.reason == "ok"
    assert d.projected_usd == 3.0


def test_warn_threshold():
    d = check_budget(monthly_budget_usd=10.0, upcoming_cost_usd=1.0, spent_usd=8.0)
    assert d.allow and d.reason == "warn"
    assert d.ratio >= 0.8


def test_over_budget_blocks():
    d = check_budget(monthly_budget_usd=5.0, upcoming_cost_usd=1.0, spent_usd=5.0)
    assert not d.allow and d.reason == "over_budget"


def test_load_returns_zero_when_file_missing(tmp_path: Path):
    assert load_monthly_spent(tmp_path / "missing.json") == 0.0


def test_add_then_load_round_trip(tmp_path: Path):
    p = tmp_path / "usage.json"
    now = _dt.datetime(2026, 5, 15, 12, 0, 0)
    add_monthly_spent(0.50, p, now=now)
    add_monthly_spent(0.25, p, now=now)
    assert load_monthly_spent(p, now=now) == 0.75
    # different month independent
    other = _dt.datetime(2026, 6, 1)
    assert load_monthly_spent(p, now=other) == 0.0


def test_add_handles_corrupt_file(tmp_path: Path):
    p = tmp_path / "usage.json"
    p.write_text("not json", encoding="utf-8")
    add_monthly_spent(1.0, p, now=_dt.datetime(2026, 1, 1))
    data = json.loads(p.read_text())
    assert data["2026-01"] == 1.0
