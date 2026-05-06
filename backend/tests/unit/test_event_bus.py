from __future__ import annotations

import asyncio

import pytest

from app.harness.runtime.event_bus import InProcessEventBus


@pytest.mark.asyncio
async def test_pubsub_basic() -> None:
    bus = InProcessEventBus()
    received: list[dict] = []  # type: ignore[type-arg]

    async with bus.subscribe("ch") as q:
        await bus.publish("ch", {"hello": 1})
        evt = await asyncio.wait_for(q.get(), timeout=1.0)
        received.append(evt.payload)

    assert received == [{"hello": 1}]


@pytest.mark.asyncio
async def test_multi_subscribers_all_receive() -> None:
    bus = InProcessEventBus()

    async def consume() -> dict:  # type: ignore[type-arg]
        async with bus.subscribe("ch") as q:
            evt = await asyncio.wait_for(q.get(), timeout=1.0)
            return evt.payload

    consumers = [asyncio.create_task(consume()) for _ in range(3)]
    await asyncio.sleep(0.01)  # let subs register
    await bus.publish("ch", {"x": 42})
    results = await asyncio.gather(*consumers)
    assert all(r == {"x": 42} for r in results)


@pytest.mark.asyncio
async def test_unsubscribed_channel_no_delivery() -> None:
    bus = InProcessEventBus()
    async with bus.subscribe("a") as q:
        await bus.publish("b", {"x": 1})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.1)
