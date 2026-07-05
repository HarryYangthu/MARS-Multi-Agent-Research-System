"""Run-level evaluators for replaying a completed MARS run.

These evaluators only read ``runs/<run_id>/``. They do not import bridge or
agent modules, so the evaluation harness can stay inside ``harness/`` while
remaining agent-agnostic.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from app.harness.evaluation.models import (
    EvaluationDecision,
    EvaluationFinding,
    EvaluationReport,
    EvaluationSeverity,
)
from app.harness.evaluation.run_types import EvaluationRun
from app.harness.evaluation.suites import EvaluationSuite
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter


class RunEvaluator(Protocol):
    id: str
    version: int

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        """Evaluate a run and return a normalized report."""


@dataclass(frozen=True)
class RunIntegrityEvaluator:
    id: str = "run_integrity.required_outcome"
    version: int = 1

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        findings: list[EvaluationFinding] = []
        scores: dict[str, float] = {}

        missing_dirs = [
            name for name in suite.expected.required_dirs if not (run.root / name).is_dir()
        ]
        scores["required_dirs"] = _ratio(
            len(suite.expected.required_dirs) - len(missing_dirs),
            len(suite.expected.required_dirs),
        )
        for name in missing_dirs:
            findings.append(
                _finding(
                    "run_missing_dir",
                    "blocker",
                    "run_integrity",
                    f"Required run directory is missing: `{name}`.",
                    run.run_id,
                )
            )

        run_meta_exists = (run.root / "run_meta.json").is_file()
        scores["run_meta"] = 1.0 if run_meta_exists else 0.0
        if not run_meta_exists:
            findings.append(
                _finding(
                    "run_meta_missing",
                    "blocker",
                    "run_integrity",
                    "`run_meta.json` is missing.",
                    run.run_id,
                )
            )

        missing_artifacts = [
            rel
            for rel in suite.expected.required_artifacts
            if not (run.root / rel).is_file()
        ]
        scores["required_artifacts"] = _ratio(
            len(suite.expected.required_artifacts) - len(missing_artifacts),
            len(suite.expected.required_artifacts),
        )
        for rel in missing_artifacts:
            findings.append(
                _finding(
                    "required_artifact_missing",
                    "high",
                    "outcome",
                    f"Required outcome artifact is missing: `{rel}`.",
                    rel,
                )
            )

        missing_events = [
            rel
            for rel in suite.expected.required_event_files
            if not (run.root / rel).is_file()
        ]
        scores["required_event_files"] = _ratio(
            len(suite.expected.required_event_files) - len(missing_events),
            len(suite.expected.required_event_files),
        )
        for rel in missing_events:
            findings.append(
                _finding(
                    "required_event_file_missing",
                    "medium",
                    "trajectory",
                    f"Required event file is missing: `{rel}`.",
                    rel,
                )
            )

        decision: EvaluationDecision = "pass"
        blocking = False
        if any(f.severity == "blocker" for f in findings):
            decision = "block"
            blocking = True
        elif any(f.severity == "high" for f in findings):
            decision = "revise"
        elif findings:
            decision = "warn"
        return _report(
            run=run,
            evaluator=self.id,
            version=self.version,
            decision=decision,
            blocking=blocking,
            scores=scores,
            findings=findings,
            actions=(
                "Restore the missing run directories or required outcome artifacts, then replay evaluation.",
            )
            if findings
            else (),
        )


@dataclass(frozen=True)
class TrajectoryAuditEvaluator:
    id: str = "trajectory.audit_coverage"
    version: int = 1

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        findings: list[EvaluationFinding] = []
        scores: dict[str, float] = {}

        context_refs = _context_refs(run.root)
        scores["context_manifest_coverage"] = 1.0 if context_refs else 0.0
        if suite.expected.require_context_manifest and not context_refs:
            findings.append(
                _finding(
                    "context_manifest_missing",
                    "high",
                    "context",
                    "No context manifest or context pack was found for this run.",
                    "context/",
                )
            )

        tool_calls = _read_jsonl(run.root / "events" / "tool_calls.jsonl")
        tool_events = _read_jsonl(run.root / "events" / "tool_events.jsonl")
        tool_call_count = len(tool_calls)
        tool_event_count = len(tool_events)
        scores["tool_audit_presence"] = 1.0 if tool_call_count or tool_event_count else 0.0

        if suite.expected.require_tool_audit and tool_call_count == 0 and tool_event_count == 0:
            findings.append(
                _finding(
                    "tool_audit_missing",
                    "medium",
                    "tool_audit",
                    "No tool audit files were found. This is acceptable only for runs that truly used no tools.",
                    "events/tool_calls.jsonl",
                )
            )

        completed_by_call_id = {
            str(event.get("call_id"))
            for event in tool_events
            if event.get("event") == "tool.completed" and event.get("call_id")
        }
        started_by_call_id = {
            str(event.get("call_id"))
            for event in tool_events
            if event.get("event") == "tool.started" and event.get("call_id")
        }
        if started_by_call_id:
            scores["tool_event_completion"] = _ratio(
                len(started_by_call_id & completed_by_call_id),
                len(started_by_call_id),
            )
        else:
            scores["tool_event_completion"] = 1.0 if tool_call_count == 0 else 0.5
        if started_by_call_id - completed_by_call_id:
            findings.append(
                _finding(
                    "tool_event_incomplete",
                    "high",
                    "tool_audit",
                    "Some tool calls have a started event but no completed event.",
                    "events/tool_events.jsonl",
                )
            )

        agent_events = _read_jsonl(run.root / "events" / "agent_events.jsonl")
        state_counts = Counter(
            str(event.get("to_state"))
            for event in agent_events
            if event.get("to_state")
        )
        scores["agent_state_trace"] = 1.0 if agent_events else 0.0
        if not agent_events:
            findings.append(
                _finding(
                    "agent_state_trace_missing",
                    "low",
                    "trajectory",
                    "No agent state transition trace was found.",
                    "events/agent_events.jsonl",
                )
            )

        scores["terminal_state_observed"] = (
            1.0 if state_counts.get("done", 0) or state_counts.get("approved", 0) else 0.0
        )
        if agent_events and scores["terminal_state_observed"] == 0.0:
            findings.append(
                _finding(
                    "terminal_state_missing",
                    "medium",
                    "trajectory",
                    "Agent events exist but no approved/done terminal state was observed.",
                    "events/agent_events.jsonl",
                )
            )

        return _report(
            run=run,
            evaluator=self.id,
            version=self.version,
            decision=_soft_decision(findings),
            blocking=False,
            scores=scores,
            findings=findings,
            actions=(
                "Ensure every agent call writes context metadata, tool audit events, and terminal state events.",
            )
            if findings
            else (),
        )


@dataclass(frozen=True)
class OutcomeEvaluator:
    id: str = "outcome.execution_and_report"
    version: int = 1

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        findings: list[EvaluationFinding] = []
        scores: dict[str, float] = {}

        metrics_path = run.root / "execution" / "metrics.json"
        metrics = _read_json(metrics_path)
        metrics_required = suite.expected.require_execution_metrics or any(
            rel.startswith("execution/") for rel in suite.expected.required_artifacts
        )
        scores["execution_metrics"] = 1.0 if isinstance(metrics, dict) else 0.0
        if metrics_required and not isinstance(metrics, dict):
            findings.append(
                _finding(
                    "execution_metrics_missing",
                    "high",
                    "outcome",
                    "`execution/metrics.json` is required but missing or invalid.",
                    "execution/metrics.json",
                )
            )

        batch_summary = _read_json(run.root / "execution" / "batch_summary.json")
        scores["batch_summary"] = 1.0 if isinstance(batch_summary, dict) else 0.0

        report_path = run.root / "writing" / "research_report.approved.md"
        if not report_path.exists():
            report_path = run.root / "writing" / "report.approved.md"
        report_chain_score = 1.0
        missing_chain_refs: list[str] = []
        if report_path.exists():
            try:
                parsed = parse_frontmatter(report_path.read_text(encoding="utf-8"))
                chain_refs = parsed.metadata.get("chain_refs")
                if isinstance(chain_refs, dict):
                    refs = [
                        str(value)
                        for value in chain_refs.values()
                        if isinstance(value, str) and value.strip()
                    ]
                    missing_chain_refs = [
                        ref for ref in refs if not (run.root / ref).exists()
                    ]
                    report_chain_score = _ratio(len(refs) - len(missing_chain_refs), len(refs))
                else:
                    report_chain_score = 0.0
            except Exception:
                report_chain_score = 0.0
                findings.append(
                    _finding(
                        "report_frontmatter_unreadable",
                        "medium",
                        "report",
                        "The approved report frontmatter could not be parsed.",
                        _rel(run.root, report_path),
                    )
                )
        elif suite.expected.require_report_chain_refs:
            report_chain_score = 0.0
            findings.append(
                _finding(
                    "approved_report_missing",
                    "high",
                    "report",
                    "An approved writing report is required but missing.",
                    "writing/research_report.approved.md",
                )
            )
        scores["report_chain_refs"] = report_chain_score

        for ref in missing_chain_refs:
            findings.append(
                _finding(
                    "report_chain_ref_missing",
                    "high",
                    "claim_support",
                    f"Report chain reference points to a missing file: `{ref}`.",
                    ref,
                )
            )

        return _report(
            run=run,
            evaluator=self.id,
            version=self.version,
            decision=_soft_decision(findings),
            blocking=False,
            scores=scores,
            findings=findings,
            actions=(
                "Regenerate or edit the report so every claim chain points to existing approved artifacts and metrics.",
            )
            if findings
            else (),
        )


@dataclass(frozen=True)
class GateBehaviorEvaluator:
    id: str = "gate_behavior.expected"
    version: int = 1

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        tool_calls = _read_jsonl(run.root / "events" / "tool_calls.jsonl")
        tool_events = _read_jsonl(run.root / "events" / "tool_events.jsonl")
        all_events = tool_calls + tool_events
        observed_gates = {
            str(value)
            for event in all_events
            for key in ("blocked_by_gate", "gate", "gate_id")
            if (value := event.get(key))
        }
        expected = set(suite.expected.expected_gates)
        findings: list[EvaluationFinding] = []
        missing = expected - observed_gates
        for gate_id in sorted(missing):
            findings.append(
                _finding(
                    "expected_gate_missing",
                    "high",
                    "gate_behavior",
                    f"Expected gate `{gate_id}` was not observed in tool audit events.",
                    "events/tool_calls.jsonl",
                )
            )

        blocked = [
            event
            for event in all_events
            if event.get("status") == "blocked" or event.get("blocked_by_gate")
        ]
        approvals = [
            event
            for event in all_events
            if event.get("requires_approval") is True
        ]
        scores = {
            "expected_gate_coverage": _ratio(len(expected) - len(missing), len(expected)),
            "gate_audit_presence": 1.0 if observed_gates or not expected else 0.0,
            "blocked_tool_visibility": 1.0 if blocked or not expected else 0.7,
            "approval_visibility": 1.0 if approvals or not expected else 0.7,
        }
        return _report(
            run=run,
            evaluator=self.id,
            version=self.version,
            decision=_soft_decision(findings),
            blocking=False,
            scores=scores,
            findings=findings,
            actions=("Add explicit gate events or adjust the suite expected_gates fixture.",)
            if findings
            else (),
        )


@dataclass(frozen=True)
class MultiAgentCollaborationEvaluator:
    id: str = "multi_agent.collaboration_quality"
    version: int = 1

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        findings: list[EvaluationFinding] = []
        scores: dict[str, float] = {}

        state = _read_json(run.root / "run_state.json")
        states = state.get("states") if isinstance(state, dict) else None
        if isinstance(states, dict):
            observed_stages = {str(key) for key in states}
        else:
            observed_stages = {
                str(event.get("agent"))
                for event in _read_jsonl(run.root / "events" / "agent_events.jsonl")
                if event.get("agent")
            }
        expected_stages = set(suite.expected.expected_stages)
        if not expected_stages:
            expected_stages = {
                stage
                for stage in ("idea", "experiment", "coding", "execution", "writing")
                if (run.root / stage).exists()
            }
        missing_stages = expected_stages - observed_stages
        scores["routing_stage_coverage"] = _ratio(
            len(expected_stages) - len(missing_stages),
            len(expected_stages),
        )
        for stage in sorted(missing_stages):
            findings.append(
                _finding(
                    "expected_stage_missing",
                    "medium",
                    "routing",
                    f"Expected stage `{stage}` was not observed in run state or agent events.",
                    "run_state.json",
                )
            )

        entrypoint = suite.expected.expected_entrypoint or run.entrypoint
        scores["entrypoint_match"] = 1.0 if run.entrypoint == entrypoint else 0.0
        if run.entrypoint != entrypoint:
            findings.append(
                _finding(
                    "entrypoint_mismatch",
                    "high",
                    "routing",
                    f"Run entrypoint `{run.entrypoint}` does not match expected `{entrypoint}`.",
                    "run_meta.json",
                )
            )

        context_packs = list((run.root / "context").glob("*context_pack*.json"))
        handoff_mentions = 0
        for path in context_packs:
            raw = _read_json(path)
            if isinstance(raw, dict):
                text = json.dumps(raw, ensure_ascii=False)
                if "upstream_handoff" in text or "upstream" in text:
                    handoff_mentions += 1
        approved_artifacts = list(run.root.glob("*/*.approved.md"))
        if len(approved_artifacts) > 1:
            scores["handoff_trace"] = 1.0 if handoff_mentions else 0.4
            if not handoff_mentions:
                findings.append(
                    _finding(
                        "handoff_trace_weak",
                        "medium",
                        "handoff",
                        "Multiple approved artifacts exist, but context packs do not expose upstream handoff metadata.",
                        "context/",
                    )
                )
        else:
            scores["handoff_trace"] = 1.0

        tool_calls = _read_jsonl(run.root / "events" / "tool_calls.jsonl")
        tool_names = [str(event.get("tool")) for event in tool_calls if event.get("tool")]
        duplicate_tool_ratio = _duplicate_ratio(tool_names)
        scores["collaboration_efficiency"] = round(1.0 - duplicate_tool_ratio, 6)
        if duplicate_tool_ratio > 0.5 and len(tool_names) >= 6:
            findings.append(
                _finding(
                    "duplicate_tool_use_high",
                    "medium",
                    "collaboration_efficiency",
                    "Tool usage shows high repetition; inspect for duplicated search/read/compute work across agents.",
                    "events/tool_calls.jsonl",
                )
            )

        diagnosis = run.root / "diagnosis" / "diagnosis.v1.md"
        report = run.root / "writing" / "research_report.approved.md"
        report_text = report.read_text(encoding="utf-8") if report.exists() else ""
        if diagnosis.exists():
            has_limitation = any(
                token in report_text.lower()
                for token in (
                    "limitation",
                    "risk",
                    "failure",
                    "constraint",
                    "不确定",
                    "限制",
                    "风险",
                )
            )
            scores["conflict_or_failure_acknowledgement"] = (
                1.0 if has_limitation or not report.exists() else 0.5
            )
            if report.exists() and not has_limitation:
                findings.append(
                    _finding(
                        "diagnosis_not_acknowledged",
                        "medium",
                        "conflict_resolution",
                        "A diagnosis artifact exists, but the final report does not clearly acknowledge limitations or failure risk.",
                        "diagnosis/diagnosis.v1.md",
                    )
                )
        else:
            scores["conflict_or_failure_acknowledgement"] = 1.0

        return _report(
            run=run,
            evaluator=self.id,
            version=self.version,
            decision=_soft_decision(findings),
            blocking=False,
            scores=scores,
            findings=findings,
            actions=("Review routing, handoff metadata, duplicated tool work, and report limitation handling.",)
            if findings
            else (),
        )


@dataclass(frozen=True)
class LLMRubricEvaluator:
    """Model-based grader with deterministic offline fallback.

    The report is advisory by design. It evaluates subjective quality dimensions
    that deterministic graders cannot safely block on, and it exposes an
    insufficient-info escape hatch for calibration.
    """

    id: str = "llm_rubric.advisory"
    version: int = 1

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        report_text = _approved_report_text(run.root)
        artifact_text = _approved_artifact_text(run.root)
        evidence_text = "\n\n".join(part for part in (report_text, artifact_text) if part)
        findings: list[EvaluationFinding] = []
        scores: dict[str, float] = {}

        if not evidence_text.strip():
            scores["escape_hatch"] = 1.0
            findings.append(
                _finding(
                    "llm_rubric_insufficient_info",
                    "info",
                    "llm_rubric",
                    "INSUFFICIENT_INFO: no approved report or approved artifacts were available for subjective quality review.",
                    run.run_id,
                )
            )
            decision: EvaluationDecision = "pass"
            overall: float | None = None
        else:
            scores = {
                "task_completion_clarity": _keyword_score(
                    evidence_text,
                    ("result", "conclusion", "完成", "结论", "metrics", "RES"),
                ),
                "evidence_grounding": _keyword_score(
                    evidence_text,
                    ("evidence", "chain_refs", "metrics", "artifact", "证据", "引用"),
                ),
                "limitation_awareness": _keyword_score(
                    evidence_text,
                    ("risk", "limitation", "warning", "限制", "风险", "failure"),
                ),
            }
            overall = round(sum(scores.values()) / len(scores), 6)
            decision = "pass" if overall >= 0.7 else "warn"
            if scores["evidence_grounding"] < 0.5:
                findings.append(
                    _finding(
                        "llm_rubric_weak_evidence_grounding",
                        "medium",
                        "llm_rubric",
                        "Model-based rubric found weak evidence grounding. This is advisory and should be calibrated with human review.",
                        "writing/",
                    )
                )
            if scores["limitation_awareness"] < 0.5:
                findings.append(
                    _finding(
                        "llm_rubric_missing_limitations",
                        "low",
                        "llm_rubric",
                        "Model-based rubric found limited discussion of risks or limitations.",
                        "writing/",
                    )
                )
        return EvaluationReport(
            project=run.project,
            scope="run",
            target_ref=run.run_id,
            target_schema=None,
            evaluator=self.id,
            evaluator_version=self.version,
            grader_type="llm",
            advisory=True,
            requires_human_review=True,
            calibration_role="model_judge",
            decision=decision,
            blocking=False,
            overall_score=overall,
            scores=scores,
            findings=tuple(findings),
            recommended_actions=(
                "Sample this LLM rubric report for human calibration when evaluator-human agreement drops below 0.85.",
            ),
        )


@dataclass(frozen=True)
class HumanReviewQueueEvaluator:
    """Human grader layer.

    Human labels are the gold standard. This evaluator creates a normalized
    report even when labels are absent so replay reports always expose the
    human-review lane.
    """

    id: str = "human_review.queue"
    version: int = 1

    def evaluate_run(self, *, run: EvaluationRun, suite: EvaluationSuite) -> EvaluationReport:
        labels = _read_jsonl(run.root / "events" / "human_review_labels.jsonl")
        findings: list[EvaluationFinding] = []
        scores: dict[str, float] = {}
        if labels:
            decisions = [str(item.get("human_decision", "")) for item in labels]
            accepted = sum(1 for decision in decisions if decision in {"pass", "warn"})
            scores["human_acceptance_rate"] = _ratio(accepted, len(decisions))
            decision: EvaluationDecision = (
                "pass" if scores["human_acceptance_rate"] >= 0.8 else "warn"
            )
            findings.append(
                _finding(
                    "human_review_labels_present",
                    "info",
                    "human_review",
                    f"Human labels were found for {len(labels)} sample(s).",
                    "events/human_review_labels.jsonl",
                )
            )
        else:
            decision = "pass"
            findings.append(
                _finding(
                    "human_review_queued",
                    "info",
                    "human_review",
                    "No human labels are present. A review queue item will be exported for calibration and high-risk inspection.",
                    "events/evaluation_human_review_queue.jsonl",
                )
            )
        return EvaluationReport(
            project=run.project,
            scope="run",
            target_ref=run.run_id,
            target_schema=None,
            evaluator=self.id,
            evaluator_version=self.version,
            grader_type="human",
            advisory=True,
            requires_human_review=not labels,
            calibration_role="gold_standard",
            decision=decision,
            blocking=False,
            overall_score=scores.get("human_acceptance_rate"),
            scores=scores,
            findings=tuple(findings),
            recommended_actions=(
                "Review queued samples, write human_decision labels, then run calibration drift reporting.",
            ),
        )


def default_run_evaluators() -> tuple[RunEvaluator, ...]:
    return (
        cast(RunEvaluator, RunIntegrityEvaluator()),
        cast(RunEvaluator, TrajectoryAuditEvaluator()),
        cast(RunEvaluator, OutcomeEvaluator()),
        cast(RunEvaluator, GateBehaviorEvaluator()),
        cast(RunEvaluator, MultiAgentCollaborationEvaluator()),
        cast(RunEvaluator, LLMRubricEvaluator()),
        cast(RunEvaluator, HumanReviewQueueEvaluator()),
    )


def _report(
    *,
    run: EvaluationRun,
    evaluator: str,
    version: int,
    decision: EvaluationDecision,
    blocking: bool,
    scores: dict[str, float],
    findings: list[EvaluationFinding],
    actions: tuple[str, ...],
) -> EvaluationReport:
    numeric_scores = list(scores.values())
    overall = (
        round(sum(numeric_scores) / len(numeric_scores), 6)
        if numeric_scores
        else None
    )
    return EvaluationReport(
        project=run.project,
        scope="run",
        target_ref=run.run_id,
        target_schema=None,
        evaluator=evaluator,
        evaluator_version=version,
        decision=decision,
        blocking=blocking,
        overall_score=overall,
        scores=scores,
        findings=tuple(findings),
        recommended_actions=actions,
    )


def _finding(
    identifier: str,
    severity: EvaluationSeverity,
    category: str,
    message: str,
    evidence_ref: str,
) -> EvaluationFinding:
    return EvaluationFinding(
        id=identifier,
        severity=severity,
        category=category,
        message=message,
        evidence_refs=(evidence_ref,),
    )


def _soft_decision(findings: list[EvaluationFinding]) -> EvaluationDecision:
    if any(f.severity == "blocker" for f in findings):
        return "block"
    if any(f.severity == "high" for f in findings):
        return "revise"
    if findings:
        return "warn"
    return "pass"


def _ratio(done: int, total: int) -> float:
    if total <= 0:
        return 1.0
    return round(max(0.0, min(1.0, done / total)), 6)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            items.append(raw)
    return items


def _approved_report_text(root: Path) -> str:
    candidates = [
        root / "writing" / "research_report.approved.md",
        root / "writing" / "report.approved.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _approved_artifact_text(root: Path) -> str:
    parts: list[str] = []
    for path in sorted(root.glob("*/*.approved.md")):
        parent_name = path.parent.name
        if parent_name in {"hitl", "events"}:
            continue
        rel = path.relative_to(root).as_posix()
        parts.append(f"# {rel}\n{path.read_text(encoding='utf-8')[:3000]}")
    return "\n\n".join(parts)


def _keyword_score(text: str, keywords: tuple[str, ...]) -> float:
    lowered = text.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in lowered or keyword in text)
    return round(min(1.0, hits / max(1, len(keywords) // 2)), 6)


def _context_refs(run_root: Path) -> list[str]:
    context_dir = run_root / "context"
    if not context_dir.exists():
        return []
    patterns = (
        "context_manifest.v2.json",
        "agents/*/manifests/*.json",
        "*context_pack*.json",
        "trace_manifest.v2.json",
    )
    refs: list[str] = []
    for pattern in patterns:
        refs.extend(_rel(run_root, path) for path in sorted(context_dir.glob(pattern)))
    return sorted(dict.fromkeys(refs))


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _duplicate_ratio(values: list[str]) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
    return min(1.0, duplicate_count / len(values))
