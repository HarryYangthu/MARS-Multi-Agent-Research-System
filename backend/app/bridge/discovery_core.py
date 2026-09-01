"""Injectable facade over the project-agnostic Discovery Core algorithms."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from typing import Any, Protocol

from app.execution.adapters.base import AdapterResponse
from app.harness.discovery.archive import ParetoArchive, evaluation_errors
from app.harness.discovery.canonical import stable_hash
from app.harness.discovery.evaluation_aggregate import t_critical_95
from app.harness.discovery.models import (
    ArchiveSnapshot,
    CandidateEvaluation,
    CandidateRecord,
    FidelityLevel,
    MetricValue,
    ResearchTaskContract,
)
from app.harness.discovery.preflight import PreflightReport, run_preflight
from app.storage.discovery_budget_ledger import BudgetSnapshot


class DiscoveryCore(Protocol):
    def preflight(
        self,
        candidate: CandidateRecord,
        contract: ResearchTaskContract,
    ) -> PreflightReport: ...

    def evaluate(
        self,
        *,
        candidate: CandidateRecord,
        contract: ResearchTaskContract,
        response: AdapterResponse,
        fidelity: FidelityLevel,
        seed: int,
    ) -> CandidateEvaluation: ...

    def archive(
        self,
        *,
        contract: ResearchTaskContract,
        evaluations: tuple[CandidateEvaluation, ...],
        iteration: int,
        budget: BudgetSnapshot,
        quarantined_candidate_ids: tuple[str, ...],
        stop_reason: str = "",
    ) -> ArchiveSnapshot: ...


@dataclass(frozen=True)
class _MetricParseResult:
    metrics: dict[str, MetricValue]
    findings: tuple[str, ...]
    evaluator_hash: str
    dataset_hash: str


class DefaultDiscoveryCore:
    """Use the frozen deterministic Core without project-specific imports."""

    def preflight(
        self,
        candidate: CandidateRecord,
        contract: ResearchTaskContract,
    ) -> PreflightReport:
        return run_preflight(candidate=candidate, contract=contract)

    def evaluate(
        self,
        *,
        candidate: CandidateRecord,
        contract: ResearchTaskContract,
        response: AdapterResponse,
        fidelity: FidelityLevel,
        seed: int,
    ) -> CandidateEvaluation:
        findings = list(response.findings)
        if response.raw_metrics.get("schema_id") == "metric_envelope.v1":
            parsed = _parse_metric_envelope(
                response.raw_metrics,
                contract=contract,
                seed=seed,
            )
        else:
            parsed = _parse_flat_metrics(response.raw_metrics, contract=contract)
        findings.extend(parsed.findings)
        hard_constraints_passed = response.status == "ok" and not parsed.findings
        for objective in contract.objectives:
            metric = parsed.metrics.get(objective.name)
            if metric is None or objective.hard_constraint is None:
                continue
            if objective.direction.value == "minimize":
                hard_constraints_passed = (
                    hard_constraints_passed
                    and metric.value <= objective.hard_constraint
                )
            else:
                hard_constraints_passed = (
                    hard_constraints_passed
                    and metric.value >= objective.hard_constraint
                )

        evaluator_hash = (
            contract.evaluator_hash
            or parsed.evaluator_hash
            or "unspecified-evaluator"
        )
        dataset_hash = contract.dataset_hash or parsed.dataset_hash

        identity = {
            "candidate_id": candidate.candidate_id,
            "fidelity": fidelity.value,
            "seed": seed,
            "evaluator_hash": evaluator_hash,
            "dataset_hash": dataset_hash,
        }
        digest = stable_hash(identity, prefix="")[:24]
        return CandidateEvaluation(
            evaluation_id=f"eval_{digest}",
            candidate_id=candidate.candidate_id,
            run_id=contract.run_id,
            fidelity=fidelity,
            seed=seed,
            evaluator_hash=evaluator_hash,
            dataset_hash=dataset_hash,
            raw_metrics=dict(response.raw_metrics),
            canonical_metrics=parsed.metrics,
            hard_constraints_passed=hard_constraints_passed,
            evidence_refs=tuple(sorted(response.artifacts.values())),
            resource_usage=dict(response.resource_usage),
            findings=tuple(findings),
        )

    def archive(
        self,
        *,
        contract: ResearchTaskContract,
        evaluations: tuple[CandidateEvaluation, ...],
        iteration: int,
        budget: BudgetSnapshot,
        quarantined_candidate_ids: tuple[str, ...],
        stop_reason: str = "",
    ) -> ArchiveSnapshot:
        archive = ParetoArchive(contract.objectives)
        negative: list[str] = []
        grouped: dict[str, list[CandidateEvaluation]] = {}
        for evaluation in evaluations:
            grouped.setdefault(evaluation.candidate_id, []).append(evaluation)
        for candidate_id, candidate_evaluations in sorted(grouped.items()):
            try:
                aggregate = _conservative_candidate_cohort(
                    tuple(candidate_evaluations),
                    contract=contract,
                )
            except ValueError:
                negative.append(candidate_id)
                continue
            archive.add(aggregate)
        usage = budget.used
        budget_payload = {
            "proposals": float(usage.proposals),
            "llm_tokens": float(usage.llm_tokens),
            "gpu_seconds": usage.gpu_seconds,
            "wall_seconds": usage.wall_seconds,
            "api_cost": usage.api_cost,
        }
        return archive.snapshot(
            run_id=contract.run_id,
            iteration=iteration,
            negative_candidate_ids=tuple(sorted(set(negative))),
            quarantined_candidate_ids=tuple(sorted(set(quarantined_candidate_ids))),
            budget_snapshot=budget_payload,
            stop_reason=stop_reason,
        )


def _conservative_candidate_cohort(
    evaluations: tuple[CandidateEvaluation, ...],
    *,
    contract: ResearchTaskContract,
) -> CandidateEvaluation:
    """Collapse the highest-fidelity shared-seed cohort before Pareto ranking."""

    if not evaluations:
        raise ValueError("candidate evaluations are required")
    candidate_ids = {item.candidate_id for item in evaluations}
    run_ids = {item.run_id for item in evaluations}
    if len(candidate_ids) != 1 or run_ids != {contract.run_id}:
        raise ValueError("candidate cohort identity mismatch")
    fidelity = max((item.fidelity for item in evaluations), key=_fidelity_rank)
    cohort = tuple(item for item in evaluations if item.fidelity == fidelity)
    if any(evaluation_errors(item, contract.objectives) for item in cohort):
        raise ValueError("candidate cohort contains invalid evidence")
    evaluator_hashes = {item.evaluator_hash for item in cohort}
    dataset_hashes = {item.dataset_hash for item in cohort}
    if len(evaluator_hashes) != 1 or len(dataset_hashes) != 1:
        raise ValueError("candidate cohort mixes evaluator or dataset identities")

    metrics: dict[str, MetricValue] = {}
    means: dict[str, float] = {}
    standard_errors: dict[str, float] = {}
    critical = t_critical_95(len(cohort)) if len(cohort) > 1 else 0.0
    for objective in contract.objectives:
        values = [item.canonical_metrics[objective.name].value for item in cohort]
        mean = statistics.fmean(values)
        standard_error = (
            statistics.stdev(values) / math.sqrt(len(values))
            if len(values) > 1
            else 0.0
        )
        margin = critical * standard_error
        conservative_value = (
            mean + margin
            if objective.direction.value == "minimize"
            else mean - margin
        )
        metrics[objective.name] = MetricValue(
            value=conservative_value,
            unit=objective.unit,
            direction=objective.direction,
        )
        means[objective.name] = mean
        standard_errors[objective.name] = standard_error

    source_ids = tuple(sorted(item.evaluation_id for item in cohort))
    identity = {
        "candidate_id": next(iter(candidate_ids)),
        "fidelity": fidelity.value,
        "evaluator_hash": next(iter(evaluator_hashes)),
        "dataset_hash": next(iter(dataset_hashes)),
        "source_evaluation_ids": source_ids,
        "aggregation": "student_t_conservative_95",
    }
    return CandidateEvaluation(
        evaluation_id=f"cohort_{stable_hash(identity, prefix='')[:24]}",
        candidate_id=next(iter(candidate_ids)),
        run_id=contract.run_id,
        fidelity=fidelity,
        seed=min(item.seed for item in cohort),
        evaluator_hash=next(iter(evaluator_hashes)),
        dataset_hash=next(iter(dataset_hashes)),
        raw_metrics={
            "schema_id": "candidate_cohort_aggregate.v1",
            "aggregation": "student_t_conservative_95",
            "source_evaluation_ids": source_ids,
            "seeds": tuple(sorted(item.seed for item in cohort)),
            "means": means,
            "standard_errors": standard_errors,
            "critical_value": critical,
        },
        canonical_metrics=metrics,
        hard_constraints_passed=True,
        evidence_refs=tuple(
            sorted({ref for item in cohort for ref in item.evidence_refs})
        ),
        findings=("highest_fidelity_shared_seed_cohort",),
        created_at=max(item.created_at for item in cohort),
    )


def _fidelity_rank(value: FidelityLevel) -> int:
    return int(value.value[1:])


def _parse_flat_metrics(
    raw_metrics: dict[str, Any],
    *,
    contract: ResearchTaskContract,
) -> _MetricParseResult:
    metrics: dict[str, MetricValue] = {}
    findings: list[str] = []
    for objective in contract.objectives:
        raw_value = raw_metrics.get(objective.name)
        try:
            if raw_value is None:
                raise TypeError
            value = float(raw_value)
        except (TypeError, ValueError):
            findings.append(f"missing or invalid objective '{objective.name}'")
            continue
        if not math.isfinite(value):
            findings.append(f"objective '{objective.name}' is not finite")
            continue
        metrics[objective.name] = MetricValue(
            value=value,
            unit=objective.unit,
            direction=objective.direction,
        )
    return _MetricParseResult(
        metrics=metrics,
        findings=tuple(findings),
        evaluator_hash="",
        dataset_hash="",
    )


def _parse_metric_envelope(
    envelope: dict[str, Any],
    *,
    contract: ResearchTaskContract,
    seed: int,
) -> _MetricParseResult:
    findings: list[str] = []
    raw_section = _mapping(envelope.get("raw_metrics"), "raw_metrics", findings)
    canonical_section = _mapping(
        envelope.get("canonical_metrics"),
        "canonical_metrics",
        findings,
    )
    provenance = _mapping(envelope.get("provenance"), "provenance", findings)
    if raw_section is None:
        findings.append("metric envelope raw_metrics is unavailable")

    claimed_hash = envelope.get("envelope_hash")
    if not isinstance(claimed_hash, str) or not claimed_hash:
        findings.append("metric envelope envelope_hash is missing or invalid")
    else:
        hash_payload = dict(envelope)
        hash_payload.pop("envelope_hash", None)
        expected_hash = _metric_envelope_hash(hash_payload, findings)
        if expected_hash is not None and expected_hash != claimed_hash:
            findings.append("metric envelope envelope_hash mismatch")

    evaluator_hash = ""
    dataset_hash = ""
    if provenance is not None:
        evaluator_hash = _string_field(
            provenance,
            "evaluator_hash",
            "provenance",
            findings,
        )
        dataset_hash = _string_field(
            provenance,
            "dataset_hash",
            "provenance",
            findings,
        )
        provenance_seed = provenance.get("seed")
        if isinstance(provenance_seed, bool) or not isinstance(provenance_seed, int):
            findings.append("metric envelope provenance.seed must be an integer")
        elif provenance_seed != seed:
            findings.append("metric envelope provenance.seed mismatch")
        if contract.evaluator_hash and evaluator_hash != contract.evaluator_hash:
            findings.append("metric envelope provenance.evaluator_hash mismatch")
        if contract.dataset_hash and dataset_hash != contract.dataset_hash:
            findings.append("metric envelope provenance.dataset_hash mismatch")

    metrics: dict[str, MetricValue] = {}
    if canonical_section is not None and provenance is not None:
        if raw_section is not None:
            _validate_raw_metrics(
                raw_section,
                provenance=provenance,
                findings=findings,
            )
        for objective in contract.objectives:
            item = _mapping(
                canonical_section.get(objective.name),
                f"canonical_metrics.{objective.name}",
                findings,
            )
            if item is None:
                continue
            item_findings: list[str] = []
            value = _finite_number(
                item.get("value"),
                f"canonical_metrics.{objective.name}.value",
                item_findings,
            )
            unit = item.get("unit")
            if unit != objective.unit:
                item_findings.append(
                    f"canonical_metrics.{objective.name}.unit mismatch"
                )
            direction = item.get("direction")
            if direction != objective.direction.value:
                item_findings.append(
                    f"canonical_metrics.{objective.name}.direction mismatch"
                )
            _validate_metric_provenance(
                item,
                provenance=provenance,
                metric_name=objective.name,
                findings=item_findings,
            )
            findings.extend(item_findings)
            if value is not None and not item_findings:
                metrics[objective.name] = MetricValue(
                    value=value,
                    unit=objective.unit,
                    direction=objective.direction,
                )

    # Any envelope-level failure invalidates all canonical values.  This keeps
    # downstream consumers safe even if they forget to inspect the hard flag.
    if findings:
        metrics = {}
    return _MetricParseResult(
        metrics=metrics,
        findings=tuple(findings),
        evaluator_hash=evaluator_hash,
        dataset_hash=dataset_hash,
    )


def _mapping(
    value: Any,
    label: str,
    findings: list[str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        findings.append(f"metric envelope {label} must be a mapping")
        return None
    return dict(value)


def _string_field(
    container: dict[str, Any],
    key: str,
    label: str,
    findings: list[str],
) -> str:
    value = container.get(key)
    if not isinstance(value, str):
        findings.append(f"metric envelope {label}.{key} is missing or invalid")
        return ""
    return value


def _finite_number(
    value: Any,
    label: str,
    findings: list[str],
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        findings.append(f"metric envelope {label} must be numeric")
        return None
    number = float(value)
    if not math.isfinite(number):
        findings.append(f"metric envelope {label} is not finite")
        return None
    return number


def _validate_metric_provenance(
    metric: dict[str, Any],
    *,
    provenance: dict[str, Any],
    metric_name: str,
    findings: list[str],
) -> None:
    for key in ("seed", "evaluator_hash", "dataset_hash"):
        if key not in metric:
            findings.append(f"canonical_metrics.{metric_name}.{key} is missing")
        elif metric[key] != provenance.get(key):
            findings.append(f"canonical_metrics.{metric_name}.{key} mismatch")


def _validate_raw_metrics(
    raw_metrics: dict[str, Any],
    *,
    provenance: dict[str, Any],
    findings: list[str],
) -> None:
    for metric_name, value in sorted(raw_metrics.items()):
        item = _mapping(value, f"raw_metrics.{metric_name}", findings)
        if item is None:
            continue
        _finite_number(
            item.get("value"),
            f"raw_metrics.{metric_name}.value",
            findings,
        )
        _validate_metric_provenance(
            item,
            provenance=provenance,
            metric_name=f"raw:{metric_name}",
            findings=findings,
        )


def _metric_envelope_hash(
    payload: dict[str, Any],
    findings: list[str],
) -> str | None:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        findings.append("metric envelope is not canonical JSON")
        return None
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
