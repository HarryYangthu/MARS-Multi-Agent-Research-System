"""Replay a completed run and write run-level evaluation outputs."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.evaluation.aggregation import write_scorecard
from app.harness.evaluation.artifacts import evaluator_slug, write_report
from app.harness.evaluation.models import EvaluationReport
from app.harness.evaluation.run_types import EvaluationRun
from app.harness.evaluation.run_evaluators import (
    RunEvaluator,
    default_run_evaluators,
)
from app.harness.evaluation.suites import EvaluationSuite, load_suite


@dataclass(frozen=True)
class RunEvaluationResult:
    reports: tuple[EvaluationReport, ...]
    report_paths: tuple[Path, ...]
    markdown_report_path: Path
    scorecard_path: Path
    self_evolution_candidates_path: Path
    human_review_queue_path: Path


def evaluate_run_replay(
    *,
    run: EvaluationRun,
    suite: EvaluationSuite | None = None,
    evaluators: tuple[RunEvaluator, ...] | None = None,
) -> RunEvaluationResult:
    active_suite = suite or load_suite()
    active_evaluators = evaluators or default_run_evaluators()
    reports = tuple(
        evaluator.evaluate_run(run=run, suite=active_suite)
        for evaluator in active_evaluators
    )
    report_paths = tuple(_write_run_eval_report(run=run, report=report) for report in reports)
    scorecard_path = write_scorecard(
        run_root=run.root,
        run_id=run.run_id,
        project=run.project,
    )
    candidates_path = _write_self_evolution_candidates(
        run=run,
        suite=active_suite,
        reports=reports,
    )
    human_review_queue_path = _write_human_review_queue(
        run=run,
        suite=active_suite,
        reports=reports,
    )
    markdown_path = _write_markdown_report(
        run=run,
        suite=active_suite,
        reports=reports,
        scorecard_path=scorecard_path,
        candidates_path=candidates_path,
        human_review_queue_path=human_review_queue_path,
    )
    return RunEvaluationResult(
        reports=reports,
        report_paths=report_paths,
        markdown_report_path=markdown_path,
        scorecard_path=scorecard_path,
        self_evolution_candidates_path=candidates_path,
        human_review_queue_path=human_review_queue_path,
    )


def _write_run_eval_report(*, run: EvaluationRun, report: EvaluationReport) -> Path:
    path = (
        run.root
        / "events"
        / "evals"
        / f"run.v1.{evaluator_slug(report.evaluator)}.eval.md"
    )
    return write_report(path, report)


def _write_markdown_report(
    *,
    run: EvaluationRun,
    suite: EvaluationSuite,
    reports: tuple[EvaluationReport, ...],
    scorecard_path: Path,
    candidates_path: Path,
    human_review_queue_path: Path,
) -> Path:
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    decision = str(scorecard.get("overall_decision", "pass"))
    score = scorecard.get("overall_score")
    advisory_score = scorecard.get("advisory_score")
    grader_counts = scorecard.get("grader_counts", {})
    findings = _top_findings(reports)
    lines = [
        "# MARS Evaluation Harness Report",
        "",
        f"- Run: `{run.run_id}`",
        f"- Project: `{run.project}`",
        f"- Suite: `{suite.id}`",
        f"- Decision: `{decision}`",
        f"- Overall score: `{score}`",
        f"- Advisory score: `{advisory_score}`",
        f"- Scorecard: `{scorecard_path.relative_to(run.root).as_posix()}`",
        f"- Self-evolution candidates: `{candidates_path.relative_to(run.root).as_posix()}`",
        f"- Human review queue: `{human_review_queue_path.relative_to(run.root).as_posix()}`",
        "",
        "## Grader Stack",
        "",
        "| Layer | Count | Role | Blocks run? |",
        "|---|---:|---|---|",
        f"| Code / deterministic | {int(grader_counts.get('code', 0))} | Schema, run integrity, trajectory, outcome, gate and collaboration checks | Yes |",
        f"| LLM rubric | {int(grader_counts.get('llm', 0))} | Subjective quality review with insufficient-info escape hatch | No, advisory + calibration |",
        f"| Human review | {int(grader_counts.get('human', 0))} | Gold-standard labels and high-risk review queue | No, unless promoted through Gate/HITL policy |",
        f"| Pending human review | {int(grader_counts.get('requires_human_review', 0))} | Items exported for reviewer labels | Review required before self-evolution promotion |",
        "",
        "## Evaluator Results",
        "",
        "| Evaluator | Type | Mode | Decision | Score | Findings |",
        "|---|---|---|---:|---:|---:|",
    ]
    for report in reports:
        score_text = (
            f"{report.overall_score:.3f}"
            if report.overall_score is not None
            else ""
        )
        mode = "advisory" if report.advisory else "blocking-capable"
        lines.append(
            f"| `{report.evaluator}` | `{report.grader_type}` | `{mode}` | `{report.decision}` | {score_text} | {len(report.findings)} |"
        )
    lines.extend(["", "## Findings", ""])
    if not findings:
        lines.append("- No run-level findings.")
    else:
        for finding in findings:
            refs = ", ".join(f"`{ref}`" for ref in finding.evidence_refs)
            lines.append(
                f"- `{finding.severity}` `{finding.category}`: {finding.message} Evidence: {refs}"
            )
    lines.extend(["", "## Recommended Actions", ""])
    actions = [action for report in reports for action in report.recommended_actions]
    if not actions:
        lines.append("- No action required.")
    else:
        for action in dict.fromkeys(actions):
            lines.append(f"- {action}")
    lines.extend(
        [
            "",
            "## Self-Evolution Use",
            "",
            "This report is replay-safe: it is derived from run artifacts, events, tool audit, context metadata, and outcome files. Findings can be promoted into memory candidates, prompt mutation proposals, deterministic regression tasks, or human review queues.",
            "",
        ]
    )
    target = run.root / "events" / "evaluation_report.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _write_human_review_queue(
    *,
    run: EvaluationRun,
    suite: EvaluationSuite,
    reports: tuple[EvaluationReport, ...],
) -> Path:
    target = run.root / "events" / "evaluation_human_review_queue.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    for report in reports:
        should_review = report.requires_human_review or _has_high_risk_finding(report)
        if not should_review:
            continue
        review_reason = (
            "requires_human_review"
            if report.requires_human_review
            else "deterministic_high_risk"
        )
        item = {
            "schema": "evaluation_human_review_item.v1",
            "run_id": run.run_id,
            "project": run.project,
            "suite": suite.id,
            "created": datetime.now(tz=timezone.utc).isoformat(),
            "source_evaluator": report.evaluator,
            "evaluator_version": report.evaluator_version,
            "grader_type": report.grader_type,
            "advisory": report.advisory,
            "calibration_role": report.calibration_role,
            "review_reason": review_reason,
            "target_ref": report.target_ref,
            "decision": report.decision,
            "overall_score": report.overall_score,
            "findings": [finding.to_metadata() for finding in report.findings],
            "status": "pending_review",
            "human_decision": None,
            "human_notes": "",
            "suggested_questions": _human_review_questions(report),
        }
        rows.append(json.dumps(item, ensure_ascii=False))
    target.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return target


def _write_self_evolution_candidates(
    *,
    run: EvaluationRun,
    suite: EvaluationSuite,
    reports: tuple[EvaluationReport, ...],
) -> Path:
    candidates: list[dict[str, Any]] = []
    for report in reports:
        for finding in report.findings:
            if finding.severity not in {"medium", "high", "blocker"}:
                continue
            candidates.append(
                {
                    "schema": "evaluation_self_evolution_candidate.v1",
                    "run_id": run.run_id,
                    "project": run.project,
                    "suite": suite.id,
                    "created": datetime.now(tz=timezone.utc).isoformat(),
                    "source_evaluator": report.evaluator,
                    "finding_id": finding.id,
                    "severity": finding.severity,
                    "category": finding.category,
                    "message": finding.message,
                    "evidence_refs": list(finding.evidence_refs),
                    "suggested_lever": _suggested_lever(finding.category),
                    "status": "pending_review",
                }
            )
    target = run.root / "events" / "evaluation_self_evolution_candidates.json"
    target.write_text(json.dumps(candidates, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def _has_high_risk_finding(report: EvaluationReport) -> bool:
    if report.decision in {"revise", "block", "fail"}:
        return True
    return any(finding.severity in {"high", "blocker"} for finding in report.findings)


def _human_review_questions(report: EvaluationReport) -> list[str]:
    if report.grader_type == "llm":
        return [
            "Does the rubric score match the evidence in the approved artifacts?",
            "Should any subjective finding be converted into a deterministic regression?",
        ]
    if report.grader_type == "human":
        return [
            "Should this run be accepted as a gold label for evaluator calibration?",
            "Is the reviewer decision pass, warn, revise, block, or fail?",
        ]
    return [
        "Is the deterministic finding a real system issue or a fixture/configuration issue?",
        "Should this finding become a regression task or a self-evolution candidate?",
    ]


def _suggested_lever(category: str) -> str:
    if category in {"context", "trajectory", "tool_audit"}:
        return "harness_or_observability_regression"
    if category in {"claim_support", "report"}:
        return "writing_prompt_or_rubric_mutation"
    if category == "outcome":
        return "task_fixture_or_agent_feedback"
    if category == "run_integrity":
        return "run_store_contract_regression"
    return "human_review"


def _top_findings(reports: tuple[EvaluationReport, ...]) -> list[Any]:
    rank = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings = [finding for report in reports for finding in report.findings]
    findings.sort(key=lambda finding: rank.get(finding.severity, 99))
    return findings[:12]
