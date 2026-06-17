"""WebSocket endpoints — one per run, optional per-experiment subchannel."""
from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.api.dependencies import get_event_bus

router = APIRouter()


@router.websocket("/ws/runs/{run_id}")
async def run_socket(ws: WebSocket, run_id: str) -> None:
    bus = get_event_bus()
    await ws.accept()
    channels = [
        f"run.{run_id}.agent_state",
        f"run.{run_id}.failure",
        f"run.{run_id}.hitl",
        f"run.{run_id}.feedback_loop",
        f"run.{run_id}.evaluation",
        "run.lifecycle",
    ]
    queues: list[asyncio.Queue] = []  # type: ignore[type-arg]

    async with contextlib.AsyncExitStack() as stack:
        for ch in channels:
            q = await stack.enter_async_context(bus.subscribe(ch))
            queues.append(q)

        async def pump(q: asyncio.Queue, channel: str) -> None:  # type: ignore[type-arg]
            while True:
                evt = await q.get()
                await ws.send_text(
                    json.dumps({"channel": channel, "payload": evt.payload}, ensure_ascii=False)
                )

        tasks = [
            asyncio.create_task(pump(q, ch))
            for q, ch in zip(queues, channels, strict=True)
        ]
        try:
            while True:
                # Keep the connection open; we ignore inbound messages for V0
                # other than logging.
                msg = await ws.receive_text()
                logger.debug("ws inbound on {}: {}", run_id, msg)
        except WebSocketDisconnect:
            logger.debug("ws disconnected for run {}", run_id)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t


@router.websocket("/ws/runs/{run_id}/experiment/{exp_id}")
async def experiment_socket(ws: WebSocket, run_id: str, exp_id: str) -> None:
    bus = get_event_bus()
    await ws.accept()
    channel = f"run.{run_id}.experiment.{exp_id}"
    async with bus.subscribe(channel) as q:
        try:
            while True:
                evt = await q.get()
                await ws.send_text(
                    json.dumps({"channel": channel, "payload": evt.payload}, ensure_ascii=False)
                )
        except WebSocketDisconnect:
            return
