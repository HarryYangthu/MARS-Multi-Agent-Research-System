"""Event bus.

Two backends:

* ``InProcessEventBus`` — async pub/sub kept entirely in-memory. Default for
  Dev E2E (no external dependency).
* ``RedisEventBus`` — pub/sub through Redis. Used in docker-compose; if Redis
  is unavailable the bus auto-falls back to the in-process variant.

Both expose the same ``publish(channel, payload)`` /
``subscribe(channel) -> AsyncIterator`` API.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger


@dataclass(frozen=True)
class Event:
    channel: str
    payload: dict[str, Any]


class EventBus(Protocol):
    async def publish(self, channel: str, payload: dict[str, Any]) -> None: ...

    def subscribe(
        self, channel: str
    ) -> "AbstractAsyncContextManager[asyncio.Queue[Event]]": ...

    async def close(self) -> None: ...


# ----------------------------------------------------------------- in-process


class InProcessEventBus:
    """Pure-asyncio pub/sub. One queue per (channel, subscriber)."""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue[Event]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        evt = Event(channel=channel, payload=payload)
        async with self._lock:
            queues = list(self._subs.get(channel, ()))
            wildcard = list(self._subs.get("*", ()))
        for q in queues + wildcard:
            await q.put(evt)

    @asynccontextmanager
    async def subscribe(
        self, channel: str
    ) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        async with self._lock:
            self._subs.setdefault(channel, []).append(q)
        try:
            yield q
        finally:
            async with self._lock:
                if channel in self._subs and q in self._subs[channel]:
                    self._subs[channel].remove(q)

    async def close(self) -> None:
        async with self._lock:
            self._subs.clear()


# --------------------------------------------------------------------- redis


@dataclass
class _RedisState:
    client: Any  # redis.asyncio.Redis
    pubsubs: list[Any] = field(default_factory=list)


class RedisEventBus:
    """Redis pub/sub backed bus. If connection fails, callers should fall
    back to ``InProcessEventBus``."""

    def __init__(self, url: str) -> None:
        try:
            import redis.asyncio as redis
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("redis package missing") from exc
        self._redis_mod = redis
        self._url = url
        self._state: _RedisState | None = None

    async def _get(self) -> _RedisState:
        if self._state is None:
            client = self._redis_mod.from_url(self._url, decode_responses=True)
            await client.ping()
            self._state = _RedisState(client=client)
        return self._state

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        st = await self._get()
        await st.client.publish(channel, json.dumps(payload, ensure_ascii=False))

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue[Event]]:
        st = await self._get()
        pubsub = st.client.pubsub()
        await pubsub.subscribe(channel)
        st.pubsubs.append(pubsub)
        q: asyncio.Queue[Event] = asyncio.Queue()

        async def reader() -> None:
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = message.get("data", "{}")
                    payload = json.loads(data) if isinstance(data, str) else {}
                    await q.put(Event(channel=channel, payload=payload))
            except Exception as exc:
                logger.warning("redis subscribe loop ended: {}", exc)

        task = asyncio.create_task(reader())
        try:
            yield q
        finally:
            task.cancel()
            with suppress(BaseException):
                await task
            with suppress(BaseException):
                await pubsub.unsubscribe(channel)
                await pubsub.close()

    async def close(self) -> None:
        if self._state is None:
            return
        for ps in list(self._state.pubsubs):
            with suppress(BaseException):
                await ps.close()
        with suppress(BaseException):
            await self._state.client.close()
        self._state = None


# -------------------------------------------------------------- factory


def build_event_bus(redis_url: str | None) -> EventBus:
    """Try Redis, fall back to in-process."""
    bus: EventBus
    if not redis_url:
        bus = InProcessEventBus()
        return bus
    candidate = RedisEventBus(redis_url)
    try:
        # eager probe via a sync ping in the asyncio loop
        async def _probe() -> bool:
            try:
                await candidate._get()
                return True
            except Exception as exc:
                logger.warning("redis probe failed ({}); falling back to in-process bus", exc)
                return False

        ok = asyncio.run(_probe()) if not _is_running_loop() else False
        if ok:
            bus = candidate
            return bus
    except Exception as exc:
        logger.warning("redis bus init failed ({}); using in-process bus", exc)
    bus = InProcessEventBus()
    return bus


def _is_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
