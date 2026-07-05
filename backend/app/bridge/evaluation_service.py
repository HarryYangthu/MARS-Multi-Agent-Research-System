"""Bridge-facing evaluation summaries and events."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.bridge.evaluation_policy import evaluate_artifact_summary, evaluate_scorecard
from app.harness.evaluation.artifacts import read_reports_for_artifact
from app.harness.evaluation.models import EvaluationDecision
from app.harness.runtime.event_bus import EventBus
from app.storage.artifact_store import ArtifactRef
from app.storage.run_store import RunHandle

_DECISION_RANK: dict[EvaluationDecision, int] = {
    "pass": 0,
    "warn": 1,
    "revise": 2,
    "block": 3,
    "fail": 4,
}
_SEVERITY_RANK = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def build_artifact_evaluation_summary(
    *,
    run: RunHandle,
    ref: ArtifactRef,
    node_key: str | None = None,
) -> dict[str, Any]:
    reports = read_reports_for_artifact(
        run_root=run.root,
        agent_dir=ref.agent_dir,
        stem=ref.stem,
        version=ref.version,
    )
    report_items = [_compact_report(report) for report in reports]
    decisions: list[EvaluationDecision] = []
    for item in report_items:
        item_decision = _as_decision(item.get("decision"))
        if item_decision is not None:
            decisions.append(item_decision)
    scores = [
        float(score)
        for item in report_items
        if isinstance(score := item.get("overall_score"), int | float)
    ]
    decision = (
        max(decisions, key=lambda value: _DECISION_RANK[value])
        if decisions
        else "pass"
    )
    blocking = any(
        bool(item.get("blocking")) or item.get("decision") in {"block", "fail"}
        for item in report_items
    )
    summary = {
        "agent": ref.agent_dir,
        "node": node_key or ref.agent_dir,
        "artifact_ref": ref.path.relative_to(run.root).as_posix(),
        "artifact_id": ref.filename,
        "stem": ref.stem,
        "version": ref.version,
        "decision": decision,
        "blocking": blocking,
        "report_count": len(report_items),
        "overall_score": round(sum(scores) / len(scores), 6) if scores else None,
        "top_findings": _top_findings(report_items),
        "reports": report_items,
    }
    summary["policy"] = evaluate_artifact_summary(summary)
    return summary


async def emit_artifact_evaluation_event(
    *,
    run: RunHandle,
    ref: ArtifactRef,
    node_key: str,
    bus: EventBus | None = None,
) -> dict[str, Any]:
    payload = {
        "event": "evaluation.artifact_evaluated",
        "run_id": run.run_id,
        "project": run.project,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        **build_artifact_evaluation_summary(run=run, ref=ref, node_key=node_key),
    }
    run.write_event("evaluation_events", payload)
    if bus is not None:
        await bus.publish(f"run.{run.run_id}.evaluation", payload)
    return payload


async def emit_scorecard_event(
    *,
    run: RunHandle,
    path: str,
    bus: EventBus | None = None,
) -> dict[str, Any]:
    quality_gate = _quality_gate_for_scorecard(run=run, relative_path=path)
    payload = {
        "event": "evaluation.scorecard_written",
        "agent": "evaluation",
        "run_id": run.run_id,
        "project": run.project,
        "path": path,
        "quality_gate": quality_gate,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    run.write_event("evaluation_events", payload)
    if bus is not None:
        await bus.publish(f"run.{run.run_id}.evaluation", payload)
    return payload


def _quality_gate_for_scorecard(*, run: RunHandle, relative_path: str) -> dict[str, Any]:
    path = run.root / relative_path
    scorecard: dict[str, Any] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            scorecard = loaded
    quality_gate = evaluate_scorecard(scorecard)
    target = run.subdir("events") / "evaluation_quality_gate.json"
    target.write_text(json.dumps(quality_gate, indent=2, ensure_ascii=False), encoding="utf-8")
    return quality_gate


def _compact_report(report: dict[str, Any]) -> dict[str, Any]:
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    findings = metadata.get("findings")
    return {
        "path": report.get("path"),
        "target_ref": metadata.get("target_ref"),
        "target_schema": metadata.get("target_schema"),
        "evaluator": metadata.get("evaluator"),
        "decision": metadata.get("decision"),
        "blocking": bool(metadata.get("blocking")),
        "overall_score": metadata.get("overall_score"),
        "finding_count": len(findings) if isinstance(findings, list) else 0,
        "findings": findings if isinstance(findings, list) else [],
    }


def _as_decision(value: object) -> EvaluationDecision | None:
    if value == "pass":
        return "pass"
    if value == "warn":
        return "warn"
    if value == "revise":
        return "revise"
    if value == "block":
        return "block"
    if value == "fail":
        return "fail"
    return None


def _top_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in items:
        raw_findings = item.get("findings")
        if not isinstance(raw_findings, list):
            continue
        for finding in raw_findings:
            if not isinstance(finding, dict):
                continue
            copied = dict(finding)
            copied["target_ref"] = item.get("target_ref")
            copied["evaluator"] = item.get("evaluator")
            findings.append(copied)
    findings.sort(
        key=lambda item: _SEVERITY_RANK.get(str(item.get("severity")), 99)
    )
    return findings[:5]


__all__ = [
    "build_artifact_evaluation_summary",
    "emit_artifact_evaluation_event",
    "emit_scorecard_event",
]
