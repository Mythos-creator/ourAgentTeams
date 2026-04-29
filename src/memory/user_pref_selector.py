"""Select task-relevant user preferences before planning/execution.

Instead of always injecting the entire user profile summary, we ask the
Leader to select only preferences relevant to the current task. This keeps
context focused and reduces prompt noise.
"""

from __future__ import annotations

import json
from typing import Any

from src.models.base import BaseModelWorker

PREF_SELECT_PROMPT = """\
你是用户偏好筛选器。请根据“当前任务”从“用户偏好档案”中挑选最相关、最可执行的偏好。

## 当前任务
{task_description}

## 用户偏好档案（JSON）
{profile_json}

## 输出要求（严格 JSON）
```json
{{
  "relevant_preferences": "只保留与当前任务强相关的偏好（1-6条，简洁）",
  "confidence": 0.0
}}
```

规则：
1) 若当前任务与偏好关联弱，返回保守简短内容，不要编造
2) 只输出可指导执行/表达风格的偏好
3) 只输出 JSON，不要附加其他文字
"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
    if "```" in text:
        text = text.split("```", 1)[0]
    return json.loads(text.strip())


def _profile_for_prompt(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "natural_language_summary": profile.get("natural_language_summary", ""),
        "dimensions": profile.get("dimensions", {}),
        "interaction_history_summary": profile.get("interaction_history_summary", ""),
    }


async def select_user_preferences(
    leader: BaseModelWorker,
    task_description: str,
    profile: dict[str, Any],
) -> str:
    """Return only task-relevant preference summary selected by the Leader."""
    fallback = str(profile.get("natural_language_summary", "")).strip()
    if not profile:
        return fallback

    try:
        prompt = PREF_SELECT_PROMPT.format(
            task_description=task_description[:1600],
            profile_json=json.dumps(_profile_for_prompt(profile), ensure_ascii=False, indent=2),
        )
        resp = await leader.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
        )
        data = _extract_json(resp.content)
        selected = str(data.get("relevant_preferences", "")).strip()
        return selected or fallback
    except Exception:
        return fallback

