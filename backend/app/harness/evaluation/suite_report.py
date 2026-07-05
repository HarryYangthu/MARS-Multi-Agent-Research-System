"""Suite-level aggregation for V1/V2 evaluation runs."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.evaluation.run_report import RunEvaluationResult
from app.harness.evaluation.run_types import EvaluationRun
from app.harness.evaluation.self_evolution import (
    build_suite_self_evolution_export,
    load_run_candidates,
    write_jsonl,
)
from app.harness.evaluation.suites import EvaluationSuite
from app.settings import repo_root

_DECISION_RANK = {"pass": 0, "warn": 1, "revise": 2, "block": 3, "fail": 4}


@dataclass(frozen=True)
class SuiteTrialResult:
    run: EvaluationRun
    evaluation: RunEvaluationResult


@dataclass(frozen=True)
class SuiteEvaluationResult:
    output_dir: Path
    report_json_path: Path
    report_markdown_path: Path
    scorecard_path: Path
    self_evolution_export_path: Path


def write_suite_report(
    *,
    suite: EvaluationSuite,
    trials: list[SuiteTrialResult],
    output_dir: Path | None = None,
) -> SuiteEvaluationResult:
    target_dir = output_dir or _default_output_dir(suite.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    run_items = [_run_item(trial) for trial in trials]
    pass_count = sum(1 for item in run_items if item["overall_decision"] in {"pass", "warn"})
    n = len(run_items)
    p = pass_count / n if n else 0.0
    decisions = [str(item["overall_decision"]) for item in run_items]
    overall_decision = max(decisions, key=lambda d: _DECISION_RANK.get(d, 99)) if decisions else "fail"
    scores = [
        float(item["overall_score"])
        for item in run_items
        if isinstance(item.get("overall_score"), int | float)
    ]
    self_evolution_exports = build_suite_self_evolution_export(
        suite_id=suite.id,
        run_items=run_items,
    )
    self_evolution_path = write_jsonl(
        target_dir / "self_evolution_export.jsonl",
        self_evolution_exports,
    )
    report = {
        "schema": "evaluation_suite_report.v1",
        "suite": suite.id,
        "mode": suite.mode,
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "trial_count": n,
        "pass_count": pass_count,
        "overall_decision": overall_decision,
        "overall_score": round(sum(scores) / len(scores), 6) if scores else None,
        "pass_rate": round(p, 6),
        "pass_at_k": round(1 - ((1 - p) ** n), 6) if n else 0.0,
        "pass_power_k": round(p**n, 6) if n else 0.0,
        "self_evolution_item_count": len(self_evolution_exports),
        "runs": run_items,
    }
    report_json_path = target_dir / "report.json"
    scorecard_path = target_dir / "scorecard.json"
    report_json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    scorecard_path.write_text(json.dumps(_scorecard(report), indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path = _write_markdown(
        target_dir=target_dir,
        suite=suite,
        report=report,
        self_evolution_path=self_evolution_path,
    )
    return SuiteEvaluationResult(
        output_dir=target_dir,
        report_json_path=report_json_path,
        report_markdown_path=markdown_path,
        scorecard_path=scorecard_path,
        self_evolution_export_path=self_evolution_path,
    )


def _run_item(trial: SuiteTrialResult) -> dict[str, Any]:
    scorecard_path = trial.evaluation.scorecard_path
    scorecard: dict[str, Any] = {}
    if scorecard_path.exists():
        loaded = json.loads(scorecard_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            scorecard = loaded
    return {
        "run_id": trial.run.run_id,
        "project": trial.run.project,
        "task": trial.run.task,
        "overall_decision": scorecard.get("overall_decision", "fail"),
        "overall_score": scorecard.get("overall_score"),
        "report_count": scorecard.get("report_count", 0),
        "finding_count": scorecard.get("finding_count", 0),
        "top_findings": scorecard.get("top_findings", []),
        "evaluation_report": trial.evaluation.markdown_report_path.as_posix(),
        "scorecard": scorecard_path.as_posix(),
        "self_evolution_candidates": load_run_candidates(trial.run.root),
    }


def _scorecard(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "evaluation_suite_scorecard.v1",
        "suite": report["suite"],
        "created": report["created"],
        "overall_decision": report["overall_decision"],
        "overall_score": report["overall_score"],
        "trial_count": report["trial_count"],
        "pass_rate": report["pass_rate"],
        "pass_at_k": report["pass_at_k"],
        "pass_power_k": report["pass_power_k"],
        "self_evolution_item_count": report["self_evolution_item_count"],
    }


def _write_markdown(
    *,
    target_dir: Path,
    suite: EvaluationSuite,
    report: dict[str, Any],
    self_evolution_path: Path,
) -> Path:
    lines = [
        "# MARS Evaluation Suite Report",
        "",
        f"- Suite: `{suite.id}`",
        f"- Mode: `{suite.mode}`",
        f"- Trials: `{report['trial_count']}`",
        f"- Decision: `{report['overall_decision']}`",
        f"- Overall score: `{report['overall_score']}`",
        f"- Pass rate: `{report['pass_rate']}`",
        f"- pass@k: `{report['pass_at_k']}`",
        f"- pass^k: `{report['pass_power_k']}`",
        f"- Self-evolution export: `{self_evolution_path.name}`",
        "",
        "## Trial Results",
        "",
        "| Run | Decision | Score | Findings |",
        "|---|---:|---:|---:|",
    ]
    for item in report["runs"]:
        lines.append(
            f"| `{item['run_id']}` | `{item['overall_decision']}` | {item['overall_score']} | {item['finding_count']} |"
        )
    lines.extend(["", "## Highest Priority Findings", ""])
    top_findings: list[dict[str, Any]] = []
    for item in report["runs"]:
        for finding in item.get("top_findings", []):
            if isinstance(finding, dict):
                copied = dict(finding)
                copied["run_id"] = item["run_id"]
                top_findings.append(copied)
    if not top_findings:
        lines.append("- No findings.")
    else:
        severity_rank = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        top_findings.sort(key=lambda item: severity_rank.get(str(item.get("severity")), 99))
        for finding in top_findings[:12]:
            lines.append(
                f"- `{finding.get('severity')}` `{finding.get('category')}` in `{finding.get('run_id')}`: {finding.get('message')}"
            )
    lines.append("")
    target = target_dir / "report.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _default_output_dir(suite_id: str) -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_suite = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in suite_id)
    return repo_root() / "evaluation_runs" / f"{ts}_{safe_suite}"
