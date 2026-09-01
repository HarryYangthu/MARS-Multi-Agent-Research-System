"""Versioned records shared by V3.0 Core and optional V3.1 extensions.

These models deliberately contain no project-specific fields or imports.  A
Project Pack may add namespaced metadata, while the stable fields below remain
portable across public and private distributions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    QUEUED = "queued"
    RUNNING = "running"
    EVALUATED = "evaluated"
    DOMINATED = "dominated"
    ELITE = "elite"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    FAILED = "failed"
    PROMOTED = "promoted"


class FidelityLevel(str, Enum):
    F0 = "F0"
    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    F4 = "F4"


class ObjectiveDirection(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class ObjectiveSpec(FrozenRecord):
    name: str = Field(min_length=1)
    direction: ObjectiveDirection
    unit: str = ""
    hard_constraint: float | None = None


class BudgetLimits(FrozenRecord):
    proposals: int = Field(default=20, ge=1)
    llm_tokens: int = Field(default=200_000, ge=0)
    gpu_seconds: float = Field(default=0.0, ge=0.0)
    wall_seconds: float = Field(default=3_600.0, gt=0.0)
    api_cost: float = Field(default=0.0, ge=0.0)
    max_parallel: int = Field(default=1, ge=1)


class ResearchTaskContract(FrozenRecord):
    schema_id: Literal["research_task_contract.v1"] = "research_task_contract.v1"
    run_id: str = Field(min_length=1)
    project: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    evolution_zones: tuple[str, ...] = ()
    dataset_ref: str = ""
    dataset_hash: str = ""
    baseline_ref: str = ""
    baseline_hash: str = ""
    evaluator_ref: str = ""
    evaluator_hash: str = ""
    objectives: tuple[ObjectiveSpec, ...] = ()
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    seed: int = 0
    promotion_policy: dict[str, Any] = Field(default_factory=dict)
    stop_policy: dict[str, Any] = Field(default_factory=dict)
    owner: str = ""
    reviewer: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    frozen_at: datetime | None = None


class ModelGenome(FrozenRecord):
    schema_id: Literal["model_genome.v1"] = "model_genome.v1"
    family: str = Field(min_length=1)
    structure: dict[str, Any] = Field(default_factory=dict)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    recipe: dict[str, Any] = Field(default_factory=dict)
    mutable_zones: tuple[str, ...] = ()


class CandidateRecord(FrozenRecord):
    schema_id: Literal["candidate.v1"] = "candidate.v1"
    candidate_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    parent_ids: tuple[str, ...] = ()
    generation: int = Field(default=0, ge=0)
    iteration: int = Field(default=0, ge=0)
    creator: str = Field(min_length=1)
    model_provider: str = ""
    model_name: str = ""
    prompt_hash: str = ""
    context_manifest_ref: str = ""
    operator: str = Field(min_length=1)
    genome: ModelGenome
    artifact_refs: dict[str, str] = Field(default_factory=dict)
    fingerprints: dict[str, str] = Field(default_factory=dict)
    status: CandidateStatus = CandidateStatus.DRAFT
    idempotency_key: str = Field(min_length=1)
    failure_reason: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetricValue(FrozenRecord):
    value: float
    unit: str = ""
    direction: ObjectiveDirection


class CandidateEvaluation(FrozenRecord):
    schema_id: Literal["candidate_evaluation.v1"] = "candidate_evaluation.v1"
    evaluation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    fidelity: FidelityLevel
    seed: int
    evaluator_hash: str = Field(min_length=1)
    dataset_hash: str = ""
    environment_hash: str = ""
    hardware_hash: str = ""
    raw_metrics: dict[str, Any] = Field(default_factory=dict)
    canonical_metrics: dict[str, MetricValue] = Field(default_factory=dict)
    hard_constraints_passed: bool = False
    evidence_refs: tuple[str, ...] = ()
    resource_usage: dict[str, float] = Field(default_factory=dict)
    findings: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class BudgetTransaction(FrozenRecord):
    schema_id: Literal["budget_transaction.v1"] = "budget_transaction.v1"
    transaction_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    candidate_id: str = ""
    idempotency_key: str = Field(min_length=1)
    proposals: int = Field(default=0, ge=0)
    llm_tokens: int = Field(default=0, ge=0)
    gpu_seconds: float = Field(default=0.0, ge=0.0)
    wall_seconds: float = Field(default=0.0, ge=0.0)
    api_cost: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=utc_now)


class ArchiveSnapshot(FrozenRecord):
    schema_id: Literal["archive_snapshot.v1"] = "archive_snapshot.v1"
    snapshot_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    pareto_candidate_ids: tuple[str, ...] = ()
    niche_elites: dict[str, str] = Field(default_factory=dict)
    negative_candidate_ids: tuple[str, ...] = ()
    quarantined_candidate_ids: tuple[str, ...] = ()
    lineage_refs: tuple[str, ...] = ()
    budget_snapshot: dict[str, float] = Field(default_factory=dict)
    stop_reason: str = ""
    snapshot_hash: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class HypothesisRecord(FrozenRecord):
    schema_id: Literal["hypothesis.v1"] = "hypothesis.v1"
    hypothesis_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    round_index: int = Field(ge=0)
    parent_ids: tuple[str, ...] = ()
    mechanism: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    testable_predictions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    uncertainty: str = ""
    operator: str = "generate"
    cluster_id: str = ""
    elo: float = 1000.0
    blocked: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ReflectionRecord(FrozenRecord):
    schema_id: Literal["reflection.v1"] = "reflection.v1"
    reflection_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    correctness: str = ""
    novelty: str = ""
    falsifiability: str = ""
    assumptions: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class PairwiseMatchRecord(FrozenRecord):
    schema_id: Literal["pairwise_match.v1"] = "pairwise_match.v1"
    match_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    round_index: int = Field(ge=0)
    left_id: str = Field(min_length=1)
    right_id: str = Field(min_length=1)
    outcome: Literal["left", "right", "draw"]
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    left_rating_before: float = 1000.0
    right_rating_before: float = 1000.0
    left_rating_after: float = 1000.0
    right_rating_after: float = 1000.0
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("right_id")
    @classmethod
    def candidates_must_differ(cls, value: str, info: Any) -> str:
        if value == info.data.get("left_id"):
            raise ValueError("pairwise candidates must differ")
        return value


class MetaReviewRecord(FrozenRecord):
    schema_id: Literal["meta_review.v1"] = "meta_review.v1"
    meta_review_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    round_index: int = Field(ge=0)
    recurring_errors: tuple[str, ...] = ()
    successful_patterns: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()
    unexplored_regions: tuple[str, ...] = ()
    next_round_guidance: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
