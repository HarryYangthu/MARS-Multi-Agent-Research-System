"""Run-scoped observability event envelopes.

The file sink remains the durable source of truth. New producers can write
``event.v1`` envelopes, while readers can normalize legacy JSONL entries into
the same shape for UI replay.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol


Severity = Literal["debug", "info", "warning", "error", "critical"]

_DENY_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}


class RunLike(Protocol):
    run_id: str
    project: str

    def subdir(self, name: str) -> Path: ...


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def make_event(
    *,
    run_id: str,
    project: str,
    channel: str,
    kind: str,
    source: Mapping[str, Any],
    payload: Mapping[str, Any],
    severity: Severity = "info",
    evidence: Sequence[str] = (),
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "event.v1",
        "event_id": f"evt_{uuid.uuid4().hex}",
        "timestamp": timestamp or now_iso(),
        "run_id": run_id,
        "project": project,
        "channel": channel,
        "kind": kind,
        "severity": severity,
        "source": dict(source),
        "correlation": {},
        "evidence": [str(item) for item in evidence],
        "payload": redact(payload),
    }


def write_event(
    *,
    run: RunLike,
    stream: str,
    channel: str,
    kind: str,
    source: Mapping[str, Any],
    payload: Mapping[str, Any],
    severity: Severity = "info",
    evidence: Sequence[str] = (),
) -> dict[str, Any]:
    event = make_event(
        run_id=run.run_id,
        project=run.project,
        channel=channel,
        kind=kind,
        source=source,
        payload=payload,
        severity=severity,
        evidence=evidence,
    )
    path = run.subdir("events") / f"{stream}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    return event


def normalize_event(
    raw: Mapping[str, Any],
    *,
    run_id: str,
    project: str,
    default_channel: str,
    default_kind: str,
) -> dict[str, Any]:
    if raw.get("schema") == "event.v1":
        event = dict(raw)
        event["payload"] = redact(_mapping(event.get("payload")))
        return event
    kind = str(raw.get("event") or default_kind)
    channel = str(raw.get("channel") or default_channel)
    return make_event(
        run_id=run_id,
        project=project,
        channel=channel,
        kind=kind,
        source={"component": "legacy.jsonl"},
        payload=dict(raw),
        severity=_severity_from_kind(kind),
        evidence=[],
        timestamp=_timestamp_from_raw(raw),
    )


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _DENY_KEYS:
                out[key_text] = "[redacted]"
            else:
                out[key_text] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _timestamp_from_raw(raw: Mapping[str, Any]) -> str:
    for key in ("timestamp", "created", "created_at", "reviewed_at"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return now_iso()


def _severity_from_kind(kind: str) -> Severity:
    lower = kind.lower()
    if "critical" in lower or "blocked" in lower:
        return "critical"
    if "failed" in lower or "rejected" in lower or "error" in lower:
        return "error"
    if "review_required" in lower or "gate" in lower or "diagnosis" in lower:
        return "warning"
    return "info"
