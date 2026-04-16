"""Tests for Cost Calculator."""

from src.cost.calculator import (
    CostTracker,
    count_tokens,
    estimate_cost,
    pick_tier,
    should_use_local,
)


def test_count_tokens_basic():
    tokens = count_tokens("Hello, world!")
    assert tokens > 0


def test_estimate_cost_known_model():
    cost = estimate_cost(1000, 500, "gpt-4o")
    assert cost > 0


def test_estimate_cost_unknown_model():
    cost = estimate_cost(1000, 500, "totally-unknown-model")
    assert cost == 0.0


def test_cost_tracker():
    tracker = CostTracker(budget_usd=1.0)
    assert not tracker.over_budget

    tracker.record(1000, 500, 0.5)
    assert tracker.spent_usd == 0.5
    assert tracker.remaining_usd == 0.5

    tracker.record(1000, 500, 0.6)
    assert tracker.over_budget


def test_pick_tier():
    assert pick_tier(3, threshold_local=5, threshold_best=8) == "local"
    assert pick_tier(6, threshold_local=5, threshold_best=8) == "mid"
    assert pick_tier(9, threshold_local=5, threshold_best=8) == "best"


def test_should_use_local():
    assert should_use_local(3, threshold_local=5) is True
    assert should_use_local(7, threshold_local=5) is False
    assert should_use_local(9, threshold_local=5, budget_remaining=0) is True
