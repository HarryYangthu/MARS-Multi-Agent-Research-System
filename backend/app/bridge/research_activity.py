"""Small public execution milestones for the research conversation."""
from typing import Any

from app.bridge.run_observability import build_run_observability
from app.storage.run_store import RunHandle

PUBLIC_LOOP_KINDS = frozenset({
    "model_request", "model_response", "model_error", "context_compressed",
    "tool_dispatch", "observation", "validation", "stopped", "interrupted",
    "resource_budget_exhausted",
})


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
    view = build_run_observability(run, limit=limit)
    return {"run_id": run.run_id, "project": run.project,
            "timeline": [item for row in view["timeline"] if (item := public_activity_event(row)) is not None]}
