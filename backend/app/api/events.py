"""Aggregate events across runs (for the global event-log sidebar)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.dependencies import get_run_store
from app.harness.observability.events import normalize_event


class EventEntry(BaseModel):
    run_id: str
    channel: str
    timestamp: str = ""
    payload: dict[str, Any]


router = APIRouter(prefix="/api/events", tags=["events"])

RUN_EVENT_STREAMS: dict[str, tuple[str, str]] = {
    "agent": ("agent_events.jsonl", "agent.state_changed"),
    "websocket": ("websocket_events.jsonl", "run.event"),
    "tool": ("tool_events.jsonl", "tool.event"),
    "tool_calls": ("tool_calls.jsonl", "tool.call"),
    "commander_tool": ("commander_tool_events.jsonl", "commander.tool"),
    "hitl": ("hitl_events.jsonl", "hitl.event"),
    "gate": ("gate_events.jsonl", "gate.event"),
    "execution": ("execution_events.jsonl", "execution.event"),
}


def _read_jsonl_tail(path: Any, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@router.get("", response_model=list[EventEntry])
async def list_events(limit: int = 80) -> list[EventEntry]:
    """Newest-first list of events across every run on disk."""
    store = get_run_store()
    runs = store.list()
    aggregated: list[EventEntry] = []
    per_run = max(5, limit // max(1, len(runs)))
    for run in runs:
        for sub in ("agent_events.jsonl", "websocket_events.jsonl"):
            entries = _read_jsonl_tail(run.subdir("events") / sub, per_run)
            for e in entries:
                aggregated.append(
                    EventEntry(
                        run_id=run.run_id,
                        channel=str(e.get("channel", sub.removesuffix(".jsonl"))),
                        timestamp=str(e.get("timestamp", "")),
                        payload={k: v for k, v in e.items() if k != "channel"},
                    )
                )
    # Newest first by timestamp string (ISO 8601 sorts lexically)
    aggregated.sort(key=lambda e: e.timestamp, reverse=True)
    return aggregated[:limit]


@router.get("/{run_id}")
async def list_run_events(
    run_id: str,
    stream: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    streams = (
        {stream: RUN_EVENT_STREAMS[stream]}
        if stream in RUN_EVENT_STREAMS
        else RUN_EVENT_STREAMS
    )
    out: list[dict[str, Any]] = []
    for stream_name, (filename, kind) in streams.items():
        rows = _read_jsonl_tail(run.subdir("events") / filename, limit)
        for row in rows:
            out.append(
                normalize_event(
                    row,
                    run_id=run.run_id,
                    project=run.project,
                    default_channel=stream_name,
                    default_kind=kind,
                )
            )
    out.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return out[: max(1, min(limit, 500))]
