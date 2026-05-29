"""AgentPool: 管理一组 AgentInstance，提供"按技能 acquire / release"语义。

关键能力
--------
1. 同一个 AgentProfile 可在 pool 里实例化多份；同一个底层模型可被多个 profile 共享。
2. **同模型 + 不同配置 = 队列里的两个独立 Agent**：
   通过 `add_variant(profile, ..., temperature=, system_prompt=, name=)`
   即可把同一个模型注册成多个配置不同的 Agent 实例，它们在调度上完全独立。
3. acquire 选择规则：
     - 仅在 IDLE/COOLDOWN 中挑选
     - 用 skill 重叠度打分，分高者优先
     - 同分时，挑 completed_count 最少的（负载均衡）
4. 提供 async context manager `lease(skills=...)`，自动 release，避免泄漏。
5. 可选 `dispatch(skills, prompt, ...)` 一步完成 acquire→run→release。

模块独立，不耦合 orchestrator。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from src.config import AppConfig
from src.models.base import BaseModelWorker, ModelResponse

from .agent_instance import AgentInstance, AgentState
from .agent_registry import AgentProfile, AgentRegistry


# 工厂函数签名：profile + model_id → BaseModelWorker
WorkerFactory = Callable[[AgentProfile, str], BaseModelWorker]


class NoAgentAvailableError(RuntimeError):
    """池子里有匹配的 Agent 但全部 busy / error，超时仍未拿到。"""


class NoMatchingAgentError(RuntimeError):
    """池子里压根没有满足 skills 的 Agent。"""


def _score(instance: AgentInstance, required_skills: list[str]) -> float:
    """技能重叠度 + 轻微的负载均衡偏置。返回越大越优。"""
    req = {s.lower() for s in required_skills}
    own = {s.lower() for s in instance.skills}
    overlap = len(req & own)
    # 完成数越多，权重轻微下降，避免热点
    load_penalty = instance.completed_count * 0.01
    return overlap * 3.0 - load_penalty


@dataclass
class _Waiter:
    skills: list[str]
    future: asyncio.Future


class AgentPool:
    """异步安全的 Agent 实例池。"""

    def __init__(
        self,
        cfg: AppConfig | None = None,
        *,
        worker_factory: WorkerFactory | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self._worker_factory = worker_factory
        self._instances: list[AgentInstance] = []
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)

    # ── 实例注册 ────────────────────────────────────────────────────

    def add(self, instance: AgentInstance) -> AgentInstance:
        """直接把已经构造好的 AgentInstance 加入池子。"""
        self._instances.append(instance)
        return instance

    def add_from_profile(
        self,
        profile: AgentProfile,
        *,
        model: str | None = None,
        worker: BaseModelWorker | None = None,
        count: int = 1,
        **overrides: Any,
    ) -> list[AgentInstance]:
        """根据 profile 构造 N 个实例，加入池子。

        - `model`: 显式指定底层模型；不传则用 `profile.resolve_model(cfg)`。
        - `worker`: 直接注入已有的 worker（高优先级，覆盖 model 参数）。
        - `count`: 同配置克隆几个（用于并发同质 Agent）。
        - `**overrides`: 透传给 AgentInstance 的 *_override 字段，例如
              add_from_profile(coder_profile, temperature_override=0.0,
                               name_override="coder-strict")
          这就是"同模型 + 不同配置 ⇒ 不同 Agent"的入口。
        """
        if worker is None:
            chosen_model = model or profile.resolve_model(self.cfg) if self.cfg else model
            if chosen_model is None:
                raise ValueError(
                    f"无法为 profile={profile.name} 解析底层模型；"
                    "请显式传入 model= 或 worker="
                )
            worker = self._make_worker(profile, chosen_model)

        created: list[AgentInstance] = []
        for _ in range(count):
            inst = AgentInstance(profile=profile, worker=worker, **overrides)
            self._instances.append(inst)
            created.append(inst)
        return created

    def add_variant(
        self,
        profile_name: str,
        *,
        model: str | None = None,
        worker: BaseModelWorker | None = None,
        name: str | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        skills: list[str] | None = None,
        tags: dict[str, str] | None = None,
        count: int = 1,
    ) -> list[AgentInstance]:
        """便捷入口：从已注册的 profile 名字派生一个"变体" Agent。

        典型用法 —— 同一个 qwen2.5:14b 模型分别做严谨和发散两种 coder：
            pool.add_variant("coder", temperature=0.0, name="coder-strict")
            pool.add_variant("coder", temperature=0.9, name="coder-creative")
        队列里就出现两个独立 Agent，调度/状态互不影响。
        """
        if self.registry is None:
            raise ValueError("add_variant 需要 AgentPool 持有 AgentRegistry")
        profile = self.registry.get(profile_name)
        if profile is None:
            raise ValueError(f"profile 不存在：{profile_name}")

        return self.add_from_profile(
            profile,
            model=model,
            worker=worker,
            count=count,
            name_override=name,
            system_prompt_override=system_prompt,
            temperature_override=temperature,
            max_tokens_override=max_tokens,
            skills_override=skills,
            tags=tags or {},
        )

    def _make_worker(self, profile: AgentProfile, model: str) -> BaseModelWorker:
        if self._worker_factory is not None:
            return self._worker_factory(profile, model)
        # 默认工厂：根据 cfg 决定 Ollama 还是 LiteLLM
        return _default_worker_factory(self.cfg, model)

    # ── 查询 ────────────────────────────────────────────────────────

    def all(self) -> list[AgentInstance]:
        return list(self._instances)

    def __len__(self) -> int:
        return len(self._instances)

    def find_by_id(self, instance_id: str) -> AgentInstance | None:
        for ins in self._instances:
            if ins.instance_id == instance_id:
                return ins
        return None

    def find_by_name(self, name: str) -> list[AgentInstance]:
        return [ins for ins in self._instances if ins.name == name]

    def candidates_for(self, required_skills: list[str]) -> list[AgentInstance]:
        return [ins for ins in self._instances if _score(ins, required_skills) > 0]

    def snapshot(self) -> list[dict[str, Any]]:
        return [ins.to_dict() for ins in self._instances]

    # ── 调度 ────────────────────────────────────────────────────────

    async def acquire(
        self,
        required_skills: list[str] | None = None,
        *,
        timeout: float | None = None,
        allow_no_match: bool = False,
    ) -> AgentInstance:
        """阻塞式获取一个空闲 Agent。

        - required_skills 为空 → 任何 IDLE Agent 都可以。
        - 没有任何 Agent 与 skills 匹配 → NoMatchingAgentError（除非 allow_no_match）。
        - 匹配但全 busy → 等待，直到有人 release 或 timeout。
        """
        skills = required_skills or []

        async with self._cond:
            if skills and not allow_no_match:
                if not any(_score(ins, skills) > 0 for ins in self._instances):
                    raise NoMatchingAgentError(
                        f"池子里没有满足 skills={skills} 的 Agent"
                    )

            async def _try_pick() -> AgentInstance | None:
                pickable = [
                    ins for ins in self._instances
                    if ins.is_available and (not skills or _score(ins, skills) > 0 or allow_no_match)
                ]
                if not pickable:
                    return None
                pickable.sort(key=lambda i: (_score(i, skills), -i.completed_count), reverse=True)
                return pickable[0]

            try:
                async def _wait_loop() -> AgentInstance:
                    while True:
                        chosen = await _try_pick()
                        if chosen is not None:
                            chosen.mark_busy()
                            return chosen
                        await self._cond.wait()

                if timeout is None:
                    return await _wait_loop()
                return await asyncio.wait_for(_wait_loop(), timeout=timeout)
            except asyncio.TimeoutError as e:
                raise NoAgentAvailableError(
                    f"在 {timeout}s 内未拿到匹配 skills={skills} 的空闲 Agent"
                ) from e

    async def release(self, instance: AgentInstance) -> None:
        async with self._cond:
            if instance.state == AgentState.BUSY:
                instance.mark_idle()
            self._cond.notify_all()

    @contextlib.asynccontextmanager
    async def lease(
        self,
        required_skills: list[str] | None = None,
        *,
        timeout: float | None = None,
        allow_no_match: bool = False,
    ) -> AsyncIterator[AgentInstance]:
        agent = await self.acquire(
            required_skills, timeout=timeout, allow_no_match=allow_no_match,
        )
        try:
            yield agent
        finally:
            await self.release(agent)

    async def dispatch(
        self,
        prompt: str,
        *,
        required_skills: list[str] | None = None,
        timeout: float | None = None,
        extra_system: str | None = None,
        use_history: bool = False,
        record_history: bool = False,
    ) -> tuple[AgentInstance, ModelResponse]:
        """一站式：拿 Agent → run → 释放。返回 (实例, 响应)。"""
        async with self.lease(required_skills, timeout=timeout) as agent:
            resp = await agent.run(
                prompt,
                extra_system=extra_system,
                use_history=use_history,
                record_history=record_history,
            )
            return agent, resp


def _default_worker_factory(cfg: AppConfig | None, model: str) -> BaseModelWorker:
    """默认 worker 工厂：根据配置识别本地/云端，否则按 Ollama 处理。

    放在模块级以便测试时 monkeypatch；同时避免顶层 import 触发可选依赖。
    """
    from src.models.api_model import APIModelWorker
    from src.models.local_model import OllamaWorker

    if cfg is None:
        return OllamaWorker(model=model)

    for w in cfg.workers_local:
        if w.model == model:
            return OllamaWorker(model=model, base_url=cfg.leader.ollama_base_url)
    for w in cfg.workers_api:
        if w.model == model:
            return APIModelWorker(model=model, api_key=w.api_key)
    # 配置里找不到时，按 Ollama 兜底
    return OllamaWorker(model=model, base_url=cfg.leader.ollama_base_url)
