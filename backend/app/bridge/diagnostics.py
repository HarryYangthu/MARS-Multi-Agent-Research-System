"""Project diagnostics config and deterministic V2 analyzers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

from app.harness.schema.frontmatter_parser import parse as parse_fm
from app.settings import repo_root
from app.storage.run_store import RunHandle


MetricDirection = str


@dataclass(frozen=True)
class MetricRule:
    name: str
    target: float
    direction: MetricDirection
    tolerance: float = 0.0
    aggregation: str = "mean"


@dataclass(frozen=True)
class DiagnosticsConfig:
    project: str
    max_iterations: int = 2
    default_budget: int = 2
    allowed_targets: tuple[str, ...] = ("coding", "experiment")
    default_target: str = "experiment"
    enable_idea_loop: bool = False
    analyzers: dict[str, bool] = field(default_factory=dict)
    metric_rules: tuple[MetricRule, ...] = ()


@dataclass(frozen=True)
class MetricFailure:
    metric: str
    observed: float
    target: float
    direction: str
    gap: float
    aggregation: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "observed": self.observed,
            "target": self.target,
            "direction": self.direction,
            "gap": self.gap,
            "aggregation": self.aggregation,
        }


@dataclass(frozen=True)
class SuspectedCause:
    kind: str
    summary: str
    severity: str = "medium"
    evidence: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "severity": self.severity,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class DiagnosisAnalysis:
    passed: bool
    failed_metrics: tuple[MetricFailure, ...]
    suspected_causes: tuple[SuspectedCause, ...]
    evidence_refs: tuple[str, ...]


def load_diagnostics_config(project: str) -> DiagnosticsConfig:
    path = repo_root() / "projects" / project / "diagnostics.yaml"
    if not path.exists():
        return DiagnosticsConfig(project=project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return DiagnosticsConfig(project=project)

    loop_raw = raw.get("loop", {})
    loop = loop_raw if isinstance(loop_raw, dict) else {}
    analyzers_raw = raw.get("diagnostic_analyzers", {})
    analyzers = (
        {str(k): bool(v) for k, v in analyzers_raw.items()}
        if isinstance(analyzers_raw, dict)
        else {}
    )
    metrics_raw = raw.get("metrics", {})
    metric_rules: list[MetricRule] = []
    if isinstance(metrics_raw, dict):
        for name, rule_raw in metrics_raw.items():
            if not isinstance(rule_raw, dict):
                continue
            target_raw = rule_raw.get("target")
            if target_raw is None:
                continue
            try:
                target = float(target_raw)
            except (TypeError, ValueError):
                continue
            metric_rules.append(
                MetricRule(
                    name=str(name),
                    target=target,
                    direction=str(rule_raw.get("direction", "gte")),
                    tolerance=float(rule_raw.get("tolerance", 0.0) or 0.0),
                    aggregation=str(rule_raw.get("aggregation", "mean")),
                )
            )

    allowed_raw = loop.get("allowed_targets", ["coding", "experiment"])
    allowed = (
        tuple(str(x) for x in allowed_raw)
        if isinstance(allowed_raw, list)
        else ("coding", "experiment")
    )
    return DiagnosticsConfig(
        project=str(raw.get("project", project)),
        max_iterations=int(loop.get("max_iterations", 2) or 2),
        default_budget=int(loop.get("default_budget", 2) or 2),
        allowed_targets=allowed,
        default_target=str(loop.get("default_target", "experiment")),
        enable_idea_loop=bool(loop.get("enable_idea_loop", False)),
        analyzers=analyzers,
        metric_rules=tuple(metric_rules),
    )


def analyze_run(run: RunHandle, config: DiagnosticsConfig) -> DiagnosisAnalysis:
    failed_metrics: list[MetricFailure] = []
    suspected_causes: list[SuspectedCause] = []
    evidence_refs: list[str] = []

    metrics_path = run.subdir("execution") / "metrics.json"
    metric_rows = _read_metrics(metrics_path)
    if metrics_path.exists():
        evidence_refs.append("execution/metrics.json")

    if config.analyzers.get("metrics_gap", True):
        failed_metrics.extend(_analyze_metric_gaps(metric_rows, config.metric_rules))
        if failed_metrics:
            suspected_causes.append(
                SuspectedCause(
                    kind="metrics_gap",
                    severity="high",
                    summary="One or more configured metrics missed the project threshold.",
                    evidence=tuple(f"metric:{f.metric}" for f in failed_metrics),
                )
            )

    if config.analyzers.get("config_sanity", True):
        cause = _analyze_config_sanity(run)
        if cause is not None:
            suspected_causes.append(cause)
            evidence_refs.append("experiment/experiment_plan.approved.md")

    if config.analyzers.get("code_change_risk", True):
        cause = _analyze_code_change_risk(run)
        if cause is not None:
            suspected_causes.append(cause)
            evidence_refs.append("coding/code_spec.approved.md")

    passed = not failed_metrics
    if not suspected_causes and not passed:
        suspected_causes.append(
            SuspectedCause(
                kind="unknown",
                severity="medium",
                summary="The run failed thresholds, but no specific config or code risk was found.",
            )
        )

    return DiagnosisAnalysis(
        passed=passed,
        failed_metrics=tuple(failed_metrics),
        suspected_causes=tuple(suspected_causes),
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
    )


def _read_metrics(path: Path) -> list[dict[str, float]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows: list[dict[str, float]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        metrics_raw = item.get("metrics", {})
        if not isinstance(metrics_raw, dict):
            continue
        metrics: dict[str, float] = {}
        for key, value in metrics_raw.items():
            try:
                metrics[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        rows.append(metrics)
    return rows


def _analyze_metric_gaps(
    rows: list[dict[str, float]],
    rules: tuple[MetricRule, ...],
) -> list[MetricFailure]:
    failures: list[MetricFailure] = []
    for rule in rules:
        values = [row[rule.name] for row in rows if rule.name in row]
        if not values:
            failures.append(
                MetricFailure(
                    metric=rule.name,
                    observed=0.0,
                    target=rule.target,
                    direction=rule.direction,
                    gap=abs(rule.target),
                    aggregation=rule.aggregation,
                )
            )
            continue
        observed = _aggregate(values, rule.aggregation)
        passed = _passes(observed, rule)
        if passed:
            continue
        failures.append(
            MetricFailure(
                metric=rule.name,
                observed=observed,
                target=rule.target,
                direction=rule.direction,
                gap=_gap(observed, rule),
                aggregation=rule.aggregation,
            )
        )
    return failures


def _aggregate(values: list[float], mode: str) -> float:
    if mode == "max":
        return max(values)
    if mode == "min":
        return min(values)
    if mode == "best":
        return min(values)
    return float(mean(values))


def _passes(observed: float, rule: MetricRule) -> bool:
    if rule.direction in {"lte", "minimize"}:
        return observed <= rule.target + rule.tolerance
    return observed >= rule.target - rule.tolerance


def _gap(observed: float, rule: MetricRule) -> float:
    if rule.direction in {"lte", "minimize"}:
        return max(0.0, observed - (rule.target + rule.tolerance))
    return max(0.0, (rule.target - rule.tolerance) - observed)


def _analyze_config_sanity(run: RunHandle) -> SuspectedCause | None:
    path = run.subdir("experiment") / "experiment_plan.approved.md"
    if not path.exists():
        return SuspectedCause(
            kind="config_sanity",
            severity="medium",
            summary="No approved experiment plan was available for diagnosis.",
        )
    metadata = parse_fm(path.read_text(encoding="utf-8")).metadata
    ablations = metadata.get("ablations", [])
    if not isinstance(ablations, list) or not ablations:
        return SuspectedCause(
            kind="config_sanity",
            severity="high",
            summary="Experiment plan has no ablations, so execution coverage is too narrow.",
            evidence=("experiment_plan.ablations",),
        )
    estimated_runs = metadata.get("estimated_runs")
    if isinstance(estimated_runs, int) and estimated_runs < len(ablations):
        return SuspectedCause(
            kind="config_sanity",
            severity="low",
            summary="estimated_runs is lower than the number of configured ablations.",
            evidence=("experiment_plan.estimated_runs",),
        )
    return None


def _analyze_code_change_risk(run: RunHandle) -> SuspectedCause | None:
    path = run.subdir("coding") / "code_spec.approved.md"
    if not path.exists():
        return SuspectedCause(
            kind="code_change_risk",
            severity="medium",
            summary="No approved code spec was available for diagnosis.",
        )
    metadata = parse_fm(path.read_text(encoding="utf-8")).metadata
    baseline = metadata.get("baseline_compat", {})
    if isinstance(baseline, dict) and baseline.get("preserved") is False:
        return SuspectedCause(
            kind="code_change_risk",
            severity="high",
            summary="Code spec declares baseline compatibility was not preserved.",
            evidence=("code_spec.baseline_compat.preserved",),
        )
    files = metadata.get("files_changed", [])
    if isinstance(files, list):
        high_risk = [
            str(item.get("path", ""))
            for item in files
            if isinstance(item, dict) and item.get("risk") == "high"
        ]
        if high_risk:
            return SuspectedCause(
                kind="code_change_risk",
                severity="high",
                summary="Code spec includes high-risk file changes.",
                evidence=tuple(high_risk),
            )
    return None
