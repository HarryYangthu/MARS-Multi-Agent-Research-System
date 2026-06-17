"""Replay evaluation for Commander attribution decisions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

from app.bridge.commander_agent import CommanderAgent, RunObservation
from app.bridge.diagnostics import (
    DiagnosisAnalysis,
    DiagnosticsConfig,
    MetricFailure,
    MetricRule,
    SuspectedCause,
)
from app.settings import repo_root
from app.storage.run_store import RunStore


@dataclass(frozen=True)
class CommanderEvalCase:
    id: str
    description: str
    expected_target: str
    expected_should_continue: bool
    expected_requires_human: bool
    attempt: int
    config: DiagnosticsConfig
    analysis: DiagnosisAnalysis
    attempt_history: list[dict[str, Any]]


def run_commander_attribution_eval(
    *,
    project: str = "moe-pimc",
    cases_path: Path | None = None,
) -> dict[str, Any]:
    """Run deterministic replay cases against ``CommanderAgent.diagnose_failure``."""
    cases = load_commander_eval_cases(project=project, path=cases_path)
    rows: list[dict[str, Any]] = []
    target_hits = 0
    continuation_hits = 0
    human_pause_hits = 0
    with TemporaryDirectory(prefix="mars_commander_eval_") as tmp:
        store = RunStore(Path(tmp))
        for case in cases:
            run = store.create(task=f"commander-eval-{case.id}", project=project)
            observation = RunObservation(
                run=run,
                attempt=case.attempt,
                config=case.config,
                analysis=case.analysis,
                metrics_summary={},
                curve_summary={},
                log_summary={},
                approved_artifact_refs={},
                attempt_history=case.attempt_history,
                latest_diagnosis=None,
            )
            attribution = CommanderAgent().diagnose_failure(observation)
            target_ok = attribution.target_agent == case.expected_target
            continue_ok = attribution.should_continue == case.expected_should_continue
            human_ok = attribution.requires_human == case.expected_requires_human
            target_hits += int(target_ok)
            continuation_hits += int(continue_ok)
            human_pause_hits += int(human_ok)
            rows.append(
                {
                    "id": case.id,
                    "description": case.description,
                    "expected": {
                        "target_agent": case.expected_target,
                        "should_continue": case.expected_should_continue,
                        "requires_human": case.expected_requires_human,
                    },
                    "actual": {
                        "target_agent": attribution.target_agent,
                        "should_continue": attribution.should_continue,
                        "requires_human": attribution.requires_human,
                        "confidence": attribution.confidence,
                        "reason": attribution.reason,
                    },
                    "passed": target_ok and continue_ok and human_ok,
                    "checks": {
                        "target": target_ok,
                        "should_continue": continue_ok,
                        "requires_human": human_ok,
                    },
                }
            )
    total = len(rows)
    passed = sum(1 for row in rows if bool(row["passed"]))
    return {
        "schema": "commander_attribution_eval.v1",
        "project": project,
        "case_count": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": passed / total if total else 0.0,
        "target_accuracy": target_hits / total if total else 0.0,
        "continuation_accuracy": continuation_hits / total if total else 0.0,
        "human_pause_accuracy": human_pause_hits / total if total else 0.0,
        "cases": rows,
    }


def load_commander_eval_cases(
    *,
    project: str,
    path: Path | None = None,
) -> tuple[CommanderEvalCase, ...]:
    source = path or repo_root() / "configs" / "evaluation" / "commander_attribution_cases.yaml"
    if not source.exists():
        return _default_cases(project)
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return _default_cases(project)
    raw_cases = raw.get("cases", [])
    if not isinstance(raw_cases, list):
        return _default_cases(project)
    cases = [_case_from_raw(project=project, raw=item) for item in raw_cases if isinstance(item, dict)]
    return tuple(case for case in cases if case is not None) or _default_cases(project)


def _case_from_raw(project: str, raw: dict[str, Any]) -> CommanderEvalCase | None:
    case_id = str(raw.get("id", "")).strip()
    if not case_id:
        return None
    config_raw = raw.get("config", {})
    config = _config_from_raw(project=project, raw=config_raw if isinstance(config_raw, dict) else {})
    analysis_raw = raw.get("analysis", {})
    analysis = _analysis_from_raw(analysis_raw if isinstance(analysis_raw, dict) else {})
    history_raw = raw.get("attempt_history", [])
    history = [dict(item) for item in history_raw if isinstance(item, dict)] if isinstance(history_raw, list) else []
    return CommanderEvalCase(
        id=case_id,
        description=str(raw.get("description", "")),
        expected_target=str(raw.get("expected_target", "coding")),
        expected_should_continue=bool(raw.get("expected_should_continue", True)),
        expected_requires_human=bool(raw.get("expected_requires_human", False)),
        attempt=int(raw.get("attempt", 1) or 1),
        config=config,
        analysis=analysis,
        attempt_history=history,
    )


def _config_from_raw(project: str, raw: dict[str, Any]) -> DiagnosticsConfig:
    allowed_raw = raw.get("allowed_targets", ["coding", "experiment"])
    rules_raw = raw.get("metric_rules", [])
    analyzers_raw = raw.get("analyzers", {})
    return DiagnosticsConfig(
        project=project,
        max_iterations=int(raw.get("max_iterations", 2) or 2),
        default_budget=int(raw.get("default_budget", 2) or 2),
        allowed_targets=tuple(str(item) for item in allowed_raw) if isinstance(allowed_raw, list) else ("coding", "experiment"),
        default_target=str(raw.get("default_target", "coding")),
        enable_idea_loop=bool(raw.get("enable_idea_loop", False)),
        analyzers={str(k): bool(v) for k, v in analyzers_raw.items()} if isinstance(analyzers_raw, dict) else {},
        metric_rules=tuple(
            MetricRule(
                name=str(item.get("name", "")),
                target=float(item.get("target", 0.0) or 0.0),
                direction=str(item.get("direction", "lte")),
                tolerance=float(item.get("tolerance", 0.0) or 0.0),
                aggregation=str(item.get("aggregation", "mean")),
            )
            for item in rules_raw
            if isinstance(item, dict) and item.get("name")
        ) if isinstance(rules_raw, list) else (),
    )


def _analysis_from_raw(raw: dict[str, Any]) -> DiagnosisAnalysis:
    failed_raw = raw.get("failed_metrics", [])
    causes_raw = raw.get("suspected_causes", [])
    refs_raw = raw.get("evidence_refs", [])
    failed = tuple(
        MetricFailure(
            metric=str(item.get("metric", "")),
            observed=float(item.get("observed", 0.0) or 0.0),
            target=float(item.get("target", 0.0) or 0.0),
            direction=str(item.get("direction", "lte")),
            gap=float(item.get("gap", 0.0) or 0.0),
            aggregation=str(item.get("aggregation", "mean")),
        )
        for item in failed_raw
        if isinstance(item, dict) and item.get("metric")
    ) if isinstance(failed_raw, list) else ()
    causes = tuple(
        SuspectedCause(
            kind=str(item.get("kind", "unknown")),
            summary=str(item.get("summary", "")) or "No summary.",
            severity=str(item.get("severity", "medium")),
            evidence=tuple(str(ref) for ref in item.get("evidence", []) if isinstance(ref, str))
            if isinstance(item.get("evidence", []), list)
            else (),
        )
        for item in causes_raw
        if isinstance(item, dict)
    ) if isinstance(causes_raw, list) else ()
    refs = tuple(str(ref) for ref in refs_raw) if isinstance(refs_raw, list) else ()
    return DiagnosisAnalysis(
        passed=bool(raw.get("passed", False)),
        failed_metrics=failed,
        suspected_causes=causes,
        evidence_refs=refs,
    )


def _default_cases(project: str) -> tuple[CommanderEvalCase, ...]:
    common_config = DiagnosticsConfig(
        project=project,
        max_iterations=2,
        allowed_targets=("coding", "experiment"),
        default_target="coding",
    )
    loss = MetricFailure(
        metric="loss",
        observed=0.5,
        target=0.04,
        direction="lte",
        gap=0.46,
        aggregation="max",
    )
    metrics_gap = SuspectedCause(
        kind="metrics_gap",
        summary="Configured metric missed threshold.",
        severity="high",
        evidence=("metric:loss",),
    )
    return (
        CommanderEvalCase(
            id="experiment_config_high",
            description="High config sanity signal should route to Experiment.",
            expected_target="experiment",
            expected_should_continue=True,
            expected_requires_human=False,
            attempt=1,
            config=common_config,
            analysis=DiagnosisAnalysis(
                passed=False,
                failed_metrics=(loss,),
                suspected_causes=(
                    metrics_gap,
                    SuspectedCause(
                        kind="config_sanity",
                        summary="No ablations were configured.",
                        severity="high",
                    ),
                ),
                evidence_refs=("execution/metrics.json", "experiment/experiment_plan.approved.md"),
            ),
            attempt_history=[],
        ),
        CommanderEvalCase(
            id="coding_risk_high",
            description="High code risk should route to Coding.",
            expected_target="coding",
            expected_should_continue=True,
            expected_requires_human=False,
            attempt=1,
            config=common_config,
            analysis=DiagnosisAnalysis(
                passed=False,
                failed_metrics=(loss,),
                suspected_causes=(
                    metrics_gap,
                    SuspectedCause(
                        kind="code_change_risk",
                        summary="High-risk file changed.",
                        severity="high",
                    ),
                ),
                evidence_refs=("execution/metrics.json", "coding/code_spec.approved.md"),
            ),
            attempt_history=[],
        ),
        CommanderEvalCase(
            id="metrics_gap_default",
            description="Ambiguous metric miss should use default Coding target.",
            expected_target="coding",
            expected_should_continue=True,
            expected_requires_human=False,
            attempt=1,
            config=common_config,
            analysis=DiagnosisAnalysis(
                passed=False,
                failed_metrics=(loss,),
                suspected_causes=(metrics_gap,),
                evidence_refs=("execution/metrics.json",),
            ),
            attempt_history=[],
        ),
        CommanderEvalCase(
            id="low_confidence_flip_hitl",
            description="Low confidence target flip should pause for human review.",
            expected_target="none",
            expected_should_continue=False,
            expected_requires_human=True,
            attempt=1,
            config=DiagnosticsConfig(
                project=project,
                allowed_targets=(),
                default_target="coding",
            ),
            analysis=DiagnosisAnalysis(
                passed=False,
                failed_metrics=(loss,),
                suspected_causes=(metrics_gap,),
                evidence_refs=("execution/metrics.json",),
            ),
            attempt_history=[
                {"attempt": 1, "target_agent": "experiment", "confidence": 0.4}
            ],
        ),
        CommanderEvalCase(
            id="budget_exhausted",
            description="Exhausted loop budget should stop repair and route to Writing.",
            expected_target="writing",
            expected_should_continue=False,
            expected_requires_human=False,
            attempt=2,
            config=common_config,
            analysis=DiagnosisAnalysis(
                passed=False,
                failed_metrics=(loss,),
                suspected_causes=(metrics_gap,),
                evidence_refs=("execution/metrics.json",),
            ),
            attempt_history=[],
        ),
        CommanderEvalCase(
            id="metrics_passed",
            description="Passing metrics should proceed to Writing.",
            expected_target="writing",
            expected_should_continue=False,
            expected_requires_human=False,
            attempt=1,
            config=common_config,
            analysis=DiagnosisAnalysis(
                passed=True,
                failed_metrics=(),
                suspected_causes=(),
                evidence_refs=("execution/metrics.json",),
            ),
            attempt_history=[],
        ),
    )
