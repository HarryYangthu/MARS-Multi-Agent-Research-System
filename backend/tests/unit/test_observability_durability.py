"""Real file/process concurrency and parser inputs; no provider/tool substitutes."""
from __future__ import annotations

import json
import multiprocessing
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from app.bridge.run_observability import build_run_observability
from app.harness.observability.events import make_event, normalize_event, write_event
from app.harness.observability.langsmith_sink import reset_langsmith_sink_for_tests
from app.harness.observability.tracing import TraceRecorder
from app.harness.persistence import append_jsonl, atomic_write_json
from app.settings import reset_settings_cache
from app.storage.run_store import RunHandle, RunStore


@pytest.fixture(autouse=True)
def local_observability_configuration(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MARS_LANGSMITH_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    reset_settings_cache()
    reset_langsmith_sink_for_tests()
    yield
    reset_settings_cache()
    reset_langsmith_sink_for_tests()


def _record_local_files(run: RunHandle, prefix: str, count: int) -> str:
    recorder = TraceRecorder(run)
    for index in range(count):
        marker = f"{prefix}-{index}"
        with recorder.start_span(name=marker, kind="filesystem", attributes={"marker": marker}):
            target = run.subdir("context") / f"{marker}.txt"
            target.write_text(marker, encoding="utf-8")
            assert target.read_text(encoding="utf-8") == marker
            recorder.record_event_ref(channel="local", event="file.persisted", payload={"version": marker})
            write_event(run=run, stream="local", channel="local", kind="file.persisted",
                        source={"component": "file-check"}, payload={"marker": marker, "padding": "x" * 8192})
    return str(recorder.ensure_manifest()["trace_id"])


@pytest.mark.parametrize("processes", [False, True])
def test_real_concurrent_manifest_transactions_and_event_appends(tmp_path: Path, processes: bool) -> None:
    run = RunStore(tmp_path).create(task="concurrent-files", project="pimc")
    if processes:
        with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = [pool.submit(_record_local_files, run, str(index), 8) for index in range(6)]
            trace_ids = [future.result(timeout=30) for future in futures]
    else:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_record_local_files, run, str(index), 8) for index in range(6)]
            trace_ids = [future.result(timeout=30) for future in futures]
    manifest = TraceRecorder(run).ensure_manifest()
    spans = manifest["spans"]
    assert len(set(trace_ids)) == 1
    assert len(spans) == len({span["span_id"] for span in spans}) == 48
    assert all(span["status"] == "ok" and span["ended_at"] for span in spans)
    assert len(manifest["event_index"]) == 48
    rows = [json.loads(line) for line in (run.subdir("events") / "local.jsonl").read_text().splitlines()]
    assert len(rows) == len({row["payload"]["marker"] for row in rows}) == 48
    assert all(row["payload"]["padding"] == "x" * 8192 for row in rows)


@pytest.mark.parametrize("contents", ['{"spans":', '[]', '{}', '{"spans": []}'])
def test_corrupt_manifest_is_reported_without_overwrite(tmp_path: Path, contents: str) -> None:
    run = RunStore(tmp_path).create(task="corrupt-manifest", project="pimc")
    path = run.subdir("context") / "trace_manifest.v2.json"
    path.write_text(contents)
    with pytest.raises(ValueError, match="trace manifest"):
        TraceRecorder(run).start_span(name="read", kind="filesystem")
    assert path.read_text() == contents
    trace = build_run_observability(run)["trace"]
    assert trace["integrity"]["consistent"] is False
    assert trace["diagnostics"]


def test_event_correlation_is_explicit_and_legacy_time_is_preserved(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="correlation", project="pimc")
    correlation = {"task_id": "task", "parent_task_id": "parent", "invocation_id": "invocation"}
    event = write_event(run=run, stream="local", channel="local", kind="file.persisted",
                        source={"component": "file-check"}, payload={}, correlation=correlation)
    assert event["correlation"] == correlation
    stored = json.loads((run.subdir("events") / "local.jsonl").read_text())
    assert stored["correlation"] == correlation
    normalized = normalize_event({"time": "2026-09-17T00:00:00+00:00", "correlation": correlation},
                                 run_id=run.run_id, project=run.project,
                                 default_channel="local", default_kind="file.persisted")
    assert normalized["correlation"] == correlation
    assert normalized["timestamp"] == "2026-09-17T00:00:00+00:00"
    assert make_event(run_id=run.run_id, project=run.project, channel="local", kind="file.persisted",
                      source={}, payload={})["correlation"] == {}


