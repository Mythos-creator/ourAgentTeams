"""Run asyncio coroutines from sync CLI code."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


def run_coro(coro: Awaitable[T]) -> T:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)  # type: ignore[arg-type]
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
