"""Task Planner: Leader analyses a user task and produces structured subtasks.

The Leader LLM receives the task description (possibly sanitized),
context from RAG, and available worker capabilities, then outputs a
structured JSON plan of subtasks with importance scores.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.models.base import BaseModelWorker

PLAN_PROMPT_TEMPLATE = """\
你是一个多智能体团队的 Leader，负责把用户任务拆解为可独立执行的子任务。

## 可用工具（MCP）
{tool_context}

## 可用工作模型及其擅长领域
{worker_capabilities}

## 用户偏好
{user_preferences}

## 过去类似任务的参考（RAG 检索结果）
{rag_context}

## 当前任务
{task_description}

## 你的职责
1. 分析任务目标、约束和所需能力
2. 将任务拆解为若干可并行或串行的子任务
3. 为每个子任务评估重要性（1-10，10最重要）
4. 标注子任务之间的依赖关系

## 输出格式（严格 JSON）
```json
{{
  "analysis": "对任务的整体分析（1-2句话）",
  "subtasks": [
    {{
      "id": "sub_1",
      "title": "子任务标题",
      "description": "详细描述，包含具体要求",
      "importance": 8,
      "required_skills": ["backend", "database"],
      "depends_on": [],
      "estimated_tokens": 2000
    }}
  ]
}}
```
只输出 JSON，不要附加任何其他文字。
"""


@dataclass
class Subtask:
    id: str
    title: str
    description: str
    importance: int = 5
    required_skills: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    estimated_tokens: int = 2000
    assigned_model: str | None = None
    status: str = "pending"
    result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "importance": self.importance,
            "required_skills": self.required_skills,
            "depends_on": self.depends_on,
            "estimated_tokens": self.estimated_tokens,
            "assigned_model": self.assigned_model,
            "status": self.status,
            "result": self.result,
        }


@dataclass
class TaskPlan:
    analysis: str
    subtasks: list[Subtask]
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis": self.analysis,
            "subtasks": [s.to_dict() for s in self.subtasks],
        }


def _build_capabilities_text(profiles: dict) -> str:
    lines: list[str] = []
    for model, p in profiles.items():
        strengths = p.get("strengths", [])
        verdict = p.get("verdict", {}).get("status", "unknown")
        avg_q = p.get("performance", {}).get("quality", {}).get("avg_score", "N/A")
        lines.append(f"- {model}: 擅长 {', '.join(strengths) or '未知'} | 质量均分 {avg_q} | 状态 {verdict}")
    return "\n".join(lines) or "无已知工作模型信息"


def _extract_json(text: str) -> dict:
    """Try to extract a JSON object from LLM output that may contain markdown fences."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
    if "```" in text:
        text = text.split("```", 1)[0]
    text = text.strip()
    return json.loads(text)


async def plan_task(
    leader: BaseModelWorker,
    task_description: str,
    *,
    worker_profiles: dict | None = None,
    user_preferences: str = "",
    rag_context: str = "",
    tool_context: str = "",
) -> TaskPlan:
    """Ask the Leader LLM to decompose a task into subtasks."""
    prompt = PLAN_PROMPT_TEMPLATE.format(
        worker_capabilities=_build_capabilities_text(worker_profiles or {}),
        user_preferences=user_preferences or "无已知偏好",
        rag_context=rag_context or "无参考信息",
        tool_context=tool_context or "当前未提供可调用工具",
        task_description=task_description,
    )

    resp = await leader.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4096,
    )

    try:
        data = _extract_json(resp.content)
    except (json.JSONDecodeError, IndexError):
        return TaskPlan(
            analysis="Leader 未能生成有效的结构化计划，回退为单任务执行",
            subtasks=[Subtask(
                id=f"sub_{uuid.uuid4().hex[:8]}",
                title="完整任务",
                description=task_description,
                importance=7,
            )],
            raw_response=resp.content,
        )

    subtasks = []
    for s in data.get("subtasks", []):
        subtasks.append(Subtask(
            id=s.get("id", f"sub_{uuid.uuid4().hex[:8]}"),
            title=s.get("title", "未命名子任务"),
            description=s.get("description", ""),
            importance=int(s.get("importance", 5)),
            required_skills=s.get("required_skills", []),
            depends_on=s.get("depends_on", []),
            estimated_tokens=int(s.get("estimated_tokens", 2000)),
        ))

    if not subtasks:
        subtasks = [Subtask(
            id=f"sub_{uuid.uuid4().hex[:8]}",
            title="完整任务",
            description=task_description,
            importance=7,
        )]

    return TaskPlan(
        analysis=data.get("analysis", ""),
        subtasks=subtasks,
        raw_response=resp.content,
    )
