"""指数退避重试包装：把任意 BaseModelWorker 包成"会自动重试"的版本。

对 API worker 特别有用，可缓解偶发 429/503/timeout，整任务不会因为一次抖动失败。
对本地 Ollama 也安全（默认不会重试 ValueError 这类业务错误）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.models.base import BaseModelWorker, ModelResponse

logger = logging.getLogger(__name__)


# 默认认为"值得重试"的异常类型名（按字符串匹配以避免引入外部依赖）
_RETRYABLE_NAME_PATTERNS = (
    "Timeout", "RateLimit", "ServiceUnavailable", "InternalServerError",
    "APIConnectionError", "APIError", "ConnectError", "ReadTimeout",
)


def _is_retryable(exc: BaseException) -> bool:
    cls = type(exc).__name__
    if any(pat in cls for pat in _RETRYABLE_NAME_PATTERNS):
        return True
    # asyncio TimeoutError
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
        return True
    return False


class RetryingWorker(BaseModelWorker):
    """对底层 worker 的 chat 调用做指数退避重试。

    `ping` 和 `list_models` 不重试（它们本身就是健康检查）。
    """

    def __init__(
        self,
        inner: BaseModelWorker,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        retryable: callable | None = None,
    ) -> None:
        super().__init__(inner.model)
        self.inner = inner
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self._retryable = retryable or _is_retryable

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self.inner.chat(
                    messages=messages, temperature=temperature, max_tokens=max_tokens,
                )
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self.max_attempts or not self._retryable(exc):
                    raise
                delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
                logger.warning(
                    "RetryingWorker(%s) attempt %d/%d failed: %s; sleeping %.2fs",
                    self.model, attempt, self.max_attempts, exc, delay,
                )
                await asyncio.sleep(delay)
        # unreachable, but for type checkers
        assert last_exc is not None
        raise last_exc

    async def ping(self) -> bool:
        return await self.inner.ping()

    async def list_models(self) -> list[str]:
        return await self.inner.list_models()
