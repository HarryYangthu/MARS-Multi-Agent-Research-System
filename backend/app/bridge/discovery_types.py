"""Service-layer contracts for orchestration and REST serialization."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.harness.discovery.models import (
    ArchiveSnapshot,
    BudgetLimits,
    CandidateEvaluation,
    CandidateRecord,
    ObjectiveSpec,
    ResearchTaskContract,
)
from app.harness.discovery.code_candidate import CodeCandidateSpec
from app.harness.discovery.code_materialization import CodeMaterializationBundle
from app.storage.discovery_archive_store import ArchiveStore
from app.storage.discovery_budget_ledger import BudgetLedger, BudgetSnapshot
from app.storage.discovery_candidate_store import CandidateStore
from app.storage.discovery_checkpoint_store import (
    DiscoveryCheckpoint,
    DiscoveryCheckpointStore,
)
from app.storage.discovery_lineage_store import LineageStore
from app.storage.discovery_promotion_store import (
    PromotionTask,
    PromotionTaskStore,
)
from app.storage.discovery_search_state_store import DiscoverySearchStateStore
from app.storage.run_store import RunHandle


class DiscoveryLifecycle(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_HITL = "waiting_hitl"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class DiscoveryRunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: str = Field(min_length=1)
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
    objectives: tuple[ObjectiveSpec, ...] = Field(min_length=1)
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    seed: int = 0
    evaluation_seeds: tuple[int, ...] = Field(default=(), max_length=32)
    promotion_policy: dict[str, Any] = Field(default_factory=dict)
    stop_policy: dict[str, Any] = Field(default_factory=dict)
    owner: str = ""
    reviewer: str = ""
    candidates_per_iteration: int = Field(default=20, ge=1, le=100)
    max_iterations: int = Field(default=1, ge=1, le=100)
    auto_approve: bool = False
    idea_mode: Literal["fast", "auto"] = "fast"
    project_inputs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> DiscoveryRunSpec:
        mode = str(self.project_inputs.get("mode") or "").strip().lower()
        if len(set(self.evaluation_seeds)) != len(self.evaluation_seeds):
            raise ValueError("evaluation_seeds must be unique")
        raw_statistical_gate = self.promotion_policy.get("statistical_gate")
        if (
            isinstance(raw_statistical_gate, dict)
            and raw_statistical_gate.get("enabled") is True
        ):
            raw_minimum_pairs = raw_statistical_gate.get("minimum_pairs", 3)
            if (
                isinstance(raw_minimum_pairs, bool)
                or not isinstance(raw_minimum_pairs, int)
                or raw_minimum_pairs < 1
            ):
                raise ValueError(
                    "statistical_gate.minimum_pairs must be a positive integer"
                )
            if len(self.evaluation_seeds) < raw_minimum_pairs:
                raise ValueError(
                    "statistical gate requires at least minimum_pairs shared "
                    "evaluation_seeds"
                )
        if mode == "production":
            missing = tuple(
                name
                for name, value in (
                    ("dataset_hash", self.dataset_hash),
                    ("baseline_hash", self.baseline_hash),
                    ("evaluator_hash", self.evaluator_hash),
                )
                if not value.strip()
            )
            if missing:
                raise ValueError(
                    "production discovery requires frozen " + ", ".join(missing)
                )
            if len(self.evaluation_seeds) < 3:
                raise ValueError(
                    "production discovery requires at least three shared evaluation_seeds"
                )
        if (
            self.project_inputs.get("scientific_comparison") is True
            and len(self.evaluation_seeds) < 3
        ):
            raise ValueError(
                "scientific comparison requires at least three shared evaluation_seeds"
            )
        fidelity = self.project_inputs.get("fidelity")
        if fidelity is not None and fidelity not in {"F0", "F1", "F2", "F3", "F4"}:
            raise ValueError("project_inputs.fidelity must be one of F0..F4")
        return self

    def contract(self, run_id: str) -> ResearchTaskContract:
        return ResearchTaskContract(
            run_id=run_id,
            project=self.project,
            objective=self.objective,
            allowed_paths=self.allowed_paths,
            forbidden_paths=self.forbidden_paths,
            evolution_zones=self.evolution_zones,
            dataset_ref=self.dataset_ref,
            dataset_hash=self.dataset_hash,
            baseline_ref=self.baseline_ref,
            baseline_hash=self.baseline_hash,
            evaluator_ref=self.evaluator_ref,
            evaluator_hash=self.evaluator_hash,
            objectives=self.objectives,
            budget=self.budget,
            seed=self.seed,
            promotion_policy=dict(self.promotion_policy),
            stop_policy=dict(self.stop_policy),
            owner=self.owner,
            reviewer=self.reviewer,
        )


class IterationNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    iteration: int = Field(ge=0)
    child_run_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    depends_on_run_ids: tuple[str, ...] = ()
    status: str = "running"


class DiscoveryProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lifecycle: DiscoveryLifecycle = DiscoveryLifecycle.CREATED
    next_iteration: int = Field(default=0, ge=0)
    next_ordinal: int = Field(default=0, ge=0)
    iteration_nodes: tuple[IterationNode, ...] = ()
    hitl_pending: bool = False
    hitl_resolved: bool = False
    selected_candidate_id: str = ""
    stop_reason: str = ""
    stop_code: str = ""
    stop_details: tuple[str, ...] = ()
    selection_evidence: dict[str, Any] = Field(default_factory=dict)


class ParentCandidateSignal(BaseModel):
    """Public search features passed to a domain-neutral candidate agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    quality: float = 0.0
    scarcity: float = 0.0
    uncertainty: float = 0.0
    recency: float = 0.0
    offspring_count: int = Field(default=0, ge=0)


