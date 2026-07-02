from __future__ import annotations

import json
from pathlib import Path

from app.api.timeline import (
    _context_manifest_items,
    _event_items,
    _tool_call_worklog,
    _trace_items,
)


def test_timeline_merges_events_trace_and_context(tmp_path: Path) -> None:
    events = tmp_path / "events"
    events.mkdir()
    (events / "reporting_events.jsonl").write_text(
        json.dumps(
            {
                "event": "reporting.deliverable_completed",
                "timestamp": "2026-06-20T00:00:00+00:00",
                "kind": "excel",
                "path": "writing/deliverables/results_workbook.xlsx",
                "status": "completed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    context = tmp_path / "context"
    context.mkdir()
    (context / "trace_manifest.v2.json").write_text(
        json.dumps(
            {
                "spans": [
                    {
                        "span_id": "s1",
                        "name": "node:writing",
                        "kind": "agent",
                        "started_at": "2026-06-20T00:00:01+00:00",
                        "status": "ok",
                        "attributes": {"node": "writing", "stage": "writing"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (context / "context_manifest.v2.json").write_text(
        json.dumps(
            {
                "schema": "context_manifest.v2",
                "created_at": "2026-06-20T00:00:02+00:00",
                "summary": "loaded",
            }
        ),
        encoding="utf-8",
    )

    items = [*_event_items(events), *_trace_items(context / "trace_manifest.v2.json"), *_context_manifest_items(context)]

    assert {item.kind for item in items} == {"reporting", "trace_span", "context_manifest"}
    assert any(item.title == "reporting.deliverable_completed" for item in items)


def test_worklog_translates_execution_tool_artifacts() -> None:
    item = _tool_call_worklog(
        index=1,
        payload={
            "timestamp": "2026-06-20T00:00:03+00:00",
            "agent": "execution",
            "tool": "execution.batch_runner",
            "status": "success",
            "ok": True,
            "args": {"experiments": [{"experiment_id": "exp1"}, {"experiment_id": "exp2"}]},
            "output_summary": {"backend": "mock", "results_count": 2},
            "metrics": {"exp1": {"RES": -31.2}},
            "artifacts": [
                {"kind": "metrics", "path": "runs/demo/execution/metrics.json"},
                {"kind": "curve", "path": "runs/demo/execution/curves/exp1_loss.json"},
            ],
        },
    )

    assert item.title == "运行实验批次"
    assert "experiments=2" in item.detail
    assert "artifacts=2" in item.detail
    assert "runs/demo/execution/metrics.json" in item.evidence_refs


def test_worklog_translates_coding_and_writing_tools() -> None:
    coding = _tool_call_worklog(
        index=1,
        payload={
            "agent": "coding",
            "tool": "code.patch_generator",
            "status": "success",
            "ok": True,
            "args": {"path": "libs/model.py"},
            "evidence_refs": ["libs/model.py"],
        },
    )
    writing = _tool_call_worklog(
        index=2,
        payload={
            "agent": "writing",
            "tool": "reporting.generate_bundle",
            "status": "success",
            "ok": True,
            "args": {},
            "artifacts": [
                {"kind": "report_bundle", "path": "writing/report_bundle.v1.json"}
            ],
        },
    )

    assert coding.title == "生成/规范化补丁"
    assert coding.evidence_refs == ["libs/model.py"]
    assert writing.title == "生成报告附件包"
    assert writing.evidence_refs == ["writing/report_bundle.v1.json"]
