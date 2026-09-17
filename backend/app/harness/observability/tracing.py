"""File-backed trace manifest with optional OpenTelemetry compatibility.

V2 keeps JSONL events as the audit source and writes a compact trace manifest
for UI waterfall/debugging. If OpenTelemetry packages are installed later,
the trace id/span ids here can be correlated with real exported spans.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from types import TracebackType
from typing import Any, Concatenate, ParamSpec, Protocol, TypeVar

from app.harness.observability.langsmith_sink import get_langsmith_sink
from app.harness.persistence import atomic_write_json, path_lock

P = ParamSpec("P")
T = TypeVar("T")


def _transaction(method: Callable[Concatenate["TraceRecorder", P], T]) -> Callable[Concatenate["TraceRecorder", P], T]:
    @wraps(method)
    def locked(self: "TraceRecorder", /, *args: P.args, **kwargs: P.kwargs) -> T:
        with path_lock(self.lock_path):
            return method(self, *args, **kwargs)
    return locked


class RunLike(Protocol):
    run_id: str

    def subdir(self, name: str) -> Path: ...


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class TraceSpan:
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    started_at: str
    ended_at: str | None
    status: str
    attributes: dict[str, Any]


class TraceRecorder:
    def __init__(self, run: RunLike) -> None:
        self.run = run
        self.path = run.subdir("context") / "trace_manifest.v2.json"
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    @_transaction
    def ensure_manifest(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"corrupt trace manifest: {self.path}") from exc
            if (not isinstance(raw, dict) or raw.get("schema") != "trace_manifest.v2"
                    or raw.get("run_id") != self.run.run_id
                    or not isinstance(raw.get("trace_id"), str) or not raw["trace_id"]
                    or not isinstance(raw.get("root_span_id"), str) or not raw["root_span_id"]
                    or not isinstance(raw.get("spans"), list)
                    or not all(isinstance(span, dict) and isinstance(span.get("span_id"), str)
                               for span in raw["spans"])
                    or not isinstance(raw.get("event_index"), list)):
                raise ValueError(f"invalid trace manifest: {self.path}")
            if len({span["span_id"] for span in raw["spans"]}) != len(raw["spans"]):
                raise ValueError(f"duplicate span IDs in trace manifest: {self.path}")
            return raw
        manifest: dict[str, Any] = {
            "schema": "trace_manifest.v2",
            "run_id": self.run.run_id,
            "trace_id": uuid.uuid4().hex,
            "root_span_id": uuid.uuid4().hex[:16],
            "created_at": _now(),
            "updated_at": _now(),
            "spans": [],
            "event_index": [],
        }
        self._write(manifest)
        return manifest

    def start_span(
        self,
        *,
        name: str,
        kind: str,
        attributes: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
    ) -> "SpanContext":
        manifest = self.ensure_manifest()
        parent = parent_span_id or str(manifest.get("root_span_id"))
        attrs = dict(attributes or {})
        sink = get_langsmith_sink()
        if sink.enabled:
            attrs.setdefault("langsmith_run_id", sink.new_run_id())
        parent_remote_run_id = self._parent_remote_run_id(manifest, parent)
        span = TraceSpan(
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent,
            name=name,
            kind=kind,
            started_at=_now(),
            ended_at=None,
            status="running",
            attributes=attrs,
        )
        self._append_span(span)
        if sink.enabled:
            sink.span_started(
                run_id=self.run.run_id,
                trace_id=str(manifest.get("trace_id", "")),
                span=span,
                parent_remote_run_id=parent_remote_run_id,
            )
        return SpanContext(self, span.span_id)

    @_transaction
    def finish_span(
        self,
        span_id: str,
        *,
        status: str = "ok",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        manifest = self.ensure_manifest()
        spans_raw = manifest.get("spans", [])
        if not isinstance(spans_raw, list):
            spans_raw = []
        finished_span: dict[str, Any] | None = None
        for span in spans_raw:
            if not isinstance(span, dict) or span.get("span_id") != span_id:
                continue
            span["ended_at"] = _now()
            span["status"] = status
            if attributes:
                attrs = span.get("attributes", {})
                if not isinstance(attrs, dict):
                    attrs = {}
                attrs.update(attributes)
                span["attributes"] = attrs
            finished_span = span
            break
        manifest["spans"] = spans_raw
        manifest["updated_at"] = _now()
        self._write(manifest)
        sink = get_langsmith_sink()
        if finished_span is not None and sink.enabled:
            sink.span_finished(
                run_id=self.run.run_id,
                trace_id=str(manifest.get("trace_id", "")),
                span=finished_span,
            )

    @_transaction
    def record_event_ref(
        self,
        *,
        channel: str,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        manifest = self.ensure_manifest()
        event_index = manifest.get("event_index", [])
        if not isinstance(event_index, list):
            event_index = []
        event_index.append(
            {
                "channel": channel,
                "event": event,
                "timestamp": _now(),
                "attrs": {
                    key: value
                    for key, value in payload.items()
                    if key in {"agent", "node", "run_id", "version", "from_state", "to_state",
                               "task_id", "parent_task_id", "invocation_id", "parent_invocation_id",
                               "trace_id", "node_id", "span_id", "parent_span_id", "correlation"}
                },
            }
        )
        manifest["event_index"] = event_index[-500:]
        manifest["updated_at"] = _now()
        self._write(manifest)

    def get_span(self, span_id: str) -> dict[str, Any] | None:
        manifest = self.ensure_manifest()
        spans_raw = manifest.get("spans", [])
        if not isinstance(spans_raw, list):
            return None
        for span in spans_raw:
            if isinstance(span, dict) and span.get("span_id") == span_id:
                return span
        return None

    @_transaction
    def _append_span(self, span: TraceSpan) -> None:
        manifest = self.ensure_manifest()
        spans_raw = manifest.get("spans", [])
        spans = spans_raw if isinstance(spans_raw, list) else []
        spans.append(
            {
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "name": span.name,
                "kind": span.kind,
                "started_at": span.started_at,
                "ended_at": span.ended_at,
                "status": span.status,
                "attributes": span.attributes,
            }
        )
        manifest["spans"] = spans
        manifest["updated_at"] = _now()
        self._write(manifest)

    def _parent_remote_run_id(
        self,
        manifest: dict[str, Any],
        parent_span_id: str | None,
    ) -> str | None:
        if parent_span_id is None:
            return None
        spans_raw = manifest.get("spans", [])
        if not isinstance(spans_raw, list):
            return None
        for span in spans_raw:
            if not isinstance(span, dict) or span.get("span_id") != parent_span_id:
                continue
            attrs = span.get("attributes", {})
            if not isinstance(attrs, dict):
                return None
            remote_run_id = attrs.get("langsmith_run_id")
            return remote_run_id if isinstance(remote_run_id, str) else None
        return None

    def _write(self, manifest: dict[str, Any]) -> None:
        atomic_write_json(self.path, manifest)


class SpanContext(AbstractContextManager["SpanContext"]):
    def __init__(self, recorder: TraceRecorder, span_id: str) -> None:
        self.recorder = recorder
        self.span_id = span_id
        self.status = "ok"

    def __enter__(self) -> "SpanContext":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if exc_value is not None:
            self.recorder.finish_span(
                self.span_id,
                status="error",
                attributes={"error": str(exc_value)},
            )
            return None
        self.recorder.finish_span(self.span_id, status=self.status)
        return None
