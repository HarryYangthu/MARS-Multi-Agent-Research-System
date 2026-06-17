"""Joined run observability view for replay and UI recovery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.harness.observability.events import normalize_event
from app.storage.run_store import RunHandle
from app.storage.run_state_store import RunStateStore


EVENT_STREAMS: tuple[tuple[str, str, str], ...] = (
    ("events/agent_events.jsonl", "agent_events", "agent.state_changed"),
    ("events/websocket_events.jsonl", "websocket_events", "run.event"),
    ("events/tool_events.jsonl", "tool_events", "tool.event"),
    ("events/tool_calls.jsonl", "tool_calls", "tool.call"),
    ("events/commander_tool_events.jsonl", "commander_tool_events", "commander.tool"),
    ("events/hitl_events.jsonl", "hitl_events", "hitl.event"),
    ("events/gate_events.jsonl", "gate_events", "gate.event"),
    ("events/execution_events.jsonl", "execution_events", "execution.event"),
    ("memory/memory_candidate_reviews.jsonl", "memory_candidate_reviews", "memory.review"),
    (
        "memory/self_evolution_mutation_reviews.jsonl",
        "self_evolution_mutation_reviews",
        "self_evolution.review",
    ),
)


def build_run_observability(run: RunHandle, *, limit: int = 200) -> dict[str, Any]:
    state = RunStateStore(run).load()
    states = (
        {key: value.value for key, value in state.graph.all_states().items()}
        if state is not None
        else {}
    )
    timeline = _timeline(run, limit=limit)
    trace = _trace_summary(run)
    execution = _execution_summary(run)
    audit = _audit_summary(run)
    latest_event_at = timeline[0]["timestamp"] if timeline else ""
    return {
        "schema": "run_observability.v1",
        "run_id": run.run_id,
        "project": run.project,
        "task": run.task,
        "entrypoint": run.entrypoint,
        "status": state.status if state is not None else "unknown",
        "states": states,
        "health": _health(status=state.status if state is not None else "unknown", states=states),
        "latest_event_at": latest_event_at,
        "event_streams": _stream_index(run),
        "timeline": timeline,
        "trace": trace,
        "execution": execution,
        "audit": audit,
    }


def _timeline(run: RunHandle, *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel_path, channel, kind in EVENT_STREAMS:
        path = run.root / rel_path
        rows = _read_jsonl_tail(path, limit)
        for row in rows:
            out.append(
                normalize_event(
                    row,
                    run_id=run.run_id,
                    project=run.project,
                    default_channel=channel,
                    default_kind=kind,
                )
            )
    out.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return out[:limit]


def _stream_index(run: RunHandle) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rel_path, stream, _kind in EVENT_STREAMS:
        path = run.root / rel_path
        rows = _read_jsonl_tail(path, 1)
        out[stream] = {
            "path": rel_path,
            "exists": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "last_event_at": _event_time(rows[-1]) if rows else "",
        }
    return out


def _trace_summary(run: RunHandle) -> dict[str, Any]:
    path = run.subdir("context") / "trace_manifest.v1.json"
    raw = _read_json(path)
    spans = raw.get("spans", []) if isinstance(raw, dict) else []
    span_rows = [item for item in spans if isinstance(item, dict)]
    statuses: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for span in span_rows:
        status = str(span.get("status", "unknown"))
        kind = str(span.get("kind", "unknown"))
        statuses[status] = statuses.get(status, 0) + 1
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "path": "context/trace_manifest.v1.json",
        "exists": path.exists(),
        "trace_id": str(raw.get("trace_id", "")) if isinstance(raw, dict) else "",
        "span_count": len(span_rows),
        "status_counts": statuses,
        "kind_counts": kinds,
        "latest_spans": span_rows[-8:],
    }


def _execution_summary(run: RunHandle) -> dict[str, Any]:
    metrics_path = run.subdir("execution") / "metrics.json"
    summary_path = run.subdir("execution") / "batch_summary.json"
    metrics_raw = _read_json(metrics_path)
    rows = metrics_raw if isinstance(metrics_raw, list) else []
    summary_raw = _read_json(summary_path)
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    failures = summary.get("failures", [])
    curves_dir = run.subdir("execution") / "curves"
    plots_dir = run.subdir("execution") / "live_plots"
    return {
        "metrics_path": "execution/metrics.json",
        "metrics_exists": metrics_path.exists(),
        "metric_rows": len([item for item in rows if isinstance(item, dict)]),
        "batch_summary": summary,
        "failure_count": len(failures) if isinstance(failures, list) else 0,
        "curve_count": len(list(curves_dir.glob("*.json"))) if curves_dir.exists() else 0,
        "plot_count": len(list(plots_dir.glob("*.png"))) if plots_dir.exists() else 0,
    }


def _audit_summary(run: RunHandle) -> dict[str, Any]:
    hitl = _read_jsonl_tail(run.subdir("hitl") / "review_log.jsonl", 200)
    candidates = _read_jsonl_tail(run.subdir("memory") / "memory_candidates.jsonl", 200)
    reviews = _read_jsonl_tail(run.subdir("memory") / "memory_candidate_reviews.jsonl", 200)
    mutations = _read_jsonl_tail(
        run.subdir("memory") / "self_evolution_mutations.jsonl",
        200,
    )
    mutation_reviews = _read_jsonl_tail(
        run.subdir("memory") / "self_evolution_mutation_reviews.jsonl",
        200,
    )
    diagnoses = sorted(run.subdir("diagnosis").glob("diagnosis.v*.md"))
    packets = sorted(run.subdir("diagnosis").glob("feedback_packet.attempt_*.md"))
    return {
        "hitl_decisions": len(hitl),
        "memory_candidates": len(candidates),
        "memory_reviews": len(reviews),
        "self_evolution_mutations": len(mutations),
        "self_evolution_mutation_reviews": len(mutation_reviews),
        "pending_self_evolution_mutations": len(
            [item for item in mutations if item.get("status") == "pending_review"]
        ),
        "diagnosis_count": len(diagnoses),
        "feedback_packet_count": len(packets),
        "latest_diagnosis": diagnoses[-1].relative_to(run.root).as_posix() if diagnoses else "",
        "latest_feedback_packet": packets[-1].relative_to(run.root).as_posix() if packets else "",
    }


def _health(*, status: str, states: dict[str, str]) -> dict[str, Any]:
    waiting = [key for key, value in states.items() if value == "waiting_review"]
    failed = [key for key, value in states.items() if value == "failed"]
    running = [key for key, value in states.items() if value == "running"]
    if failed:
        reason = "node_failed"
        severity = "error"
    elif status == "waiting_feedback":
        reason = "waiting_feedback"
        severity = "warning"
    elif waiting:
        reason = "waiting_review"
        severity = "warning"
    elif running:
        reason = "running"
        severity = "info"
    elif status == "completed":
        reason = "completed"
        severity = "info"
    else:
        reason = status or "unknown"
        severity = "info"
    return {
        "status": status,
        "reason": reason,
        "severity": severity,
        "waiting_review": waiting,
        "failed": failed,
        "running": running,
    }


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            out.append(raw)
    return out


def _event_time(row: dict[str, Any]) -> str:
    for key in ("timestamp", "created", "created_at", "reviewed_at"):
        value = row.get(key)
        if isinstance(value, str):
            return value
    return ""
