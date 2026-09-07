"""Durable visible-I/O events, minimal host facts, and atomic checkpoints."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(value)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class LoopTrace:
    def __init__(self, root: Path, mode: str, *, resume: bool = False) -> None:
        self.root = root
        self.mode = mode
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = root / "events.jsonl"
        self.seq = 0
        if self.events.exists():
            if not resume:
                raise ValueError("trace already exists; use explicit resume")
            rows = [json.loads(line) for line in self.events.read_text().splitlines()]
            if [r["event_seq"] for r in rows] != list(range(1, len(rows) + 1)):
                raise ValueError("cannot resume a discontinuous event stream")
            self.seq = len(rows)

    def emit(self, kind: str, payload: dict[str, Any], *, visible: Any = None) -> None:
        self.seq += 1
        if self.mode == "off":
            return
        row: dict[str, Any] = {
            "event_seq": self.seq,
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        if visible is not None:
            row["visible_sha256"] = digest(visible)
            if self.mode == "full":
                row["visible"] = visible
        with self.events.open("a", encoding="utf-8") as handle:
            handle.write(canonical(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def snapshot(self, state: dict[str, Any]) -> None:
        facts = {k: state[k] for k in ("status", "counts", "usage", "usage_complete", "fingerprint", "pending")}
        facts.update(event_seq=self.seq, trace_mode=self.mode, resume_available=self.mode == "full")
        atomic_json(self.root / "facts.json", facts)
        if self.mode == "full":
            atomic_json(self.root / "checkpoint.json", state)

    def record_attempt(self, state: dict[str, Any], kind: str, data: dict[str, Any]) -> None:
        if kind == "sdk_attempt_started":
            state["counts"]["sdk_attempts"] += 1
        elif kind == "sdk_attempt_failed":
            # A later successful retry does not reveal the failed attempt's usage.
            state["usage_complete"] = False
        self.emit(kind, {"request": state["counts"]["model_requests"], **data})
        self.snapshot(state)


def audit_trace(root: Path) -> dict[str, Any]:
    facts = json.loads((root / "facts.json").read_text())
    if facts["trace_mode"] == "off":
        return {"trace_available": False, "consistent": None, "facts": facts}
    rows = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    seq_ok = [r["event_seq"] for r in rows] == list(range(1, len(rows) + 1))
    count_map = {"model_requests": "model_request", "model_responses": "model_response",
                 "tool_dispatches": "tool_dispatch", "observations": "observation",
                 "sdk_attempts": "sdk_attempt_started", "reflections": "reflection"}
    actual = {key: sum(r["kind"] == event for r in rows) for key, event in count_map.items()}
    mismatches = {key: {"events": value, "facts": facts["counts"].get(key, 0)}
                  for key, value in actual.items() if value != facts["counts"].get(key, 0)}
    stale = facts["event_seq"] != len(rows)
    last_attempt = next((r for r in reversed(rows)
                         if r["kind"] in {"sdk_attempt_failed", "sdk_attempt_succeeded"}), None)
    provider_error = ({"error": last_attempt.get("error"), **last_attempt.get("details", {})}
                      if last_attempt and last_attempt["kind"] == "sdk_attempt_failed" else None)
    return {"trace_available": True, "consistent": seq_ok and not mismatches and not stale,
            "event_sequence_continuous": seq_ok, "summary_snapshot_stale": stale,
            "count_mismatches": mismatches, "event_count": len(rows), "facts": facts,
            "provider_error": provider_error}
