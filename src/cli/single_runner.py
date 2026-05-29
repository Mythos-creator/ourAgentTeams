"""Single 模式安全执行包装：对外发到云端 API 的请求做隐私扫描和回填。

设计成一个独立可调用的函数，便于（1）现有 interactive.py 切过来，
（2）任何脚本/测试单独使用。

策略：
- 选中 worker 的 provider != "ollama" 时，认定为"会出本机"，对最近一条
  user 消息做 sanitize；返回后对 assistant 输出做 restore。
- provider == "ollama" 或本地路径时，不做替换（仅可选审计写日志）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config import AppConfig
from src.models.base import BaseModelWorker, ModelResponse
from src.privacy.guard import PrivacyGuard, SanitizeResult


def is_remote_worker(worker: BaseModelWorker, cfg: AppConfig | None = None) -> bool:
    """判定一个 worker 是否会让数据离开本机。

    OllamaWorker → False
    APIModelWorker → True
    其它子类 → 默认按 True 处理（保守）
    """
    cls = type(worker).__name__
    if cls == "OllamaWorker":
        return False
    if cls == "APIModelWorker":
        return True
    # 兜底：未知 worker 默认认为是远端，触发脱敏
    return True


@dataclass
class SingleRunOutcome:
    response: ModelResponse
    sanitized: bool
    entity_count: int


async def run_single_safely(
    worker: BaseModelWorker,
    messages: list[dict[str, str]],
    *,
    cfg: AppConfig | None = None,
    guard: PrivacyGuard | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> SingleRunOutcome:
    """执行一次 chat，自动对远端调用做隐私扫描 + 回填。

    - messages 内最后一条 role=user 的 content 会被 sanitize
    - 返回的 ModelResponse.content 会被 restore
    - 若 guard=None 且 cfg 提供，会按 cfg.privacy.enabled 决定是否启用
    """
    privacy_enabled = bool(cfg and cfg.privacy.enabled)
    use_guard = guard or (PrivacyGuard() if privacy_enabled else None)

    sanitized_msgs = list(messages)
    sanitize_result: SanitizeResult | None = None

    if use_guard and is_remote_worker(worker, cfg):
        # 找到最后一条 user 消息做替换
        for i in range(len(sanitized_msgs) - 1, -1, -1):
            if sanitized_msgs[i].get("role") == "user":
                original = sanitized_msgs[i].get("content", "")
                sanitize_result = use_guard.sanitize(original)
                sanitized_msgs[i] = {**sanitized_msgs[i], "content": sanitize_result.sanitized}
                break

    resp = await worker.chat(
        messages=sanitized_msgs, temperature=temperature, max_tokens=max_tokens,
    )

    if sanitize_result and sanitize_result.has_sensitive:
        restored = use_guard.restore(resp.content, sanitize_result.placeholder_map)
        resp = ModelResponse(
            content=restored,
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens,
            elapsed_s=resp.elapsed_s,
            raw=resp.raw,
        )

    return SingleRunOutcome(
        response=resp,
        sanitized=bool(sanitize_result and sanitize_result.has_sensitive),
        entity_count=len(sanitize_result.spans) if sanitize_result else 0,
    )
