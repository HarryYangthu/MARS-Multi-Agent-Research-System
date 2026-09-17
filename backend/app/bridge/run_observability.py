"""Joined run observability view for replay and UI recovery."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeGuard

from app.harness.observability.events import normalize_event, redact
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

_LOOP_COUNT_EVENTS = {
    "model_requests": "model_request", "model_responses": "model_response",
    "tool_dispatches": "tool_dispatch", "observations": "observation",
    "sdk_attempts": "sdk_attempt_started", "reflections": "reflection",
}
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
_CORRELATION_KEYS = (
    "task_id", "parent_task_id", "invocation_id", "parent_invocation_id",
    "trace_id", "node_id", "span_id", "parent_span_id",
)


def build_run_observability(run: RunHandle, *, limit: int = 200) -> dict[str, Any]:
    state = RunStateStore(run).load()
    states = (
        {key: value.value for key, value in state.graph.all_states().items()}
        if state is not None
        else {}
    )
    invocations, loop_events, loop_streams = _loop_observability(run)
    timeline = _timeline(run, limit=limit, loop_events=loop_events)
    trace = _trace_summary(run, invocations=invocations)
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
        "event_streams": {**_stream_index(run), **loop_streams},
        "timeline": timeline,
        "trace": trace,
        "execution": execution,
        "audit": audit,
    }


def _timeline(
    run: RunHandle, *, limit: int, loop_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    out: list[dict[str, Any]] = list(loop_events or [])
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


def _trace_summary(
    run: RunHandle, *, invocations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if invocations is None:
        invocations, _events, _streams = _loop_observability(run)
    path = run.subdir("context") / "trace_manifest.v2.json"
    diagnostics: list[dict[str, Any]] = []
    raw = _read_trace_json(path, run, diagnostics)
    if path.exists() and not diagnostics and (
        raw.get("schema") != "trace_manifest.v2" or raw.get("run_id") != run.run_id
        or not isinstance(raw.get("trace_id"), str) or not raw.get("trace_id")
        or not isinstance(raw.get("root_span_id"), str) or not raw.get("root_span_id")
    ):
        diagnostics.append(_diagnostic(run, path, "invalid_trace_manifest", "manifest identity fields are invalid"))
    spans = raw.get("spans", []) if isinstance(raw, dict) else []
    if not isinstance(spans, list):
        diagnostics.append(_diagnostic(run, path, "invalid_spans", "spans must be an array"))
        spans = []
    span_rows = [item for item in spans if isinstance(item, dict)]
    if len(span_rows) != len(spans):
        diagnostics.append(_diagnostic(run, path, "invalid_span", "span records must be objects"))
    span_ids = [item.get("span_id") for item in span_rows]
    if (any(not isinstance(item, str) or not item for item in span_ids)
            or len({str(item) for item in span_ids}) != len(span_ids)):
        diagnostics.append(_diagnostic(run, path, "invalid_span_ids", "span IDs must be nonempty and unique"))
    statuses: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for span in span_rows:
        status = str(span.get("status", "unknown"))
        kind = str(span.get("kind", "unknown"))
        statuses[status] = statuses.get(status, 0) + 1
        kinds[kind] = kinds.get(kind, 0) + 1
    counts = {key: sum(item["counts"].get(key, 0) for item in invocations) for key in _LOOP_COUNT_EVENTS}
    known_usage = {key: sum(item["known_usage"].get(key, 0) for item in invocations) for key in _USAGE_KEYS}
    usage_complete = all(item["usage_complete"] is True for item in invocations) if invocations else None
    all_diagnostics = [*diagnostics, *(problem for item in invocations for problem in item["diagnostics"])]
    consistency = [item["integrity"]["consistent"] for item in invocations]
    consistent = (False if all_diagnostics or False in consistency else
                  None if not path.exists() and not consistency or None in consistency else True)
    return {
        "path": "context/trace_manifest.v2.json",
        "exists": path.exists(),
        "trace_id": str(raw.get("trace_id", "")) if isinstance(raw, dict) else "",
        "span_count": len(span_rows),
        "status_counts": statuses,
        "kind_counts": kinds,
        "latest_spans": span_rows[-8:],
        "invocation_count": len(invocations),
        "invocations": invocations,
        "counts": counts,
        "counts_complete": all(item["counts_complete"] for item in invocations) if invocations else None,
        "known_usage": known_usage,
        "usage": known_usage if usage_complete else None,
        "usage_complete": usage_complete,
        "integrity": {"consistent": consistent, "diagnostic_count": len(all_diagnostics)},
        "diagnostics": all_diagnostics,
    }


def _diagnostic(run: RunHandle, path: Path, code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "path": path.relative_to(run.root).as_posix(), "message": message, **details}


def _read_trace_json(path: Path, run: RunHandle, diagnostics: list[dict[str, Any]], *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            diagnostics.append(_diagnostic(run, path, "missing_facts", "invocation facts are unavailable"))
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        diagnostics.append(_diagnostic(run, path, "unreadable_json", type(exc).__name__))
        return {}
    if not isinstance(raw, dict):
        diagnostics.append(_diagnostic(run, path, "invalid_json_object", "expected a JSON object"))
        return {}
    return raw


def _read_trace_events(path: Path, run: RunHandle, diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        diagnostics.append(_diagnostic(run, path, "unreadable_events", type(exc).__name__))
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            diagnostics.append(_diagnostic(run, path, "invalid_jsonl", "invalid event JSON", line=line_number))
            continue
        if not isinstance(raw, dict):
            diagnostics.append(_diagnostic(run, path, "invalid_event", "event must be an object", line=line_number))
            continue
        if not isinstance(raw.get("kind"), str) or not raw.get("kind"):
            diagnostics.append(_diagnostic(run, path, "invalid_event_kind", "event kind must be a nonempty string", line=line_number))
        rows.append(raw)
    return rows


def _correlation(raw: Mapping[str, Any]) -> dict[str, Any]:
    nested = raw.get("correlation")
    source = nested if isinstance(nested, Mapping) else raw
    return {key: source[key] for key in _CORRELATION_KEYS
            if key in source and (source[key] is None or isinstance(source[key], str))}


def _nonnegative_integer(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _usage_summary(payloads: list[Any]) -> tuple[dict[str, int], bool]:
    known = {key: 0 for key in _USAGE_KEYS}
    complete = True
    for payload in payloads:
        for key in _USAGE_KEYS:
            value = payload.get(key) if isinstance(payload, Mapping) else None
            if _nonnegative_integer(value):
                known[key] += value
            else:
                complete = False
    return known, complete


def _loop_observability(run: RunHandle) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    streams: dict[str, Any] = {}
    for root in sorted((run.root / "agent_traces").glob("*/*")):
        if not root.is_dir() or not ((root / "facts.json").exists() or (root / "events.jsonl").exists()):
            continue
        summary, rows = _loop_invocation_summary(run, root)
        summaries.append(summary)
        rel_path = (root / "events.jsonl").relative_to(run.root).as_posix()
        for row in rows:
            event = normalize_event(
                {**row, "event": row.get("kind", "loop.event"), "correlation": _correlation(row)},
                run_id=run.run_id, project=run.project, default_channel="agent_loop", default_kind="loop.event",
            )
            event["source"] = {"component": "agent_loop", "agent": root.parent.name, "path": rel_path}
            # A file position is the persisted identity; no parent relationship
            # is guessed from a directory name or an event's chronological order.
            event["event_id"] = f"loop:{rel_path}:{row.get('event_seq', 'unknown')}"
            events.append(event)
        event_path = root / "events.jsonl"
        streams[rel_path] = {"path": rel_path, "exists": event_path.exists(),
                             "size_bytes": event_path.stat().st_size if event_path.exists() else 0,
                             "last_event_at": _event_time(rows[-1]) if rows else ""}
    return summaries, events, streams


def _loop_invocation_summary(run: RunHandle, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    facts_path, event_path = root / "facts.json", root / "events.jsonl"
    facts = _read_trace_json(facts_path, run, diagnostics, required=True)
    rows = _read_trace_events(event_path, run, diagnostics)
    trace_off = facts.get("trace_mode") == "off"
    if not trace_off and not event_path.exists():
        diagnostics.append(_diagnostic(run, event_path, "missing_events", "trace events are unavailable"))
    actual_counts = {key: sum(row.get("kind") == kind for row in rows) for key, kind in _LOOP_COUNT_EVENTS.items()}
    raw_counts = facts.get("counts")
    facts_counts = raw_counts if isinstance(raw_counts, dict) else {}
    invalid_counts = [key for key in _LOOP_COUNT_EVENTS if not _nonnegative_integer(facts_counts.get(key))]
    if invalid_counts:
        diagnostics.append(_diagnostic(run, facts_path, "invalid_counts", "missing or invalid counters", fields=invalid_counts))
    count_mismatches = {key: {"events": value, "facts": facts_counts.get(key)}
                        for key, value in actual_counts.items() if facts_counts.get(key) != value}
    sequence_ok = (all(_nonnegative_integer(row.get("event_seq")) for row in rows)
                   and [row.get("event_seq") for row in rows] == list(range(1, len(rows) + 1)))
    snapshot_stale = facts.get("event_seq") != len(rows)
    if not trace_off:
        if not sequence_ok:
            diagnostics.append(_diagnostic(run, event_path, "event_sequence_discontinuous", "event sequence is not continuous"))
        if count_mismatches:
            diagnostics.append(_diagnostic(run, facts_path, "count_mismatch", "facts differ from persisted events", counts=count_mismatches))
        if snapshot_stale:
            diagnostics.append(_diagnostic(run, facts_path, "snapshot_stale", "facts and events are at different positions",
                                           facts_event_seq=facts.get("event_seq"), event_count=len(rows)))
    counts = ({key: value for key, value in facts_counts.items() if key in _LOOP_COUNT_EVENTS and _nonnegative_integer(value)}
              if trace_off else actual_counts)
    usage_payloads = ([facts.get("usage")] if trace_off else
                      [row.get("usage") for row in rows if row.get("kind") == "model_response"])
    known_usage, usage_fields_complete = _usage_summary(usage_payloads)
    if not trace_off and usage_fields_complete:
        facts_usage, facts_usage_fields_complete = _usage_summary([facts.get("usage")])
        if not facts_usage_fields_complete or facts_usage != known_usage:
            diagnostics.append(_diagnostic(run, facts_path, "usage_mismatch", "facts usage differs from visible response usage"))
    usage_complete = bool(facts.get("usage_complete") is True and usage_fields_complete and not diagnostics
                          and counts.get("model_requests", 0) == counts.get("model_responses", 0)
                          and not any(row.get("kind") == "sdk_attempt_failed" for row in rows))
    correlation = _correlation(facts)
    for row in rows:
        for key, value in _correlation(row).items():
            if key in correlation and correlation[key] != value:
                diagnostics.append(_diagnostic(run, event_path, "correlation_conflict", "correlation changed within an invocation", field=key))
                usage_complete = False
            else:
                correlation[key] = value
    return {
        "path": root.relative_to(run.root).as_posix(), "agent": root.parent.name,
        "directory": root.name, "correlation": correlation,
        "status": facts.get("status", "unknown"), "trace_mode": facts.get("trace_mode", "unknown"),
        "fingerprint": facts.get("fingerprint"), "counts": counts, "facts_counts": facts_counts,
        "context_metadata": redact(facts.get("context_metadata", {})),
        "counts_source": "facts" if trace_off else "events", "counts_complete": not diagnostics,
        "known_usage": known_usage, "usage": known_usage if usage_complete else None,
        "usage_complete": usage_complete, "usage_source": "facts" if trace_off else "model_response_events",
        "integrity": {"trace_available": event_path.exists() and not trace_off,
                      "consistent": None if trace_off and not diagnostics else not diagnostics,
                      "event_count": len(rows), "event_sequence_continuous": None if trace_off else sequence_ok,
                      "summary_snapshot_stale": None if trace_off else snapshot_stale,
                      "count_mismatches": {} if trace_off else count_mismatches},
        "diagnostics": diagnostics,
    }, rows


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
    for key in ("timestamp", "time", "created", "created_at", "reviewed_at"):
        value = row.get(key)
        if isinstance(value, str):
            return value
    return ""
