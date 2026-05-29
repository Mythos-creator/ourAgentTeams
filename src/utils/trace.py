"""LLM 调用 trace：把每次 chat() 的输入/输出/耗时/成本写入 jsonl 便于审计与回放。

设计：
- 通过 TracingWorker 装饰 BaseModelWorker；与 RetryingWorker 同样是包装器。
- 默认落到 data/traces/YYYY-MM-DD.jsonl，可通过 `path` 注入。
- 写入用 atomic 追加（utf-8 + 单行 json + \n），失败不抛（trace 不应影响主流程）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.models.base import BaseModelWorker

DEFAULT_TRACE_DIR = Path(__file__).resolve().parents[2] / "data" / "traces"
_LOCK = threading.Lock()


def _today_path(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / (time.strftime("%Y-%m-%d") + ".jsonl")


def _safe_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"...[truncated {len(text)-max_chars} chars]"


def write_trace(
    record: dict[str, Any],
    *,
    path: Path | None = None,
    base_dir: Path = DEFAULT_TRACE_DIR,
) -> None:
    """单行 JSON 追加；失败静默。"""
    try:
        target = Path(path) if path else _today_path(base_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _LOCK:
            with open(target, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
    except Exception:
        # tracing must never break the call site
        pass


class TracingWorker(BaseModelWorker):
    """装饰器：包一层 BaseModelWorker，记录每次 chat 的 trace。"""

    def __init__(
        self,
        inner: BaseModelWorker,
        *,
        path: Path | None = None,
        base_dir: Path = DEFAULT_TRACE_DIR,
        max_msg_chars: int = 4000,
        max_resp_chars: int = 8000,
        run_id: str | None = None,
        agent_name: str | None = None,
    ):
        # don't call super().__init__ if BaseModelWorker has heavy init; just delegate
        self._inner = inner
        self._path = Path(path) if path else None
        self._base_dir = base_dir
        self._max_msg = max_msg_chars
        self._max_resp = max_resp_chars
        self._run_id = run_id
        self._agent_name = agent_name

    @property
    def model(self) -> str:  # type: ignore[override]
        return getattr(self._inner, "model", "<unknown>")

    async def ping(self) -> bool:  # type: ignore[override]
        return await self._inner.ping()

    async def list_models(self) -> list[str]:  # type: ignore[override]
        return await self._inner.list_models()

    async def chat(self, messages, *, temperature=None, max_tokens=None):  # type: ignore[override]
        call_id = uuid.uuid4().hex[:12]
        started = time.time()
        snap_in = {
            "msgs": [
                {"role": m.get("role"), "content": _safe_truncate(str(m.get("content", "")), self._max_msg)}
                for m in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        ok = True
        err: str | None = None
        resp: Any = None
        try:
            resp = await self._inner.chat(messages, temperature=temperature, max_tokens=max_tokens)
            return resp
        except Exception as e:
            ok = False
            err = f"{type(e).__name__}: {e}"
            raise
        finally:
            elapsed = round(time.time() - started, 4)
            content = ""
            tokens = None
            cost = None
            if ok and resp is not None:
                content = getattr(resp, "content", None)
                if content is None:
                    content = str(resp)
                tokens = getattr(resp, "total_tokens", None)
                cost = getattr(resp, "cost_usd", None)
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "call_id": call_id,
                "run_id": self._run_id,
                "agent": self._agent_name,
                "model": self.model,
                "ok": ok,
                "elapsed_s": elapsed,
                "input": snap_in,
                "output": _safe_truncate(content, self._max_resp) if ok else None,
                "tokens": tokens,
                "cost_usd": cost,
                "error": err,
            }
            write_trace(record, path=self._path, base_dir=self._base_dir)
