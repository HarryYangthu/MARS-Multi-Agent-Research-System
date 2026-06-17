from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.observability import tracing
from app.harness.observability.langsmith_sink import (
    LangSmithSink,
    LangSmithSinkConfig,
    get_langsmith_sink,
    reset_langsmith_sink_for_tests,
)
from app.harness.observability.tracing import TraceRecorder
from app.storage.run_store import RunStore


class DisabledSink:
    enabled = False

    def new_run_id(self) -> str:
        raise AssertionError("disabled sink should not allocate remote ids")

    def span_started(self, **_: Any) -> None:
        raise AssertionError("disabled sink should not receive span_started")

    def span_finished(self, **_: Any) -> None:
        raise AssertionError("disabled sink should not receive span_finished")


class RecordingSink:
    enabled = True

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    def new_run_id(self) -> str:
        return "00000000-0000-0000-0000-000000000123"

    def span_started(self, **kwargs: Any) -> None:
        self.started.append(kwargs)

    def span_finished(self, **kwargs: Any) -> None:
        self.finished.append(kwargs)


class FakeLangSmithClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    def create_run(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def update_run(self, *args: Any, **kwargs: Any) -> None:
        self.updated.append({"args": args, **kwargs})


def _run(tmp_path: Path) -> Any:
    return RunStore(tmp_path).create(
        task="observability",
        project="moe-pimc",
        now=datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc),
    )


def test_trace_recorder_keeps_file_only_behavior_when_langsmith_disabled(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(tracing, "get_langsmith_sink", lambda: DisabledSink())
    run = _run(tmp_path)

    recorder = TraceRecorder(run)
    with recorder.start_span(name="node:idea", kind="idea", attributes={"node": "idea"}):
        pass

    manifest = json.loads((run.subdir("context") / "trace_manifest.v1.json").read_text())
    span = manifest["spans"][0]
    assert span["status"] == "ok"
    assert span["attributes"] == {"node": "idea"}


def test_trace_recorder_mirrors_span_to_langsmith_sink(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    sink = RecordingSink()
    monkeypatch.setattr(tracing, "get_langsmith_sink", lambda: sink)
    run = _run(tmp_path)

    recorder = TraceRecorder(run)
    with recorder.start_span(name="node:coding", kind="coding", attributes={"node": "coding"}):
        pass

    assert len(sink.started) == 1
    assert len(sink.finished) == 1
    started_span = sink.started[0]["span"]
    finished_span = sink.finished[0]["span"]
    assert started_span.attributes["langsmith_run_id"] == "00000000-0000-0000-0000-000000000123"
    assert finished_span["attributes"]["langsmith_run_id"] == "00000000-0000-0000-0000-000000000123"
    assert sink.started[0]["run_id"] == run.run_id
    assert sink.finished[0]["run_id"] == run.run_id


def test_langsmith_sink_mirrors_with_redacted_attributes() -> None:
    client = FakeLangSmithClient()
    sink = LangSmithSink(
        LangSmithSinkConfig(
            enabled=True,
            api_key="test-key",
            endpoint="https://smith.example.test",
            project="mars-test",
            timeout_ms=1000,
        )
    )
    sink._client = client

    span = tracing.TraceSpan(
        span_id="span1",
        parent_span_id=None,
        name="tool:code.apply_patch",
        kind="tool",
        started_at="2026-06-17T10:00:00+00:00",
        ended_at=None,
        status="running",
        attributes={
            "langsmith_run_id": "00000000-0000-0000-0000-000000000456",
            "api_key": "raw-key",
            "nested": {"password": "raw-pass"},
        },
    )

    sink.span_started(run_id="r1", trace_id="trace1", span=span)
    finished = {
        "status": "ok",
        "ended_at": "2026-06-17T10:00:01+00:00",
        "attributes": span.attributes,
    }
    sink.span_finished(run_id="r1", trace_id="trace1", span=finished)

    assert len(client.created) == 1
    assert len(client.updated) == 1
    created_attrs = client.created[0]["inputs"]["attributes"]
    updated_attrs = client.updated[0]["outputs"]["attributes"]
    assert client.created[0]["project_name"] == "mars-test"
    assert created_attrs["api_key"] == "[redacted]"
    assert created_attrs["nested"]["password"] == "[redacted]"
    assert updated_attrs["api_key"] == "[redacted]"
    assert updated_attrs["nested"]["password"] == "[redacted]"


def test_langsmith_sink_reads_runtime_enable_switch(monkeypatch: Any) -> None:
    import app.settings as settings_mod

    monkeypatch.setenv("MARS_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    settings_mod._settings = None
    reset_langsmith_sink_for_tests()

    sink = get_langsmith_sink()

    assert sink.enabled is True
    assert sink.config.api_key == "test-key"
    reset_langsmith_sink_for_tests()
    settings_mod._settings = None
