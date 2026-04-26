"""Query Router: Leader classifies each user query and routes to the best available model.

Flow (Single mode):
  1. Leader LLM does a lightweight classification (category, needs_tools, preferred_skill, complexity)
  2. Router matches classification against available workers' strengths + historical profiles
  3. Returns the chosen WorkerEntry (or None → Leader answers itself) plus any user-facing hints
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.config import AppConfig, WorkerEntry
from src.memory.capability_store import get_profile
from src.models.base import BaseModelWorker

# ── Classification prompt (kept short for fast inference) ────────────────────

CLASSIFY_PROMPT = """\
你是一个路由分类器，根据用户的输入判断其领域和所需能力。

## 用户输入
{user_input}

## 输出格式（严格 JSON，不要附加任何其他文字）
```json
{{
  "category": "<reasoning|knowledge|coding|creative|general>",
  "needs_tools": <true|false>,
  "preferred_skill": "<math|backend|frontend|database|devops|writing|research|analysis|general>",
  "complexity": "<simple|complex>"
}}
```
分类说明:
- category: reasoning=数理逻辑推理, knowledge=事实/百科/检索, coding=编程开发, creative=文案创意, general=闲聊/其他
- needs_tools: 是否需要查阅资料/搜索/调用工具才能回答好
- preferred_skill: 最匹配的细化能力标签
- complexity: simple=单步可完成, complex=需要多步骤/多角色协作

只输出 JSON。"""


# ── Skill → category mapping (for matching workers) ─────────────────────────

_CATEGORY_SKILLS: dict[str, set[str]] = {
    "reasoning": {"reasoning", "math", "logic", "analysis", "thinking"},
    "knowledge": {"knowledge", "research", "rag", "search"},
    "coding": {"coding", "backend", "frontend", "database", "devops", "code_review"},
    "creative": {"creative", "writing", "design", "copywriting"},
    "general": {"general"},
}


@dataclass
class ClassifyResult:
    category: str = "general"
    needs_tools: bool = False
    preferred_skill: str = "general"
    complexity: str = "simple"
    raw: str = ""


@dataclass
class RouteResult:
    worker: WorkerEntry | None = None
    hint: str = ""
    classify: ClassifyResult = field(default_factory=ClassifyResult)
    is_fallback: bool = False


def _extract_json(text: str) -> dict:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
    if "```" in text:
        text = text.split("```", 1)[0]
    return json.loads(text.strip())


async def classify(leader: BaseModelWorker, user_input: str) -> ClassifyResult:
    """Ask Leader to classify a user query (single fast LLM call)."""
    prompt = CLASSIFY_PROMPT.format(user_input=user_input)
    resp = await leader.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=256,
    )
    try:
        data = _extract_json(resp.content)
    except (json.JSONDecodeError, IndexError):
        return ClassifyResult(raw=resp.content)

    return ClassifyResult(
        category=data.get("category", "general"),
        needs_tools=bool(data.get("needs_tools", False)),
        preferred_skill=data.get("preferred_skill", "general"),
        complexity=data.get("complexity", "simple"),
        raw=resp.content,
    )


def _worker_skill_score(worker: WorkerEntry, cr: ClassifyResult) -> float:
    """Score a worker's fit for a classified query."""
    target_skills = _CATEGORY_SKILLS.get(cr.category, set()) | {cr.preferred_skill}
    w_strengths = {s.lower() for s in worker.strengths}
    overlap = len(target_skills & w_strengths)

    profile = get_profile(worker.model)
    avg_q = profile.get("performance", {}).get("quality", {}).get("avg_score", 5.0)

    return overlap * 3.0 + avg_q


_CATEGORY_MODEL_HINTS: dict[str, str] = {
    "reasoning": "deepseek-r1 / qwen3 等 thinking 模型",
    "knowledge": "支持 RAG 检索或长上下文的模型",
    "coding": "专长编码的模型（如 deepseek-coder, qwen-coder 等）",
    "creative": "创意写作模型",
}


def route(cr: ClassifyResult, cfg: AppConfig) -> RouteResult:
    """Pick the best available worker for a classified query.

    Returns RouteResult with worker=None when Leader should answer itself.
    """
    all_workers = cfg.workers_local + cfg.workers_api
    if not all_workers:
        return RouteResult(
            worker=None,
            hint="当前没有可用的工作模型，Leader 将直接回答。",
            classify=cr,
            is_fallback=True,
        )

    scored: list[tuple[WorkerEntry, float]] = []
    for w in all_workers:
        scored.append((w, _worker_skill_score(w, cr)))

    scored.sort(key=lambda x: x[1], reverse=True)
    best_worker, best_score = scored[0]

    # If best match has zero skill overlap, it's a fallback
    target_skills = _CATEGORY_SKILLS.get(cr.category, set()) | {cr.preferred_skill}
    w_strengths = {s.lower() for s in best_worker.strengths}
    has_overlap = bool(target_skills & w_strengths)

    hint = ""
    is_fallback = False
    if not has_overlap:
        suggestion = _CATEGORY_MODEL_HINTS.get(cr.category, "")
        if suggestion:
            hint = (
                f"当前没有擅长「{cr.category}」类问题的模型，"
                f"建议 `ollama pull` {suggestion} 或配置对应 API worker。"
                f"Leader 将尽力回答。"
            )
        is_fallback = True

    return RouteResult(
        worker=best_worker,
        hint=hint,
        classify=cr,
        is_fallback=is_fallback,
    )
