"""Worker factory: 单一来源构造 BaseModelWorker。

把"按 provider 选 OllamaWorker / APIModelWorker"的逻辑集中到一个地方，避免
在 cli/interactive.py、orchestrator.py、agent_pool.py 各自重复实现。

接受三种来源：
- WorkerEntry (来自 cfg.workers_local / workers_api)
- LeaderConfig (来自 cfg.leader)
- 裸 model 字符串 + cfg（按 cfg 推断 provider；找不到时按 ollama 兜底）
"""

from __future__ import annotations

from typing import Any

from src.config import AppConfig, LeaderConfig, WorkerEntry
from src.models.api_model import APIModelWorker
from src.models.base import BaseModelWorker
from src.models.local_model import OllamaWorker


def create_worker_from_entry(entry: WorkerEntry, cfg: AppConfig | None = None) -> BaseModelWorker:
    """根据 WorkerEntry 构造 worker。"""
    base_url = cfg.leader.ollama_base_url if cfg else "http://localhost:11434"
    provider = (entry.provider or "ollama").lower()
    if provider == "ollama":
        return OllamaWorker(model=entry.model, base_url=base_url)
    # litellm / openai / anthropic / 其它都走 APIModelWorker
    return APIModelWorker(model=entry.model, api_key=entry.api_key)


def create_leader_worker(cfg: AppConfig) -> BaseModelWorker:
    """根据 cfg.leader.provider 构造 Leader worker。

    与 orchestrator.py 中 _create_leader_worker 的硬编码 OllamaWorker 不同，
    这里真正尊重 provider 字段，让用户可以把 Leader 也设成 API 模型。
    """
    leader = cfg.leader
    provider = (leader.provider or "ollama").lower()
    if provider == "ollama":
        return OllamaWorker(model=leader.model, base_url=leader.ollama_base_url)
    return APIModelWorker(model=leader.model)


def create_worker_for_model(model: str, cfg: AppConfig | None = None) -> BaseModelWorker:
    """按裸 model 名构造；从 cfg 中查 provider，找不到 → 按 ollama 兜底。"""
    if cfg is not None:
        for w in cfg.workers_local:
            if w.model == model:
                return create_worker_from_entry(w, cfg)
        for w in cfg.workers_api:
            if w.model == model:
                return create_worker_from_entry(w, cfg)
    base_url = cfg.leader.ollama_base_url if cfg else "http://localhost:11434"
    return OllamaWorker(model=model, base_url=base_url)
