"""Process-local task ownership, not a recovery or replay engine."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger


class OwnedRunTasks:
    """One driver per run. Factories avoid creating unused coroutine objects."""

    def __init__(self) -> None:
        self._active: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self.closing = False

    def active(self, run_id: str) -> asyncio.Task[None] | None:
        task = self._active.get(run_id)
        return task if task is not None and not task.done() else None

    def run_ids(self) -> tuple[str, ...]:
        return tuple(key for key in self._active if self.active(key) is not None)

    def stopping(self, run_id: str) -> bool:
        return run_id in self._cancelled

    def spawn(self, run_id: str, operation: str, factory: Callable[[], Awaitable[None]],
              *, finished: Callable[[], None]) -> bool:
        if self.closing or self.stopping(run_id) or self.active(run_id) is not None:
            return False

        async def execute() -> None:
            await factory()

        task = asyncio.create_task(execute(), name=f"owned_run:{run_id}:{operation}")
        self._active[run_id] = task

        def done(completed: asyncio.Task[None]) -> None:
            if self._active.get(run_id) is completed:
                self._active.pop(run_id, None)
            if not completed.cancelled():
                error = completed.exception()
                if error is not None:
                    logger.error("Owned run task failed: run={} type={}", run_id, type(error).__name__)
            # Also executes when cancellation happened before execute() started.
            finished()

        task.add_done_callback(done)
        return True

    def cancel_once(self, run_id: str) -> asyncio.Task[None] | None:
        task = self.active(run_id)
        if task is not None and run_id not in self._cancelled:
            self._cancelled.add(run_id)
            task.cancel("owned run stop requested")
        return task

    def release_after_explicit_review(self, run_id: str) -> None:
        """Bridge calls this only after validating a completed review boundary."""
        if self.closing or self.active(run_id) is not None:
            raise ValueError("cannot release a stop while work is active or service is closing")
        self._cancelled.discard(run_id)

    async def wait(self, task: asyncio.Task[None], *, timeout: float) -> bool:
        # Unlike wait_for/gather cancellation, timing out or disconnecting this
        # waiter must not send a second cancel into the target's cleanup.
        _, pending = await asyncio.wait({task}, timeout=timeout)
        return not pending
