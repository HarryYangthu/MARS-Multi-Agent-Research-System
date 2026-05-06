"""Asyncio-backed task queue.

V0 keeps it simple: a bounded asyncio.Queue per RunGraph and an in-process
worker pool. Redis durability hooks are stubbed for V1.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class QueuedTask:
    run_id: str
    node_key: str
    coro: Callable[[], Awaitable[None]]


class QueueManager:
    def __init__(self, *, max_concurrency: int = 6) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, task: QueuedTask) -> asyncio.Task[None]:
        async def runner() -> None:
            async with self._sem:
                await task.coro()

        t = asyncio.create_task(runner(), name=f"{task.run_id}:{task.node_key}")
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def join(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def active(self) -> int:
        return sum(1 for t in self._tasks if not t.done())
