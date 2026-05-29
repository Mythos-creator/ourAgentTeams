"""AgentInstance: 把 AgentProfile（配方） + BaseModelWorker（底座） + 运行时状态绑在一起。

设计要点
--------
- 同一个模型可以被多个 AgentInstance 共享。
- 同一个 AgentProfile 可以被实例化为**多个** AgentInstance；每个实例可以独立
  覆写 system_prompt / temperature / max_tokens / name，从而形成不同的 Agent
  排进队列里（即"模型相同、配置不同 ⇒ 两个 Agent"）。
- 实例自己持有：状态机（idle/busy/cooldown/error）、错误计数、对话历史。

不依赖 orchestrator —— 可独立使用，也可被 AgentPool 管理。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.models.base import BaseModelWorker, ModelResponse

from .agent_registry import AgentProfile


class AgentState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    COOLDOWN = "cooldown"  # 临时冷却，错误后等待恢复
    ERROR = "error"        # 连续错误超过阈值，需手动恢复


@dataclass
class AgentInstance:
    """一个可调度、可调用的 Agent 实例。

    - `profile` 提供基础配方（system prompt、采样参数、技能标签等）
    - `worker`  是当下绑定的模型工作器（可在创建时由 profile.resolve_model 决定，
                 也可由调用方手动注入，便于在 pool 中混用本地/云端模型）
    - 任意 `*_override` 字段非 None 时覆盖 profile 同名字段；这是"同模型、不同
      配置 ⇒ 不同 Agent"的实现钥匙。
    """

    profile: AgentProfile
    worker: BaseModelWorker
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name_override: str | None = None
    system_prompt_override: str | None = None
    temperature_override: float | None = None
    max_tokens_override: int | None = None
    skills_override: list[str] | None = None
    allowed_tools: list[str] | None = None  # MCP 工具白名单；None=全开，[]=全禁
    tags: dict[str, str] = field(default_factory=dict)  # 自由元数据，便于路由/调试

    # 运行时状态（不可序列化字段）
    state: AgentState = AgentState.IDLE
    history: list[dict[str, str]] = field(default_factory=list)
    busy_since: float | None = None
    last_finished_at: float | None = None
    error_count: int = 0
    completed_count: int = 0

    # ── 视图属性（覆盖 > profile） ────────────────────────────────────

    @property
    def name(self) -> str:
        return self.name_override or self.profile.name

    @property
    def display_name(self) -> str:
        return f"{self.name}#{self.instance_id}"

    @property
    def system_prompt(self) -> str:
        return self.system_prompt_override if self.system_prompt_override is not None else self.profile.system_prompt

    @property
    def temperature(self) -> float:
        return self.temperature_override if self.temperature_override is not None else self.profile.temperature

    @property
    def max_tokens(self) -> int:
        return self.max_tokens_override if self.max_tokens_override is not None else self.profile.max_tokens

    @property
    def skills(self) -> list[str]:
        return list(self.skills_override) if self.skills_override is not None else list(self.profile.required_skills)

    @property
    def model(self) -> str:
        return self.worker.model

    @property
    def is_idle(self) -> bool:
        return self.state == AgentState.IDLE

    @property
    def is_busy(self) -> bool:
        return self.state == AgentState.BUSY

    @property
    def is_available(self) -> bool:
        return self.state in (AgentState.IDLE, AgentState.COOLDOWN)

    # ── 状态机 ──────────────────────────────────────────────────────

    def mark_busy(self) -> None:
        self.state = AgentState.BUSY
        self.busy_since = time.time()

    def mark_idle(self) -> None:
        self.state = AgentState.IDLE
        self.busy_since = None
        self.last_finished_at = time.time()

    def mark_cooldown(self) -> None:
        self.state = AgentState.COOLDOWN
        self.busy_since = None

    def mark_error(self) -> None:
        self.state = AgentState.ERROR
        self.busy_since = None

    def reset(self) -> None:
        """重置到全新状态（清历史、清错误计数、回到 IDLE）。"""
        self.state = AgentState.IDLE
        self.history.clear()
        self.busy_since = None
        self.last_finished_at = None
        self.error_count = 0

    def reset_history(self) -> None:
        self.history.clear()

    # ── 推理 ────────────────────────────────────────────────────────

    def _build_messages(
        self,
        user_input: str,
        *,
        extra_system: str | None = None,
        use_history: bool = False,
        context_messages: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = []
        sys_parts: list[str] = []
        if self.system_prompt:
            sys_parts.append(self.system_prompt)
        if extra_system:
            sys_parts.append(extra_system)
        if sys_parts:
            msgs.append({"role": "system", "content": "\n\n".join(sys_parts)})
        if use_history and self.history:
            msgs.extend(self.history)
        if context_messages:
            msgs.extend(context_messages)
        msgs.append({"role": "user", "content": user_input})
        return msgs

    async def run(
        self,
        user_input: str,
        *,
        extra_system: str | None = None,
        use_history: bool = False,
        record_history: bool = False,
        context_messages: list[dict[str, str]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        """执行一次推理。

        参数语义：
        - `extra_system`: 在实例 system prompt 后追加一段（如任务说明）。
        - `use_history`: 把实例历史拼进消息（多轮对话场景）。
        - `record_history`: 把本轮 user/assistant 写回 self.history。
        - `context_messages`: 临时上下文（不入历史，例如检索结果）。
        - `temperature` / `max_tokens`: 单次调用覆盖。
        """
        msgs = self._build_messages(
            user_input,
            extra_system=extra_system,
            use_history=use_history,
            context_messages=context_messages,
        )
        try:
            resp = await self.worker.chat(
                messages=msgs,
                temperature=temperature if temperature is not None else self.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            )
        except Exception:
            self.error_count += 1
            raise
        else:
            self.completed_count += 1
            if record_history:
                self.history.append({"role": "user", "content": user_input})
                self.history.append({"role": "assistant", "content": resp.content})
            return resp

    # ── 序列化 ──────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "profile": self.profile.name,
            "model": self.model,
            "state": self.state.value,
            "skills": self.skills,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "completed": self.completed_count,
            "errors": self.error_count,
            "allowed_tools": list(self.allowed_tools) if self.allowed_tools is not None else None,
            "tags": dict(self.tags),
        }

    def __repr__(self) -> str:
        return (
            f"AgentInstance({self.display_name}, model={self.model}, "
            f"state={self.state.value}, T={self.temperature})"
        )
