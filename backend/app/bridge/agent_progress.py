"""Adapt concise agent updates to durable run events and existing live channels."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.bridge.node_key import parse_node_key
from app.harness.runtime.event_bus import EventBus
from app.storage.run_store import RunHandle


def agent_progress_payload(
    *, run_id: str, project: str, node_key: str, payload: Mapping[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    """Keep only the public progress contract and stamp the host's run identity."""
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in {
        "started", "action", "observation", "candidate", "validation", "review", "finished",
    }:
        raise ValueError("unknown agent progress kind")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("agent progress message must be nonempty text")
    phase = payload.get("phase", kind)
    if not isinstance(phase, str):
        raise ValueError("agent progress phase must be text")
    invocation = payload.get("invocation", "")
    if not isinstance(invocation, str):
        raise ValueError("agent progress invocation must be text")
    return {
        "event": "agent.progress", "kind": kind, "message": message,
        "summary": message, "phase": phase, "run_id": run_id, "project": project,
        "agent": parse_node_key(node_key).stage, "node": node_key,
        "invocation": invocation, "timestamp": timestamp,
    }


def build_agent_progress_sink(
    *, run: RunHandle, node_key: str, bus: EventBus | None = None,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Persist before best-effort publication; progress never changes node state."""
    sequence = 0

    async def emit(payload: dict[str, Any]) -> None:
        nonlocal sequence
        event = agent_progress_payload(
            run_id=run.run_id, project=run.project, node_key=node_key, payload=payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        sequence += 1
        event["progress_seq"] = sequence
        # Reuse a channel already subscribed by /ws/runs/{id}. Omitting to_state
        # keeps progress separate from the orchestrator's state transitions.
        channel = f"run.{run.run_id}.agent_state"
        run.write_event("agent_events", {"channel": channel, **event})
        if bus is not None:
            try:
                await bus.publish(channel, event)
            except Exception as exc:  # pragma: no cover - authoritative event remains on disk
                logger.warning(
                    "agent progress publication failed: run={} node={} kind={} error={}",
                    run.run_id, node_key, event["kind"], exc,
                )

    return emit
