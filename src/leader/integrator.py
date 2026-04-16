"""Result Integrator: review subtask outputs, score quality, and merge final result.

The Leader LLM reviews each subtask result, assigns a quality score,
and then produces a unified final deliverable from all subtask outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.models.base import BaseModelWorker
from src.leader.task_planner import Subtask


REVIEW_PROMPT = """\
你是团队的 Leader，正在 Review 一个子任务的执行结果。

## 子任务信息
标题: {title}
描述: {description}
执行模型: {model}

## 执行结果
{result}

## 评分标准
- 准确性：结果是否正确完成了任务要求
- 完整性：是否覆盖了所有要求的细节
- 格式规范：输出格式是否清晰可用

## 输出格式（严格 JSON）
```json
{{
  "quality_score": 8,
  "passed": true,
  "issues": [],
  "suggestions": "",
  "summary": "一句话评价"
}}
```
quality_score 范围 1-10。passed 为 false 表示需要返工。
只输出 JSON，不要附加其他文字。
"""


INTEGRATE_PROMPT = """\
你是团队的 Leader，所有子任务已完成。请将下面的子任务结果整合为一份完整的最终交付物。

## 原始任务
{original_task}

## 子任务结果
{subtask_results}

## 要求
1. 将各子任务结果合并为连贯的整体输出
2. 确保逻辑连贯、格式统一
3. 如有冲突，以更高质量的子任务输出为准
4. 输出最终交付物即可，不需要元信息
"""


@dataclass
class ReviewResult:
    subtask_id: str
    quality_score: float
    passed: bool
    issues: list[str] = field(default_factory=list)
    suggestions: str = ""
    summary: str = ""
    raw: str = ""


@dataclass
class IntegrationResult:
    final_output: str
    reviews: list[ReviewResult] = field(default_factory=list)
    total_quality_avg: float = 0.0


def _extract_json(text: str) -> dict:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
    if "```" in text:
        text = text.split("```", 1)[0]
    return json.loads(text.strip())


async def review_subtask(
    leader: BaseModelWorker,
    subtask: Subtask,
) -> ReviewResult:
    """Have the Leader LLM score a single subtask result."""
    prompt = REVIEW_PROMPT.format(
        title=subtask.title,
        description=subtask.description,
        model=subtask.assigned_model or "unknown",
        result=subtask.result or "(no result)",
    )

    resp = await leader.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1024,
    )

    try:
        data = _extract_json(resp.content)
    except (json.JSONDecodeError, IndexError):
        return ReviewResult(
            subtask_id=subtask.id,
            quality_score=5.0,
            passed=True,
            summary="Leader 未能生成结构化 Review，默认通过",
            raw=resp.content,
        )

    return ReviewResult(
        subtask_id=subtask.id,
        quality_score=float(data.get("quality_score", 5)),
        passed=bool(data.get("passed", True)),
        issues=data.get("issues", []),
        suggestions=data.get("suggestions", ""),
        summary=data.get("summary", ""),
        raw=resp.content,
    )


async def integrate_results(
    leader: BaseModelWorker,
    original_task: str,
    subtasks: list[Subtask],
    reviews: list[ReviewResult],
) -> IntegrationResult:
    """Merge all subtask results into a final deliverable."""
    parts: list[str] = []
    for st in subtasks:
        review = next((r for r in reviews if r.subtask_id == st.id), None)
        score_text = f" (质量分: {review.quality_score})" if review else ""
        parts.append(f"### {st.title}{score_text}\n{st.result or '(无结果)'}")

    prompt = INTEGRATE_PROMPT.format(
        original_task=original_task,
        subtask_results="\n\n".join(parts),
    )

    resp = await leader.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=8192,
    )

    scores = [r.quality_score for r in reviews if r.quality_score > 0]
    avg = sum(scores) / len(scores) if scores else 0.0

    return IntegrationResult(
        final_output=resp.content,
        reviews=reviews,
        total_quality_avg=round(avg, 2),
    )
