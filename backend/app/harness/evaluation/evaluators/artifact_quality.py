"""Deterministic artifact quality evaluator."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.harness.evaluation.models import (
    EvaluationDecision,
    EvaluationFinding,
    EvaluationReport,
    EvaluationSeverity,
    EvaluationTarget,
)
from app.harness.evaluation.rubrics import (
    ArtifactRubric,
    RubricNotFoundError,
    load_rubric,
    raw_list,
    raw_mapping,
    weighted_score,
)
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import validate_document

ScoreFn = Callable[[dict[str, Any], str], dict[str, float]]

_PROPOSAL_EVIDENCE_WARNING_IDS: frozenset[str] = frozenset(
    {
        "missing_evidence_refs",
        "evidence_refs_not_in_research_index",
        "literature_relevance_low",
        "related_literature_placeholder",
        "source_summaries_missing",
    }
)


class ArtifactQualityEvaluator:
    id = "artifact_quality.rubric"
    version = 1

    def evaluate(self, target: EvaluationTarget) -> EvaluationReport:
        validation = validate_document(target.text, expected_schema=target.expected_schema)
        try:
            parsed = parse_frontmatter(target.text)
        except Exception:
            return self._blocked_invalid_schema(
                target=target,
                schema_id=target.expected_schema or target.target_schema or "",
            )
        metadata = parsed.metadata
        schema_id = str(
            target.expected_schema or target.target_schema or metadata.get("schema") or ""
        )
        if not validation.valid:
            return self._blocked_invalid_schema(target=target, schema_id=schema_id)

        try:
            rubric = load_rubric(schema_id)
        except RubricNotFoundError:
            return EvaluationReport(
                project=target.project,
                scope=target.scope,
                target_ref=target.target_ref,
                target_schema=schema_id,
                evaluator=self.id,
                evaluator_version=self.version,
                decision="warn",
                blocking=False,
                overall_score=0.0,
                scores={},
                findings=(
                    EvaluationFinding(
                        id="rubric_missing",
                        severity="medium",
                        category="rubric",
                        message=f"No artifact quality rubric configured for `{schema_id}`.",
                        evidence_refs=(target.target_ref,),
                    ),
                ),
                recommended_actions=("Add a structured rubric before using this schema in benchmark scoring.",),
            )

        scores = _score_for_schema(schema_id, metadata, parsed.body)
        for dimension in rubric.dimensions:
            scores.setdefault(dimension.id, 0.0)
        overall = weighted_score(scores, rubric)
        findings = _quality_findings(
            scores=scores,
            rubric=rubric,
            target_ref=target.target_ref,
        )
        decision = _decision(overall=overall, findings=findings, rubric=rubric)
        return EvaluationReport(
            project=target.project,
            scope=target.scope,
            target_ref=target.target_ref,
            target_schema=schema_id,
            evaluator=self.id,
            evaluator_version=self.version,
            decision=decision,
            blocking=False,
            overall_score=overall,
            scores=scores,
            findings=tuple(findings),
            recommended_actions=_recommended_actions(findings),
        )

    def _blocked_invalid_schema(
        self,
        *,
        target: EvaluationTarget,
        schema_id: str,
    ) -> EvaluationReport:
        return EvaluationReport(
            project=target.project,
            scope=target.scope,
            target_ref=target.target_ref,
            target_schema=schema_id or target.expected_schema,
            evaluator=self.id,
            evaluator_version=self.version,
            decision="block",
            blocking=True,
            overall_score=0.0,
            scores={"artifact_quality": 0.0},
            findings=(
                EvaluationFinding(
                    id="quality_schema_invalid",
                    severity="blocker",
                    category="schema",
                    message="Artifact quality cannot be evaluated until schema validation passes.",
                    evidence_refs=(target.target_ref,),
                ),
            ),
            recommended_actions=("Repair schema errors before applying quality rubrics.",),
        )


def _score_for_schema(schema_id: str, metadata: dict[str, Any], body: str) -> dict[str, float]:
    scorer = _SCORERS.get(schema_id)
    if scorer is None:
        return {}
    return scorer(metadata, body)


def _score_proposal(metadata: dict[str, Any], _body: str) -> dict[str, float]:
    constraints_text = " ".join(str(x).lower() for x in raw_list(metadata.get("constraints")))
    risks_text = " ".join(str(x).lower() for x in raw_list(metadata.get("risk_register")))
    quality_warnings = {
        str(item).strip()
        for item in raw_list(metadata.get("quality_warnings"))
        if str(item).strip()
    }
    evidence_score = _score_bool(
        bool(raw_list(metadata.get("evidence_refs")) or raw_list(metadata.get("related_literature"))),
        fallback=0.55 if metadata.get("theoretical_basis") else 0.25,
    )
    if quality_warnings & _PROPOSAL_EVIDENCE_WARNING_IDS:
        evidence_score = min(evidence_score, 0.35)
    return {
        "testability": _score_bool(
            bool(raw_list(metadata.get("testable_predictions"))),
            fallback=0.7 if _mentions_metric(metadata.get("hypothesis")) else 0.4,
        ),
        "evidence": evidence_score,
        "downstream_readiness": _score_bool(
            bool(metadata.get("experiment_hint") or raw_list(metadata.get("downstream_requirements"))),
            fallback=0.45,
        ),
        "baseline_safety": 1.0
        if "baseline" in constraints_text or "baseline" in risks_text
        else 0.65,
        "novelty": min(1.0, max(0.45, len(str(metadata.get("novelty", ""))) / 90.0)),
    }


def _score_experiment_plan(metadata: dict[str, Any], _body: str) -> dict[str, float]:
    variables = raw_mapping(metadata.get("variables"))
    metrics = raw_mapping(metadata.get("metrics"))
    ablations = raw_list(metadata.get("ablations"))
    baseline = raw_mapping(metadata.get("baseline_ref"))
    estimated_runs = metadata.get("estimated_runs")
    variable_score = 1.0 if variables.get("independent") and variables.get("dependent") else 0.35
    metric_score = 1.0 if metrics.get("primary") and metrics.get("secondary") else 0.75
    ablation_score = 1.0 if len(ablations) >= 2 else 0.65
    budget_score = 0.7
    if isinstance(estimated_runs, int):
        budget_score = 1.0 if estimated_runs >= len(ablations) else 0.45
    baseline_score = 1.0 if baseline.get("reuse_decision") else 0.45
    return {
        "variable_clarity": variable_score,
        "metric_validity": metric_score,
        "ablation_coverage": ablation_score,
        "budget_realism": budget_score,
        "baseline_reuse": baseline_score,
    }


def _score_code_spec(metadata: dict[str, Any], _body: str) -> dict[str, float]:
    files = raw_list(metadata.get("files_changed"))
    baseline = raw_mapping(metadata.get("baseline_compat"))
    coverage = raw_mapping(metadata.get("test_coverage"))
    dependencies = raw_list(metadata.get("new_dependencies"))
    risks = [
        str(item.get("risk", ""))
        for item in files
        if isinstance(item, dict)
    ]
    patch_score = 1.0 if len(files) <= 3 else 0.75 if len(files) <= 5 else 0.45
    test_score = 0.4
    if coverage.get("baseline_smoke_test") == "pass" or int(coverage.get("unit_tests_added", 0) or 0) > 0:
        test_score = 1.0
    elif coverage.get("baseline_smoke_test") == "skipped":
        test_score = 0.6
    risk_score = 1.0 if risks and all(risk in {"low", "medium"} for risk in risks) else 0.55
    if "high" in risks:
        risk_score = 0.45
    return {
        "patch_minimality": patch_score,
        "test_adequacy": test_score,
        "baseline_preservation": 1.0 if baseline.get("preserved") is True else 0.2,
        "risk_clarity": risk_score,
        "dependency_discipline": 1.0 if not dependencies else 0.75 if len(dependencies) <= 2 else 0.45,
    }


def _score_run_log(metadata: dict[str, Any], _body: str) -> dict[str, float]:
    metrics = raw_mapping(metadata.get("metrics"))
    status = str(metadata.get("status", ""))
    return {
        "metric_completeness": 1.0 if len(metrics) >= 2 else 0.7,
        "reproducibility": 1.0 if metadata.get("run_id") and metadata.get("fingerprint_hash") else 0.3,
        "failure_isolation": 1.0 if status == "completed" else 0.65 if status == "failed" else 0.5,
        "resource_trace": 1.0
        if metadata.get("duration_seconds") is not None or metadata.get("gpu_used") or metadata.get("is_mock") is not None
        else 0.55,
        "mock_real_parity": 1.0 if metadata.get("is_mock") is not None else 0.7,
    }


def _score_report(metadata: dict[str, Any], body: str) -> dict[str, float]:
    chain = raw_mapping(metadata.get("chain_refs"))
    chain_count = sum(1 for key in ("proposal", "plan", "code", "runs") if chain.get(key))
    lower_body = body.lower()
    return {
        "claim_support": 1.0 if chain_count >= 3 else 0.65 if chain_count >= 2 else 0.35,
        "metric_accuracy": 1.0 if _mentions_metric(body) or chain.get("runs") else 0.45,
        "limitation_honesty": 1.0
        if any(token in lower_body for token in ("limitation", "risk", "caveat", "failure", "constraint"))
        else 0.45,
        "chain_coverage": min(1.0, chain_count / 4.0),
        "audience_fit": 1.0 if metadata.get("target_audience") and metadata.get("deliverable_type") else 0.4,
    }


def _score_diagnosis(metadata: dict[str, Any], _body: str) -> dict[str, float]:
    causes = raw_list(metadata.get("suspected_causes"))
    metrics = raw_list(metadata.get("failed_metrics"))
    evidence = raw_list(metadata.get("evidence_refs"))
    passed = bool(metadata.get("passed"))
    target = str(metadata.get("recommended_target", ""))
    return {
        "root_cause_evidence": 1.0 if evidence and (causes or metrics or passed) else 0.45,
        "target_selection": 1.0 if (passed and target == "writing") or (not passed and target != "none") else 0.5,
        "budget_handling": 1.0 if metadata.get("attempt") and metadata.get("budget_status") else 0.4,
        "action_specificity": min(1.0, max(0.35, len(str(metadata.get("recommended_action", ""))) / 80.0)),
        "confidence": 1.0 if metadata.get("confidence") is not None or evidence else 0.55,
    }


def _quality_findings(
    *,
    scores: dict[str, float],
    rubric: ArtifactRubric,
    target_ref: str,
) -> list[EvaluationFinding]:
    findings: list[EvaluationFinding] = []
    for dimension in rubric.dimensions:
        score = scores.get(dimension.id, 0.0)
        if score >= rubric.warn_threshold:
            continue
        severity: EvaluationSeverity = "high" if score < 0.4 else "medium"
        findings.append(
            EvaluationFinding(
                id=f"quality_{dimension.id}",
                severity=severity,
                category=dimension.id,
                message=f"{dimension.label} scored {score:.2f}; expected at least {rubric.warn_threshold:.2f}.",
                evidence_refs=(f"{target_ref}#frontmatter",),
            )
        )
    return findings


def _decision(
    *,
    overall: float,
    findings: list[EvaluationFinding],
    rubric: ArtifactRubric,
) -> EvaluationDecision:
    if any(finding.severity == "high" for finding in findings):
        return "revise"
    if overall >= rubric.pass_threshold:
        return "pass"
    if overall >= rubric.warn_threshold:
        return "warn"
    return "revise"


def _recommended_actions(findings: list[EvaluationFinding]) -> tuple[str, ...]:
    return tuple(
        f"Improve `{finding.category}` and add concrete evidence in the target artifact."
        for finding in findings
    )


def _score_bool(value: bool, *, fallback: float) -> float:
    return 1.0 if value else fallback


def _mentions_metric(value: Any) -> bool:
    text = str(value).lower()
    return any(token in text for token in ("res", "pim", "ape", "loss", "metric", "db", "%", "threshold"))


_SCORERS: dict[str, ScoreFn] = {
    "proposal.v1": _score_proposal,
    "experiment_plan.v1": _score_experiment_plan,
    "code_spec.v1": _score_code_spec,
    "run_log.v1": _score_run_log,
    "report.v1": _score_report,
    "diagnosis.v1": _score_diagnosis,
}
