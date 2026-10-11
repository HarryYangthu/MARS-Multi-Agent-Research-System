"""Small public execution milestones for the research conversation."""
from collections import deque
from functools import lru_cache
import json
from pathlib import Path
from threading import Lock
from typing import Any

from app.bridge.run_observability import _event_time
from app.storage.run_store import RunHandle

PUBLIC_LOOP_KINDS = frozenset({
    "model_request", "model_response", "model_error", "context_compressed",
    "tool_dispatch", "observation", "validation", "stopped", "interrupted",
    "resource_budget_exhausted",
})

_activity_read_lock = Lock()


@lru_cache(maxsize=128)
def _public_trace_rows(path: Path, size: int, modified_ns: int, changed_ns: int,
                       limit: int) -> tuple[dict[str, Any] | None, ...]:
    # Only the public projection is cached. Never retain model contexts for a
    # polling endpoint. Stat fields invalidate both appended and replaced files.
    rows: deque[dict[str, Any] | None] = deque(maxlen=limit)
    with path.open("rb") as stream:
        for line in stream:
            item = None
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                row = None
            if isinstance(row, dict):
                item = public_activity_event({
                    "event_id": str(row.get("event_seq", "unknown")),
                    "timestamp": _event_time(row), "kind": row.get("kind"),
                    "source": {"component": "agent_loop", "agent": path.parent.parent.name},
                    "payload": row.get("payload", {}) if row.get("schema") == "event.v1" else row,
                })
            # Keep placeholders: limit is the original raw-line window, not a
            # different window of valid/public events after filtering.
            rows.append(item)
    return tuple(rows)


def public_activity_event(event: dict[str, Any]) -> dict[str, Any] | None:
    source, payload = event.get("source"), event.get("payload")
    if not isinstance(source, dict) or source.get("component") != "agent_loop":
        return None
    if event.get("kind") not in PUBLIC_LOOP_KINDS or not isinstance(payload, dict):
        return None
    return {
        "event_id": event.get("event_id", ""), "timestamp": event.get("timestamp", ""),
        "kind": event["kind"], "source": {"component": "agent_loop", "agent": source.get("agent", "")},
        "payload": {key: payload[key] for key in ("model", "tool", "valid", "ok", "status")
                    if key in payload and isinstance(payload[key], (str, bool))},
    }


def build_research_activity(run: RunHandle, *, limit: int) -> dict[str, Any]:
    # Live milestones need only small public event fields. Full trace integrity,
    # usage and model context remain in the separate observability endpoint.
    timeline: list[dict[str, Any]] = []
    if limit > 0:
        for path in sorted((run.root / "agent_traces").glob("*/*/events.jsonl")):
            try:
                if not path.resolve().is_relative_to(run.root.resolve()):
                    continue
                # Serialize cache misses so simultaneous page polls cannot
                # repeatedly allocate the same large historical JSON record.
                with _activity_read_lock:
                    stat = path.stat()
                    rows = _public_trace_rows(path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, limit)
                for item in rows:
                    if item is not None:
                        timeline.append({**item,
                            "event_id": f"loop:{path.relative_to(run.root).as_posix()}:{item['event_id']}",
                            "source": dict(item["source"]), "payload": dict(item["payload"])})
            except OSError:
                continue
        timeline.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    return {"run_id": run.run_id, "project": run.project, "timeline": timeline[:limit] if limit > 0 else []}
