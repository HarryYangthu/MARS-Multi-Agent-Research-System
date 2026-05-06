"""Aggregate events across runs (for the global event-log sidebar)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import get_run_store


class EventEntry(BaseModel):
    run_id: str
    channel: str
    timestamp: str = ""
    payload: dict[str, Any]


router = APIRouter(prefix="/api/events", tags=["events"])


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
