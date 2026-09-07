"""Actual local tracing/configuration and pure redaction checks; no remote sink/client doubles."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest

from app.harness.observability.langsmith_sink import (
    LangSmithSink, LangSmithSinkConfig, _redact, _run_type,
    get_langsmith_sink, reset_langsmith_sink_for_tests,
)
from app.harness.observability.tracing import TraceRecorder
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def reset_observability_settings() -> Iterator[None]:
    reset_langsmith_sink_for_tests()
    reset_settings_cache()
    yield
    reset_langsmith_sink_for_tests()
    reset_settings_cache()


def test_actual_trace_recorder_keeps_file_output_when_remote_sink_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_LANGSMITH_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    run = RunStore(tmp_path).create(task="local-trace-contract", project="pimc")
    recorder = TraceRecorder(run)
    with recorder.start_span(name="local:contract", kind="local", attributes={"purpose": "unit-file-check"}):
        (tmp_path / "operation.txt").write_text("actual local operation")
    manifest = json.loads((run.subdir("context") / "trace_manifest.v2.json").read_text())
    assert manifest["spans"][0]["status"] == "ok"
    assert manifest["spans"][0]["attributes"] == {"purpose": "unit-file-check"}
    assert get_langsmith_sink().enabled is False and get_langsmith_sink()._client is None


def test_actual_failed_local_span_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_LANGSMITH_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    run = RunStore(tmp_path).create(task="failed-local-trace", project="pimc")
    with pytest.raises(FileNotFoundError):
        with TraceRecorder(run).start_span(name="local:missing-file", kind="local"):
            (tmp_path / "missing.txt").read_text()
    manifest = json.loads((run.subdir("context") / "trace_manifest.v2.json").read_text())
    assert manifest["spans"][0]["status"] == "error"


def test_nested_redaction_preserves_nonsecret_context_without_mutating_input() -> None:
    # Pure authored payload, never sent to a service.
    value = {"api_key": "sensitive-field-input", "nested": {"password": "sensitive-field-input"},
             "records": [{"Authorization": "sensitive-field-input"}, {"metric": 2}],
             "tuple": ({"Cookie": "sensitive-field-input"},), "name": "allowed"}
    redacted = _redact(value)
    assert redacted["api_key"] == redacted["nested"]["password"] == "[redacted]"
    assert redacted["records"] == [{"Authorization": "[redacted]"}, {"metric": 2}]
    assert redacted["tuple"] == [{"Cookie": "[redacted]"}] and redacted["name"] == "allowed"
    assert value["api_key"] == "sensitive-field-input"


@pytest.mark.parametrize("kind,expected", [("llm", "llm"), ("tool", "tool"), ("gate", "tool"), ("agent", "chain")])
def test_remote_run_type_mapping_is_pure(kind: str, expected: str) -> None:
    assert _run_type(kind) == expected


def test_disabled_or_unconfigured_sink_never_connects() -> None:
    for enabled, key in ((False, "unused-config-input"), (True, "")):
        sink = LangSmithSink(LangSmithSinkConfig(enabled=enabled, api_key=key,
                            endpoint="https://example.invalid", project="unit-config", timeout_ms=100))
        assert sink.enabled is False and sink._client is None
        assert UUID(sink.new_run_id()).version == 4


def test_runtime_enable_switch_is_configuration_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "configuration-only-not-a-credential")
    sink = get_langsmith_sink()
    assert sink.enabled is True and sink._client is None