def _parser_input(
    run: RunHandle, name: str, *, usage: dict[str, int] | None,
    correlation: dict[str, str] | None = None,
) -> Path:
    """Persist caller-authored audit records to test reading, never execute an Agent."""
    root = run.root / "agent_traces" / "parser-input" / name
    counts = {"model_requests": 1, "model_responses": 1, "tool_dispatches": 0,
              "observations": 0, "sdk_attempts": 0, "reflections": 0}
    atomic_write_json(root / "facts.json", {
        "status": "interrupted", "trace_mode": "full", "counts": counts,
        "usage": usage, "usage_complete": usage is not None,
        "fingerprint": "caller-authored-parser-input", "event_seq": 2,
        **({"correlation": correlation} if correlation else {}),
    })
    for sequence, kind in enumerate(("model_request", "model_response"), 1):
        append_jsonl(root / "events.jsonl", {
            "event_seq": sequence, "time": f"2026-09-17T00:00:0{sequence}+00:00",
            "kind": kind, **({"usage": usage} if kind == "model_response" else {}),
            **({"correlation": correlation} if correlation else {}),
        })
    return root


def test_loop_summary_uses_real_file_events_and_preserves_ui_fields(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="loop-summary", project="pimc")
    with TraceRecorder(run).start_span(name="local-read", kind="filesystem"):
        assert run.root.exists()
    root = _parser_input(run, "one", usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                         correlation={"task_id": "task", "invocation_id": "one", "node_id": "node"})
    result = build_run_observability(run)
    trace = result["trace"]
    assert trace["span_count"] == 1 and trace["status_counts"] == {"ok": 1}
    assert trace["kind_counts"] == {"filesystem": 1} and trace["latest_spans"]
    assert trace["counts"]["model_requests"] == trace["counts"]["model_responses"] == 1
    assert trace["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert trace["usage_complete"] and trace["integrity"]["consistent"]
    invocation = trace["invocations"][0]
    assert invocation["correlation"] == {"task_id": "task", "invocation_id": "one", "node_id": "node"}
    assert "parent_task_id" not in invocation["correlation"]
    assert result["timeline"][0]["kind"] == "model_response"
    assert result["timeline"][0]["timestamp"] == "2026-09-17T00:00:02+00:00"
    assert result["timeline"][0]["correlation"]["invocation_id"] == "one"
    assert (root / "events.jsonl").relative_to(run.root).as_posix() in result["event_streams"]
    assert build_run_observability(run, limit=0)["timeline"] == []


def test_unknown_usage_is_not_reported_as_zero_and_legacy_ids_are_not_invented(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="unknown-usage", project="pimc")
    _parser_input(run, "known", usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
    _parser_input(run, "unknown", usage=None)
    trace = build_run_observability(run)["trace"]
    assert trace["counts"]["model_requests"] == 2
    assert trace["known_usage"]["total_tokens"] == 10
    assert trace["usage"] is None and trace["usage_complete"] is False
    assert all(item["correlation"] == {} for item in trace["invocations"])


@pytest.mark.parametrize("damage,code", [
    ("broken_json", "invalid_jsonl"), ("sequence", "event_sequence_discontinuous"),
    ("missing_facts", "missing_facts"), ("counts", "count_mismatch"),
    ("usage", "usage_mismatch"), ("correlation", "correlation_conflict"),
])
def test_loop_corruption_is_diagnosed_and_valid_event_counts_survive(tmp_path: Path, damage: str, code: str) -> None:
    run = RunStore(tmp_path).create(task="trace-damage", project="pimc")
    root = _parser_input(run, "one", usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                         correlation={"invocation_id": "one"})
    path = root / "facts.json"
    facts: dict[str, Any] = json.loads(path.read_text())
    if damage == "broken_json":
        with (root / "events.jsonl").open("a") as stream:
            stream.write('{"event_seq":')
    elif damage == "sequence":
        append_jsonl(root / "events.jsonl", {"event_seq": 99, "kind": "interrupted"})
    elif damage == "missing_facts":
        path.unlink()
    elif damage == "counts":
        facts["counts"]["model_requests"] = 40
        atomic_write_json(path, facts)
    elif damage == "usage":
        facts["usage"]["total_tokens"] = 100
        atomic_write_json(path, facts)
    elif damage == "correlation":
        facts["correlation"]["invocation_id"] = "different"
        atomic_write_json(path, facts)
    trace = build_run_observability(run)["trace"]
    assert trace["counts"]["model_requests"] == 1
    assert trace["integrity"]["consistent"] is False
    assert code in {item["code"] for item in trace["diagnostics"]}
    assert trace["usage"] is None


def test_trace_off_exposes_facts_source_and_unavailable_event_verification(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="trace-off", project="pimc")
    root = _parser_input(run, "one", usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
    facts = json.loads((root / "facts.json").read_text())
    facts["trace_mode"] = "off"
    atomic_write_json(root / "facts.json", facts)
    (root / "events.jsonl").unlink()
    trace = build_run_observability(run)["trace"]
    invocation = trace["invocations"][0]
    assert invocation["counts_source"] == "facts" and invocation["counts"]["model_requests"] == 1
    assert invocation["integrity"]["consistent"] is None
    assert invocation["integrity"]["trace_available"] is False
    assert trace["integrity"]["consistent"] is None
    assert trace["usage"]["total_tokens"] == 10 and trace["usage_complete"] is True
