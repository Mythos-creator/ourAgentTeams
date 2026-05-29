"""RAG 写入前的安全裁剪/摘要：避免把整段最终输出（可能数万字符）丢进 Chroma。

提供两种策略：
- truncate(text, max_chars): 简单截断，附"...（已截断）"提示
- compact(text, max_chars): 头尾各保留一段，中间省略

这是一个独立模块；index_task_result 当前没有调用，但任何调用方在写入前都可以用。
"""

from __future__ import annotations

DEFAULT_MAX_CHARS = 2000
TRUNCATE_HINT = "\n…（已截断 {dropped} 字符）"


def truncate(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """从头部保留 max_chars 字符；超出部分用提示替换。"""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    return head + TRUNCATE_HINT.format(dropped=len(text) - max_chars)


def compact(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """头尾各保留一段，中间用省略号合并；用于保留两端信息的情况。"""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= max_chars:
        return text
    half = max(64, (max_chars - 32) // 2)
    head = text[:half]
    tail = text[-half:]
    dropped = len(text) - 2 * half
    return f"{head}\n…（中间省略 {dropped} 字符）…\n{tail}"


def safe_index_text(
    description: str,
    result_summary: str,
    *,
    max_desc_chars: int = 500,
    max_result_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, str]:
    """便捷封装：返回适合写 RAG 的 (description, result_summary)。"""
    return truncate(description, max_desc_chars), truncate(result_summary, max_result_chars)
