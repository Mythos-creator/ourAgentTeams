"""Transcript trimming utilities for chat history.

Single 模式当前用一个简单截断函数容易切掉头部 system 消息（参考 P1-6）。
本模块提供更稳健的版本：始终保留所有 system 消息，只裁剪 user/assistant 配对。
"""

from __future__ import annotations

from typing import Iterable


def cap_transcript_keep_system(
    messages: list[dict[str, str]],
    *,
    max_user_assistant_pairs: int = 10,
    max_total_chars: int | None = None,
) -> list[dict[str, str]]:
    """裁剪会话历史，但始终保留所有 system 消息。

    - 优先按"最近 N 轮 user/assistant 对"保留。
    - 如果 `max_total_chars` 指定，再额外按字符总量从老到新丢弃多余的对。
    - 函数返回新列表；不修改入参。
    """
    systems = [m for m in messages if m.get("role") == "system"]
    body = [m for m in messages if m.get("role") != "system"]

    pairs: list[tuple[dict[str, str], dict[str, str] | None]] = []
    i = 0
    while i < len(body):
        if body[i].get("role") == "user":
            assistant = body[i + 1] if i + 1 < len(body) and body[i + 1].get("role") == "assistant" else None
            pairs.append((body[i], assistant))
            i += 2 if assistant else 1
        else:
            # 落单的 assistant：单独保留，不配对
            pairs.append((body[i], None))
            i += 1

    if max_user_assistant_pairs == 0:
        pairs = []
    elif max_user_assistant_pairs > 0:
        pairs = pairs[-max_user_assistant_pairs:]

    if max_total_chars is not None:
        sys_chars = sum(len(m.get("content", "")) for m in systems)
        budget = max(0, max_total_chars - sys_chars)
        kept_reversed: list[tuple[dict, dict | None]] = []
        for u, a in reversed(pairs):
            cost = len(u.get("content", "")) + (len(a.get("content", "")) if a else 0)
            if budget - cost < 0 and kept_reversed:
                break
            budget -= cost
            kept_reversed.append((u, a))
        pairs = list(reversed(kept_reversed))

    out: list[dict[str, str]] = list(systems)
    for u, a in pairs:
        out.append(u)
        if a is not None:
            out.append(a)
    return out


def transcript_chars(messages: Iterable[dict[str, str]]) -> int:
    return sum(len(m.get("content", "")) for m in messages)
