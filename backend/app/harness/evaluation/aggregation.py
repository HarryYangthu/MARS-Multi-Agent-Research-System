"""Small aggregation helpers for evaluation reports."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeGuard

from app.harness.evaluation.artifacts import read_all_reports
from app.harness.evaluation.models import EvaluationDecision, EvaluationReport

_RANK: dict[EvaluationDecision, int] = {
    "pass": 0,
    "warn": 1,
    "revise": 2,
    "block": 3,
    "fail": 4,
}


def worst_decision(reports: list[EvaluationReport]) -> EvaluationDecision:
    if not reports:
        return "pass"
    return max((report.decision for report in reports), key=lambda d: _RANK[d])


def has_blocker(reports: list[EvaluationReport]) -> bool:
    return any(report.blocking or report.decision == "block" for report in reports)


def build_scorecard(
    *,
    run_root: Path,
    run_id: str,
    project: str,
) -> dict[str, Any]:
    reports = read_all_reports(run_root=run_root)
    report_items = [_scorecard_item(report) for report in reports]
    decisions = [
        decision
        for item in report_items
        if _is_decision(decision := item.get("decision"))
    ]
    overall_decision = (
        max(decisions, key=lambda d: _RANK[d]) if decisions else "pass"
    )
    scores = [
        float(item["overall_score"])
        for item in report_items
        if isinstance(item.get("overall_score"), (int, float))
    ]
    counts = {decision: decisions.count(decision) for decision in _RANK}
    findings = _top_findings(report_items)
    return {
        "schema": "evaluation_scorecard.v1",
        "run_id": run_id,
        "project": project,
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "overall_decision": overall_decision,
        "overall_score": round(sum(scores) / len(scores), 6) if scores else None,
        "counts": counts,
        "report_count": len(report_items),
        "finding_count": sum(len(item["findings"]) for item in report_items),
        "top_findings": findings,
        "reports": report_items,
    }


def write_scorecard(
    *,
    run_root: Path,
    run_id: str,
    project: str,
) -> Path:
    scorecard = build_scorecard(run_root=run_root, run_id=run_id, project=project)
    target = run_root / "events" / "evaluation_scorecard.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(scorecard, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def _scorecard_item(report: dict[str, Any]) -> dict[str, Any]:
    metadata = report["metadata"]
    findings = metadata.get("findings", [])
    return {
        "path": report["path"],
        "target_ref": metadata.get("target_ref"),
        "target_schema": metadata.get("target_schema"),
        "evaluator": metadata.get("evaluator"),
        "decision": metadata.get("decision"),
        "blocking": bool(metadata.get("blocking")),
        "overall_score": metadata.get("overall_score"),
        "findings": findings if isinstance(findings, list) else [],
    }


def _is_decision(value: object) -> TypeGuard[EvaluationDecision]:
    return value in _RANK


def _top_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    severity_rank = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings: list[dict[str, Any]] = []
    for item in items:
        for finding in item["findings"]:
            if not isinstance(finding, dict):
                continue
            copied = dict(finding)
            copied["target_ref"] = item.get("target_ref")
            copied["evaluator"] = item.get("evaluator")
            findings.append(copied)
    findings.sort(key=lambda f: severity_rank.get(str(f.get("severity")), 99))
    return findings[:10]