class BanditArmSignal(BaseModel):
    """Persisted UCB statistics exposed without coupling Bridge to a Store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    arm_id: str = Field(min_length=1)
    pulls: int = Field(default=0, ge=0)
    total_reward: float = 0.0


class CandidateProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: ResearchTaskContract
    iteration: int = Field(ge=0)
    ordinal: int = Field(ge=0)
    child_run_id: str = Field(min_length=1)
    parent_candidate_ids: tuple[str, ...] = ()
    parent_candidates: tuple[ParentCandidateSignal, ...] = ()
    model_arms: tuple[BanditArmSignal, ...] = ()
    operator_arms: tuple[BanditArmSignal, ...] = ()


class DiscoveryCandidateAgent(Protocol):
    async def propose(self, request: CandidateProposalRequest) -> CandidateRecord: ...


class PreparedCodeCandidate(Protocol):
    """Minimum secure-workspace receipt consumed by Discovery orchestration."""

    @property
    def root(self) -> Path: ...

    @property
    def snapshot_ref(self) -> str: ...

    @property
    def workspace_ref(self) -> str: ...

    @property
    def receipt_ref(self) -> str: ...

    @property
    def receipt_sha256(self) -> str: ...

    @property
    def bundle_sha256(self) -> str: ...

    @property
    def workspace_manifest_sha256(self) -> str: ...


class CodeCandidateWorkspacePreparer(Protocol):
    """Bridge composition boundary for secure materialization plus preflight."""

    async def prepare(
        self,
        *,
        run: RunHandle,
        contract: ResearchTaskContract,
        candidate: CandidateRecord,
        code_spec: CodeCandidateSpec,
        bundle: CodeMaterializationBundle,
    ) -> PreparedCodeCandidate: ...


@dataclass(frozen=True)
class DiscoveryStores:
    candidates: CandidateStore
    lineage: LineageStore
    archive: ArchiveStore
    budget: BudgetLedger
    checkpoints: DiscoveryCheckpointStore
    search: DiscoverySearchStateStore
    promotions: PromotionTaskStore


class DiscoveryStoreFactory(Protocol):
    def build(
        self,
        run: RunHandle,
        contract: ResearchTaskContract,
    ) -> DiscoveryStores: ...


class FilesystemDiscoveryStoreFactory:
    def build(
        self,
        run: RunHandle,
        contract: ResearchTaskContract,
    ) -> DiscoveryStores:
        return DiscoveryStores(
            candidates=CandidateStore(run.root, run_id=run.run_id),
            lineage=LineageStore(run.root, run_id=run.run_id),
            archive=ArchiveStore(run.root, run_id=run.run_id),
            budget=BudgetLedger(
                run.root,
                run_id=run.run_id,
                limits=contract.budget,
            ),
            checkpoints=DiscoveryCheckpointStore(run.root, run_id=run.run_id),
            search=DiscoverySearchStateStore(run.root, run_id=run.run_id),
            promotions=PromotionTaskStore(run.root, run_id=run.run_id),
        )


class DiscoveryRunView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    project: str
    objective: str
    lifecycle: DiscoveryLifecycle
    checkpoint_sequence: int
    next_iteration: int
    next_ordinal: int
    candidate_count: int
    evaluated_count: int
    failed_count: int
    quarantined_count: int
    promotion_pending_count: int = 0
    promotion_running_count: int = 0
    promotion_completed_count: int = 0
    promotion_failed_count: int = 0
    promotion_cancelled_count: int = 0
    hitl_pending: bool
    selected_candidate_id: str = ""
    iteration_nodes: tuple[IterationNode, ...] = ()
    budget: BudgetSnapshot
    latest_archive: ArchiveSnapshot | None = None
    stop_reason: str = ""
    stop_code: str = ""
    stop_details: tuple[str, ...] = ()
    selection_evidence: dict[str, Any] = Field(default_factory=dict)


class DiscoveryReplayView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run: DiscoveryRunView
    checkpoints: tuple[DiscoveryCheckpoint, ...]
    candidates: tuple[CandidateRecord, ...]
    evaluations: tuple[CandidateEvaluation, ...]
    promotions: tuple[PromotionTask, ...] = ()
    archives: tuple[ArchiveSnapshot, ...]
    events: tuple[dict[str, Any], ...]
