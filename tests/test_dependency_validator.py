"""Tests for src.leader.dependency_validator (cycles + topo + ready set)."""

from __future__ import annotations

import pytest

from src.leader.dependency_validator import (
    DependencyCycleError, DependencyValidationError,
    detect_cycle, get_ready_tasks, topological_sort, validate_dependencies,
)


def test_no_cycle_returns_none():
    subs = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
    ]
    assert detect_cycle(subs) is None


def test_simple_cycle_raises():
    subs = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(DependencyCycleError):
        detect_cycle(subs)


def test_self_loop_raises():
    subs = [{"id": "a", "depends_on": ["a"]}]
    with pytest.raises(DependencyCycleError):
        detect_cycle(subs)


def test_three_node_cycle_raises():
    subs = [
        {"id": "a", "depends_on": ["c"]},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    ]
    with pytest.raises(DependencyCycleError):
        detect_cycle(subs)


def test_validate_unknown_dep_raises():
    subs = [
        {"id": "a", "depends_on": ["x"]},
    ]
    with pytest.raises(DependencyValidationError):
        validate_dependencies(subs)


def test_topological_sort_linear_chain():
    subs = [
        {"id": "c", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
        {"id": "a", "depends_on": []},
    ]
    out = topological_sort(subs)
    ids = [s["id"] for s in out]
    assert ids.index("a") < ids.index("b") < ids.index("c")


def test_topological_sort_diamond():
    # a -> b, a -> c, b -> d, c -> d
    subs = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a"]},
        {"id": "d", "depends_on": ["b", "c"]},
    ]
    out = [s["id"] for s in topological_sort(subs)]
    assert out.index("a") == 0
    assert out.index("d") == 3
    assert out.index("a") < out.index("b") < out.index("d")
    assert out.index("a") < out.index("c") < out.index("d")


def test_topological_sort_cycle_raises():
    subs = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(DependencyCycleError):
        topological_sort(subs)


def test_get_ready_tasks_initial_state():
    subs = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a"]},
    ]
    ready = get_ready_tasks(subs, completed_ids=set())
    assert {s["id"] for s in ready} == {"a"}


def test_get_ready_tasks_after_a_done():
    # a is marked completed via status field, b/c should now be ready
    subs = [
        {"id": "a", "depends_on": [], "status": "completed"},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a"]},
    ]
    ready = get_ready_tasks(subs, completed_ids={"a"})
    assert {s["id"] for s in ready} == {"b", "c"}


def test_get_ready_skips_completed():
    subs = [
        {"id": "a", "depends_on": [], "status": "completed"},
        {"id": "b", "depends_on": ["a"]},
    ]
    ready = get_ready_tasks(subs, completed_ids={"a"})
    assert {s["id"] for s in ready} == {"b"}


def test_empty_input():
    assert detect_cycle([]) is None
    validate_dependencies([])
    assert topological_sort([]) == []
    assert get_ready_tasks([], set()) == []


def test_disconnected_components_topo():
    # two independent chains
    subs = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "x", "depends_on": []},
        {"id": "y", "depends_on": ["x"]},
    ]
    out = [s["id"] for s in topological_sort(subs)]
    assert out.index("a") < out.index("b")
    assert out.index("x") < out.index("y")
