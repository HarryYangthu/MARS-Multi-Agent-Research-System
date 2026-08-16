"""Injectable facade over the project-agnostic Discovery Core algorithms."""
from __future__ import annotations

import math
from typing import Protocol

from app.execution.adapters.base import AdapterResponse
from app.harness.discovery.archive import ParetoArchive, evaluation_errors
from app.harness.discovery.canonical import stable_hash
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
        canonical_metrics: dict[str, MetricValue] = {}
        findings = list(response.findings)
        hard_constraints_passed = response.status == "ok"
        for objective in contract.objectives:
            raw_value = response.raw_metrics.get(objective.name)
            try:
                if raw_value is None:
                    raise TypeError
                value = float(raw_value)
            except (TypeError, ValueError):
                findings.append(f"missing or invalid objective '{objective.name}'")
                hard_constraints_passed = False
                continue
            if not math.isfinite(value):
                findings.append(f"objective '{objective.name}' is not finite")
                hard_constraints_passed = False
                continue
            canonical_metrics[objective.name] = MetricValue(
                value=value,
                unit=objective.unit,
                direction=objective.direction,
            )
            if objective.hard_constraint is None:
                continue
            if objective.direction.value == "minimize":
                hard_constraints_passed = hard_constraints_passed and value <= objective.hard_constraint
            else:
                hard_constraints_passed = hard_constraints_passed and value >= objective.hard_constraint

        identity = {
            "candidate_id": candidate.candidate_id,
            "fidelity": fidelity.value,
            "seed": seed,
            "evaluator_hash": contract.evaluator_hash,
            "dataset_hash": contract.dataset_hash,
        }
        digest = stable_hash(identity, prefix="")[:24]
        return CandidateEvaluation(
            evaluation_id=f"eval_{digest}",
            candidate_id=candidate.candidate_id,
            run_id=contract.run_id,
            fidelity=fidelity,
            seed=seed,
            evaluator_hash=contract.evaluator_hash or "unspecified-evaluator",
            dataset_hash=contract.dataset_hash,
            raw_metrics=dict(response.raw_metrics),
            canonical_metrics=canonical_metrics,
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
        for evaluation in sorted(evaluations, key=lambda item: item.candidate_id):
            if evaluation_errors(evaluation, contract.objectives):
                negative.append(evaluation.candidate_id)
                continue
            archive.add(evaluation)
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
