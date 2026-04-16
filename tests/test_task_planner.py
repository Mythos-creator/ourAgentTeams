"""Tests for task planner JSON extraction and subtask creation."""

from src.leader.task_planner import Subtask, TaskPlan, _extract_json


def test_extract_json_plain():
    text = '{"analysis": "test", "subtasks": []}'
    data = _extract_json(text)
    assert data["analysis"] == "test"


def test_extract_json_with_fences():
    text = 'Some text\n```json\n{"analysis": "test", "subtasks": []}\n```\nMore text'
    data = _extract_json(text)
    assert data["analysis"] == "test"


def test_subtask_to_dict():
    st = Subtask(
        id="sub_1",
        title="Test",
        description="Do something",
        importance=8,
        required_skills=["backend"],
    )
    d = st.to_dict()
    assert d["id"] == "sub_1"
    assert d["importance"] == 8
    assert d["status"] == "pending"


def test_task_plan_to_dict():
    plan = TaskPlan(
        analysis="Test analysis",
        subtasks=[
            Subtask(id="s1", title="T1", description="D1"),
            Subtask(id="s2", title="T2", description="D2"),
        ],
    )
    d = plan.to_dict()
    assert len(d["subtasks"]) == 2
    assert d["analysis"] == "Test analysis"
