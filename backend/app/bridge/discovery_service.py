"""Product orchestration for durable, project-agnostic model discovery."""
from __future__ import annotations

import asyncio
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app.bridge.discovery_core import DefaultDiscoveryCore, DiscoveryCore
from app.bridge.discovery_events import DiscoveryEventSink
from app.bridge.discovery_types import (
    BanditArmSignal,
    CandidateProposalRequest,
    CodeCandidateWorkspacePreparer,
    DiscoveryCandidateAgent,
    DiscoveryLifecycle,
    DiscoveryProgress,
    DiscoveryReplayView,
    DiscoveryRunSpec,
    DiscoveryRunView,
    DiscoveryStoreFactory,
    DiscoveryStores,
    FilesystemDiscoveryStoreFactory,
    IterationNode,
    ParentCandidateSignal,
)
from app.execution.adapters.base import (
    AdapterAction,
    AdapterRequest,
    AdapterResponse,
    ProjectAdapter,
)
from app.harness.discovery.models import (
    BudgetTransaction,
    CandidateEvaluation,
    CandidateRecord,
    CandidateStatus,
    FidelityLevel,
    ObjectiveDirection,
    ResearchTaskContract,
)
from app.harness.discovery.code_candidate import CodeCandidateSpec
from app.harness.discovery.code_materialization import CodeMaterializationBundle
from app.harness.discovery.evaluation_aggregate import (
    DatasetRole,
    EvaluationAggregate,
    StatisticalGate,
    aggregate_candidate_vs_baseline,
    t_critical_95,
)
from app.harness.discovery.promotion import PromotionDecision, decide_promotion
from app.harness.discovery.protocol import DiscoveryErrorCode, DiscoveryEventName
from app.harness.discovery.stopping import (
    BudgetUsage as PolicyBudgetUsage,
    PatienceState,
    StopDecision,
    StopPolicy,
    StopReason,
    evaluate_stop,
)
from app.harness.runtime.event_bus import EventBus
from app.storage.artifact_store import ArtifactStore
from app.storage.discovery_budget_ledger import BudgetExceededError
from app.storage.discovery_checkpoint_store import CheckpointStatus
from app.storage.discovery_common import (
    DiscoveryPaths,
    atomic_write_json,
    discovery_lock,
    model_payload,
    payload_hash,
    read_json,
    stable_key,
)
from app.storage.discovery_search_state_store import DiscoverySearchState
from app.storage.discovery_promotion_store import (
    PromotionTask,
    PromotionTaskState,
)
from app.storage.run_store import RunHandle, RunStore


_CODE_CANDIDATE_SPEC_REF = "code_candidate_spec"
_CODE_MATERIALIZATION_BUNDLE_REF = "code_materialization_bundle"


class DiscoveryServiceError(RuntimeError):
    def __init__(
        self,
        code: DiscoveryErrorCode,
        detail: str,
        *,
        status_code: int = 409,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class _ServiceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = "discovery_service.v1"
    run_id: str = Field(min_length=1)
    create_idempotency_key: str = Field(min_length=1)
    spec: DiscoveryRunSpec


class IdeaSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = "idea_discovery_selection_request.v1"
    run_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    actor: str = ""
    reason: str = ""
    selection_request_ref: str = Field(min_length=1)
    proposal_ref: str = ""


class CandidateDecisionRecord(BaseModel):
    """Durable human decision over one evaluated Discovery candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["candidate_decision.v1"] = "candidate_decision.v1"
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    action: Literal["approve", "reject", "promote"]
    actor: str = Field(min_length=1)
    reason: str = ""
    idempotency_key: str = Field(min_length=1)
    status: Literal["applied"] = "applied"
    audit_ref: str = Field(min_length=1)
    candidate: CandidateRecord
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class HypothesisActionRecord(BaseModel):
    """Durable scientist-authored mutation of the run-local hypothesis pool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["idea_hypothesis_action.v1"] = "idea_hypothesis_action.v1"
    run_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    action: Literal["add", "edit", "reject"]
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    statement: str = ""
    idempotency_key: str = Field(min_length=1)
    status: Literal["applied"] = "applied"
    audit_ref: str = Field(min_length=1)
    hypothesis: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class _RunControl:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class _Context:
    run: RunHandle
    record: _ServiceRecord
    contract: ResearchTaskContract
    stores: DiscoveryStores
    events: DiscoveryEventSink


@dataclass(frozen=True)
class _CandidateSelection:
    candidate_id: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class _StatisticalPromotionResult:
    passed: bool
    payload: dict[str, Any]


class DiscoveryService:
    """The only write path for a Discovery run and its iteration children."""

    def __init__(
        self,
        *,
        run_store: RunStore,
        event_bus: EventBus,
        candidate_agent: DiscoveryCandidateAgent,
        adapter: ProjectAdapter,
        code_candidate_preparer: CodeCandidateWorkspacePreparer | None = None,
        core: DiscoveryCore | None = None,
        store_factory: DiscoveryStoreFactory | None = None,
    ) -> None:
        self.run_store = run_store
        self.event_bus = event_bus
        self.candidate_agent = candidate_agent
        self.adapter = adapter
        self.code_candidate_preparer = code_candidate_preparer
        self.core = core or DefaultDiscoveryCore()
        self.store_factory = store_factory or FilesystemDiscoveryStoreFactory()
        self._controls: dict[str, _RunControl] = {}
        self._create_lock = asyncio.Lock()

    async def create(
        self,
        spec: DiscoveryRunSpec,
        *,
        idempotency_key: str,
    ) -> DiscoveryRunView:
        if not idempotency_key.strip():
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_CONTRACT,
                "idempotency_key is required",
                status_code=422,
            )
        async with self._create_lock:
            existing = self._find_create(idempotency_key)
            if existing is not None:
                if existing.record.spec != spec:
                    raise DiscoveryServiceError(
                        DiscoveryErrorCode.IDEMPOTENCY_CONFLICT,
                        "create idempotency key is already bound to another request",
                    )
                return self.status(existing.run.run_id)

            run = self.run_store.create(
                task=spec.task,
                project=spec.project,
                entrypoint="model_discovery",
                user_request=spec.objective,
            )
            contract = spec.contract(run.run_id)
            record = _ServiceRecord(
                run_id=run.run_id,
                create_idempotency_key=idempotency_key,
                spec=spec,
            )
            paths = DiscoveryPaths(run_root=run.root, run_id=run.run_id)
            with discovery_lock(paths):
                atomic_write_json(paths.root / "service.json", model_payload(record))
                atomic_write_json(paths.root / "contract.json", model_payload(contract))
            stores = self.store_factory.build(run, contract)
            progress = DiscoveryProgress()
            stores.checkpoints.save(
                phase="created",
                iteration=0,
                status=CheckpointStatus.PAUSED,
                state=model_payload(progress),
                idempotency_key=f"create:{idempotency_key}",
                reason="not_started",
            )
            events = DiscoveryEventSink(run.root, run_id=run.run_id, bus=self.event_bus)
            await events.emit(
                DiscoveryEventName.RUN_CREATED,
                payload={"project": spec.project, "idea_mode": spec.idea_mode},
            )
            return self.status(run.run_id)

    async def start(self, run_id: str, *, wait: bool = False) -> DiscoveryRunView:
        context = self._context(run_id)
        control = self._control(run_id)
        task: asyncio.Task[None] | None
        async with control.lock:
            latest = context.stores.checkpoints.latest()
            if latest is None:
                raise self._invalid_state("discovery checkpoint is missing")
            if latest.status in {CheckpointStatus.COMPLETED, CheckpointStatus.FAILED}:
                return self.status(run_id)
            if latest.status == CheckpointStatus.PAUSED:
                await self._require_adapter_ready(context)
                context.stores.checkpoints.resume()
                progress = DiscoveryProgress.model_validate(latest.state)
                if progress.lifecycle == DiscoveryLifecycle.CREATED:
                    progress = progress.model_copy(
                        update={"lifecycle": DiscoveryLifecycle.RUNNING}
                    )
                    context.stores.checkpoints.checkpoint(
                        phase="started",
                        iteration=progress.next_iteration,
                        state=model_payload(progress),
                        idempotency_key="run-started",
                    )
                control.gate.set()
                control.stop_requested.clear()
                await context.events.emit(DiscoveryEventName.RUN_STARTED)
            task = self._launch(context, control)
        if wait and task is not None:
            await task
        return self.status(run_id)

    def status(self, run_id: str) -> DiscoveryRunView:
        context = self._context(run_id)
        latest = context.stores.checkpoints.latest()
        if latest is None:
            raise self._invalid_state("discovery checkpoint is missing")
        progress = DiscoveryProgress.model_validate(latest.state)
        candidates = context.stores.candidates.list()
        evaluations = context.stores.candidates.list_evaluations()
        promotions = context.stores.promotions.list()
        return DiscoveryRunView(
            run_id=run_id,
            project=context.contract.project,
            objective=context.contract.objective,
            lifecycle=self._lifecycle(latest.status, progress),
            checkpoint_sequence=latest.sequence,
            next_iteration=progress.next_iteration,
            next_ordinal=progress.next_ordinal,
            candidate_count=len(candidates),
            evaluated_count=len(evaluations),
            failed_count=sum(item.status == CandidateStatus.FAILED for item in candidates),
            quarantined_count=sum(
                item.status == CandidateStatus.QUARANTINED for item in candidates
            ),
            promotion_pending_count=sum(
                item.state == PromotionTaskState.PENDING for item in promotions
            ),
            promotion_running_count=sum(
                item.state == PromotionTaskState.RUNNING for item in promotions
            ),
            promotion_completed_count=sum(
                item.state == PromotionTaskState.COMPLETED for item in promotions
            ),
            promotion_failed_count=sum(
                item.state == PromotionTaskState.FAILED for item in promotions
            ),
            promotion_cancelled_count=sum(
                item.state == PromotionTaskState.CANCELLED for item in promotions
            ),
            hitl_pending=progress.hitl_pending,
            selected_candidate_id=progress.selected_candidate_id,
            iteration_nodes=progress.iteration_nodes,
            budget=context.stores.budget.snapshot(),
            latest_archive=context.stores.archive.latest(),
            stop_reason=progress.stop_reason,
            stop_code=progress.stop_code,
            stop_details=progress.stop_details,
            selection_evidence=progress.selection_evidence,
        )

    async def pause(self, run_id: str, *, reason: str = "user_requested") -> DiscoveryRunView:
        context = self._context(run_id)
        control = self._control(run_id)
        async with control.lock:
            latest = context.stores.checkpoints.latest()
            if latest is None:
                raise self._invalid_state("discovery checkpoint is missing")
            if latest.status in {CheckpointStatus.COMPLETED, CheckpointStatus.FAILED}:
                return self.status(run_id)
            if latest.status != CheckpointStatus.PAUSED:
                control.gate.clear()
                context.stores.checkpoints.pause(reason=reason)
                await context.events.emit(
                    DiscoveryEventName.RUN_PAUSED,
                    payload={"reason": reason},
                )
        return self.status(run_id)

    async def resume(self, run_id: str, *, wait: bool = False) -> DiscoveryRunView:
        context = self._context(run_id)
        control = self._control(run_id)
        task: asyncio.Task[None] | None
        async with control.lock:
            latest = context.stores.checkpoints.latest()
            if latest is None:
                raise self._invalid_state("discovery checkpoint is missing")
            if latest.status in {CheckpointStatus.COMPLETED, CheckpointStatus.FAILED}:
                return self.status(run_id)

            live = control.task is not None and not control.task.done()
            if latest.status == CheckpointStatus.RUNNING and not live:
                latest = context.stores.checkpoints.recover()
                context.stores.candidates.recover()
                context.stores.promotions.recover()
                context.stores.budget.recover()
                context.stores.budget.recover_slots(active_lease_ids=set())
            if latest is not None and latest.status == CheckpointStatus.PAUSED:
                progress = DiscoveryProgress.model_validate(latest.state)
                context.stores.checkpoints.resume()
                if progress.hitl_pending and not progress.hitl_resolved:
                    progress = progress.model_copy(
                        update={"hitl_pending": False, "hitl_resolved": True}
                    )
                    context.stores.checkpoints.checkpoint(
                        phase="hitl_resolved",
                        iteration=progress.next_iteration,
                        state=model_payload(progress),
                        idempotency_key=f"hitl-resolved:{progress.next_iteration}",
                    )
                    await context.events.emit(DiscoveryEventName.HITL_RESOLVED)
                control.gate.set()
                control.stop_requested.clear()
                await context.events.emit(DiscoveryEventName.RUN_RESUMED)
            task = self._launch(context, control)
        if wait and task is not None:
            await task
        return self.status(run_id)

    async def stop(self, run_id: str, *, reason: str = "user_requested") -> DiscoveryRunView:
        context = self._context(run_id)
        control = self._control(run_id)
        async with control.lock:
            latest = context.stores.checkpoints.latest()
            if latest is None:
                raise self._invalid_state("discovery checkpoint is missing")
            if latest.status in {CheckpointStatus.COMPLETED, CheckpointStatus.FAILED}:
                return self.status(run_id)
            control.stop_requested.set()
            control.gate.set()
            context.stores.promotions.cancel_pending(reason=reason)
            progress = DiscoveryProgress.model_validate(latest.state).model_copy(
                update={
                    "lifecycle": DiscoveryLifecycle.STOPPED,
                    "stop_reason": reason,
                    "stop_code": StopReason.MANUAL.value,
                    "stop_details": (reason,),
                }
            )
            context.stores.checkpoints.complete(state=model_payload(progress))
            await context.events.emit(
                DiscoveryEventName.RUN_STOPPED,
                payload={"reason": reason},
            )
        return self.status(run_id)

    async def wait(self, run_id: str) -> DiscoveryRunView:
        control = self._control(run_id)
        if control.task is not None:
            await control.task
        return self.status(run_id)

    def replay(self, run_id: str) -> DiscoveryReplayView:
        context = self._context(run_id)
        return DiscoveryReplayView(
            run=self.status(run_id),
            checkpoints=tuple(context.stores.checkpoints.replay()),
            candidates=tuple(context.stores.candidates.list()),
            evaluations=tuple(context.stores.candidates.list_evaluations()),
            promotions=tuple(context.stores.promotions.list()),
            archives=tuple(context.stores.archive.replay()),
            events=tuple(model_payload(item) for item in context.events.replay()),
        )

    def idea_discovery(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        idea_dir = run.root / "idea" / "discovery"
        artifacts: dict[str, Any] = {}
        if idea_dir.exists():
            for path in sorted(idea_dir.rglob("*.json")):
                artifacts[str(path.relative_to(idea_dir))] = read_json(path)
        service_path = run.root / "discovery" / "service.json"
        request_options = self._run_request_extra(run)
        idea_mode = str(request_options.get("idea_mode") or "fast")
        if service_path.exists():
            idea_mode = _ServiceRecord.model_validate(read_json(service_path)).spec.idea_mode
        pool = self._artifact_mapping(artifacts, "hypothesis_pool.v1.json")
        state = self._artifact_mapping(artifacts, "state.v1.json")
        hypotheses = self._record_items(artifacts, "hypotheses.v1.json")
        reflections = self._record_items(artifacts, "reflections.v1.json")
        matches = self._record_items(artifacts, "pairwise_matches.v1.json")
        proximity_graphs = self._record_items(
            artifacts,
            "proximity_graphs.v1.json",
        )
        meta_reviews = self._record_items(artifacts, "meta_reviews.v1.json")
        selection = self._artifact_mapping(artifacts, "selection.v1.json")
        top_ids = self._string_items(pool.get("top_hypothesis_ids"))
        selected_id = str(
            selection.get("hypothesis_id")
            or pool.get("selected_hypothesis_id")
            or ""
        )
        proposal_ref = self._proposal_ref(run) if selection else ""
        round_index = max(
            (
                int(item.get("round_index", 0))
                for item in hypotheses
                if isinstance(item.get("round_index", 0), int)
            ),
            default=0,
        )
        status = str(pool.get("status") or state.get("status") or "unknown")
        if selection:
            status = "selected"
        return {
            "run_id": run_id,
            "idea_mode": idea_mode,
            "project": str(pool.get("project") or run.project),
            "status": status,
            "round_index": round_index,
            "backend_mode": str(state.get("backend_mode") or ""),
            "config": pool.get("config") if isinstance(pool.get("config"), dict) else {},
            "hypotheses": hypotheses,
            "reflections": reflections,
            "matches": matches,
            "pairwise_matches": matches,
            "proximity_graphs": proximity_graphs,
            "meta_reviews": meta_reviews,
            "finalist_ids": top_ids,
            "top_hypothesis_ids": top_ids,
            "selected_id": selected_id,
            "selected_hypothesis_id": selected_id,
            "proposal_ref": proposal_ref,
            "artifacts": artifacts,
            "selection": selection or None,
        }

    def idea_hypotheses(self, run_id: str) -> tuple[dict[str, Any], ...]:
        discovery = self.idea_discovery(run_id)
        found = {
            str(item["hypothesis_id"]): item
            for item in discovery["hypotheses"]
            if isinstance(item, dict) and item.get("hypothesis_id")
        }
        return tuple(found[key] for key in sorted(found))

    def add_idea_hypothesis(
        self,
        run_id: str,
        *,
        statement: str,
        actor: str,
        reason: str,
    ) -> HypothesisActionRecord:
        return self._mutate_idea_hypothesis(
            run_id,
            hypothesis_id="",
            action="add",
            statement=statement,
            actor=actor,
            reason=reason,
        )

    def edit_idea_hypothesis(
        self,
        run_id: str,
        hypothesis_id: str,
        *,
        statement: str,
        actor: str,
        reason: str,
    ) -> HypothesisActionRecord:
        return self._mutate_idea_hypothesis(
            run_id,
            hypothesis_id=hypothesis_id,
            action="edit",
            statement=statement,
            actor=actor,
            reason=reason,
        )

    def reject_idea_hypothesis(
        self,
        run_id: str,
        hypothesis_id: str,
        *,
        actor: str,
        reason: str,
    ) -> HypothesisActionRecord:
        return self._mutate_idea_hypothesis(
            run_id,
            hypothesis_id=hypothesis_id,
            action="reject",
            statement="",
            actor=actor,
            reason=reason,
        )

    def _mutate_idea_hypothesis(
        self,
        run_id: str,
        *,
        hypothesis_id: str,
        action: Literal["add", "edit", "reject"],
        statement: str,
        actor: str,
        reason: str,
    ) -> HypothesisActionRecord:
        actor = actor.strip()
        reason = reason.strip()
        statement = statement.strip()
        if not actor or not reason or (action in {"add", "edit"} and not statement):
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_CONTRACT,
                "actor, reason and a non-empty statement are required",
                status_code=422,
            )
        if action != "add" and not hypothesis_id.strip():
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_CONTRACT,
                "hypothesis_id is required",
                status_code=422,
            )
        run = self._require_run(run_id)
        idea_dir = run.root / "idea" / "discovery"
        pool_path = idea_dir / "hypothesis_pool.v1.json"
        hypotheses_path = idea_dir / "hypotheses.v1.json"
        state_path = idea_dir / "state.v1.json"
        if not pool_path.is_file() or not hypotheses_path.is_file():
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_STATE,
                "Idea deep discovery artifacts are not available",
                status_code=404,
            )
        idempotency_key = stable_key(
            "\n".join((run_id, hypothesis_id, action, actor, reason, statement))
        )
        relative_record = (
            Path("idea")
            / "discovery"
            / "hitl"
            / "hypothesis_actions"
            / f"{idempotency_key}.json"
        )
        record_path = run.root / relative_record
        paths = DiscoveryPaths(run_root=run.root, run_id=run_id)
        with discovery_lock(paths):
            if record_path.is_file():
                return HypothesisActionRecord.model_validate(read_json(record_path))
            pool = read_json(pool_path)
            wrapper = read_json(hypotheses_path)
            items_raw = wrapper.get("items")
            if not isinstance(items_raw, list):
                raise self._invalid_state("hypothesis record wrapper is invalid")
            items = [dict(item) for item in items_raw if isinstance(item, dict)]
            status = str(pool.get("status") or "")
            if status != "waiting_selection":
                raise self._invalid_state(
                    "hypotheses can only be changed while waiting for selection"
                )

            found_index = next(
                (
                    index
                    for index, item in enumerate(items)
                    if str(item.get("hypothesis_id") or "") == hypothesis_id
                ),
                None,
            )
            top_ids = list(self._string_items(pool.get("top_hypothesis_ids")))
            audit_ref = relative_record.as_posix()
            if action == "add":
                hypothesis_id = f"hypothesis_human_{idempotency_key[:16]}"
                found_index = next(
                    (
                        index
                        for index, item in enumerate(items)
                        if str(item.get("hypothesis_id") or "") == hypothesis_id
                    ),
                    None,
                )
                if found_index is None:
                    max_round = max(
                        (
                            int(item.get("round_index", 0))
                            for item in items
                            if isinstance(item.get("round_index", 0), int)
                        ),
                        default=0,
                    )
                    updated = {
                        "schema_id": "hypothesis.v1",
                        "hypothesis_id": hypothesis_id,
                        "run_id": run_id,
                        "round_index": max_round + 1,
                        "parent_ids": [],
                        "mechanism": "human_proposed",
                        "statement": statement,
                        "testable_predictions": [],
                        "evidence_refs": [audit_ref],
                        "constraints": [],
                        "uncertainty": (
                            "Human-proposed hypothesis has not been independently reranked."
                        ),
                        "operator": "human_add",
                        "cluster_id": "human-review",
                        "elo": 1000.0,
                        "blocked": False,
                        "created_at": datetime.now(tz=timezone.utc).isoformat(),
                    }
                    items.append(updated)
                else:
                    updated = items[found_index]
                config = pool.get("config")
                top_k = 3
                if isinstance(config, dict) and isinstance(config.get("top_k"), int):
                    top_k = max(1, int(config["top_k"]))
                top_ids = [hypothesis_id, *[item for item in top_ids if item != hypothesis_id]][
                    :top_k
                ]
            else:
                if found_index is None:
                    raise DiscoveryServiceError(
                        DiscoveryErrorCode.INVALID_CONTRACT,
                        f"unknown hypothesis: {hypothesis_id}",
                        status_code=404,
                    )
                current = items[found_index]
                if bool(current.get("blocked")):
                    raise self._invalid_state("blocked hypothesis cannot be changed")
                updated = dict(current)
                refs = list(self._string_items(updated.get("evidence_refs")))
                if audit_ref not in refs:
                    refs.append(audit_ref)
                updated["evidence_refs"] = refs
                if action == "edit":
                    updated["statement"] = statement
                    updated["operator"] = "human_edit"
                    updated["uncertainty"] = (
                        "Human-edited after ranking; inherited ranking is only an allocation hint."
                    )
                else:
                    updated["blocked"] = True
                    top_ids = [item for item in top_ids if item != hypothesis_id]
                items[found_index] = updated

            wrapper["count"] = len(items)
            wrapper["items"] = items
            legal_count = sum(not bool(item.get("blocked")) for item in items)
            pool["hypothesis_count"] = len(items)
            pool["legal_count"] = legal_count
            pool["top_hypothesis_ids"] = top_ids
            warning = f"human_{action}:{hypothesis_id}:{audit_ref}"
            warnings = list(self._string_items(pool.get("warnings")))
            if warning not in warnings:
                warnings.append(warning)
            pool["warnings"] = warnings
            atomic_write_json(hypotheses_path, wrapper)
            atomic_write_json(pool_path, pool)
            if state_path.is_file():
                state = read_json(state_path)
                state["hypotheses"] = items
                state["top_hypothesis_ids"] = top_ids
                state_warnings = list(self._string_items(state.get("warnings")))
                if warning not in state_warnings:
                    state_warnings.append(warning)
                state["warnings"] = state_warnings
                atomic_write_json(state_path, state)
            record = HypothesisActionRecord(
                run_id=run_id,
                hypothesis_id=hypothesis_id,
                action=action,
                actor=actor,
                reason=reason,
                statement=statement,
                idempotency_key=idempotency_key,
                audit_ref=audit_ref,
                hypothesis=updated,
            )
            atomic_write_json(record_path, model_payload(record))
            return record

    def select_idea_hypothesis(
        self,
        run_id: str,
        *,
        hypothesis_id: str,
        idempotency_key: str,
        actor: str = "",
        reason: str = "",
    ) -> IdeaSelectionRequest:
        if not hypothesis_id.strip() or not idempotency_key.strip():
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_CONTRACT,
                "hypothesis_id and idempotency_key are required",
                status_code=422,
            )
        run = self._require_run(run_id)
        hypotheses = self.idea_hypotheses(run_id)
        if hypotheses and hypothesis_id not in {
            str(item["hypothesis_id"]) for item in hypotheses
        }:
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_CONTRACT,
                f"unknown hypothesis: {hypothesis_id}",
                status_code=404,
            )
        relative_record = (
            Path("idea")
            / "discovery"
            / "selection_requests"
            / f"{stable_key(idempotency_key)}.json"
        )
        record_path = run.root / relative_record
        paths = DiscoveryPaths(run_root=run.root, run_id=run_id)
        with discovery_lock(paths):
            if record_path.exists():
                existing = IdeaSelectionRequest.model_validate(read_json(record_path))
                if (
                    existing.run_id != run_id
                    or existing.hypothesis_id != hypothesis_id
                    or existing.idempotency_key != idempotency_key
                    or existing.actor != actor
                    or existing.reason != reason
                    or existing.selection_request_ref != relative_record.as_posix()
                ):
                    raise DiscoveryServiceError(
                        DiscoveryErrorCode.IDEMPOTENCY_CONFLICT,
                        "selection idempotency key is already bound",
                    )
                return existing
            selection = IdeaSelectionRequest(
                run_id=run_id,
                hypothesis_id=hypothesis_id,
                idempotency_key=idempotency_key,
                actor=actor,
                reason=reason,
                selection_request_ref=relative_record.as_posix(),
                proposal_ref=self._proposal_ref(run),
            )
            atomic_write_json(record_path, model_payload(selection))
        return selection

    async def decide_candidate(
        self,
        run_id: str,
        candidate_id: str,
        *,
        action: Literal["approve", "reject", "promote"],
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> CandidateDecisionRecord:
        """Apply one auditable candidate decision while archive HITL is pending."""

        if not candidate_id.strip() or not actor.strip() or not idempotency_key.strip():
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_CONTRACT,
                "candidate_id, actor and idempotency_key are required",
                status_code=422,
            )
        context = self._context(run_id)
        control = self._control(run_id)
        relative_record = (
            Path("discovery")
            / "hitl"
            / "candidate_decisions"
            / f"{stable_key(idempotency_key)}.json"
        )
        record_path = context.run.root / relative_record
        async with control.lock:
            if record_path.exists():
                existing = CandidateDecisionRecord.model_validate(read_json(record_path))
                if (
                    existing.run_id != run_id
                    or existing.candidate_id != candidate_id
                    or existing.action != action
                    or existing.actor != actor
                    or existing.reason != reason
                    or existing.idempotency_key != idempotency_key
                ):
                    raise DiscoveryServiceError(
                        DiscoveryErrorCode.IDEMPOTENCY_CONFLICT,
                        "candidate decision idempotency key is already bound",
                    )
                return existing

            latest = context.stores.checkpoints.latest()
            if latest is None:
                raise self._invalid_state("discovery checkpoint is missing")
            progress = DiscoveryProgress.model_validate(latest.state)
            if latest.status != CheckpointStatus.PAUSED or not progress.hitl_pending:
                raise self._invalid_state(
                    "candidate decisions require a discovery run waiting for HITL"
                )
            candidate = context.stores.candidates.get(candidate_id)
            if candidate is None:
                raise DiscoveryServiceError(
                    DiscoveryErrorCode.INVALID_CONTRACT,
                    f"unknown candidate: {candidate_id}",
                    status_code=404,
                )

            allowed = {
                "approve": {
                    CandidateStatus.EVALUATED,
                    CandidateStatus.DOMINATED,
                    CandidateStatus.ELITE,
                    CandidateStatus.PROMOTED,
                },
                "promote": {
                    CandidateStatus.EVALUATED,
                    CandidateStatus.DOMINATED,
                    CandidateStatus.ELITE,
                    CandidateStatus.PROMOTED,
                },
                "reject": {
                    CandidateStatus.DRAFT,
                    CandidateStatus.VALIDATED,
                    CandidateStatus.QUEUED,
                    CandidateStatus.EVALUATED,
                    CandidateStatus.DOMINATED,
                    CandidateStatus.ELITE,
                    CandidateStatus.QUARANTINED,
                    CandidateStatus.REJECTED,
                    CandidateStatus.FAILED,
                },
            }[action]
            if candidate.status not in allowed:
                raise self._invalid_state(
                    f"cannot {action} candidate in {candidate.status.value} state"
                )

            if action == "promote":
                candidate = await self._transition(
                    context,
                    candidate_id,
                    CandidateStatus.PROMOTED,
                    reason=reason or "human promotion",
                )
            elif action == "reject":
                candidate = await self._transition(
                    context,
                    candidate_id,
                    CandidateStatus.REJECTED,
                    reason=reason or "human rejection",
                )

            selected_candidate_id = progress.selected_candidate_id
            if action in {"approve", "promote"}:
                selected_candidate_id = candidate_id
            elif selected_candidate_id == candidate_id:
                selected_candidate_id = ""
            progress = progress.model_copy(
                update={"selected_candidate_id": selected_candidate_id}
            )
            context.stores.checkpoints.save(
                phase="candidate_hitl_decision",
                iteration=progress.next_iteration,
                status=CheckpointStatus.PAUSED,
                state=model_payload(progress),
                idempotency_key=f"candidate-decision:{stable_key(idempotency_key)}",
                reason="waiting_hitl",
            )
            record = CandidateDecisionRecord(
                run_id=run_id,
                candidate_id=candidate_id,
                action=action,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
                audit_ref=relative_record.as_posix(),
                candidate=candidate,
            )
            atomic_write_json(record_path, model_payload(record))
            await context.events.emit(
                DiscoveryEventName.HITL_RESOLVED,
                candidate_id=candidate_id,
                payload={
                    "scope": "candidate",
                    "action": action,
                    "actor": actor,
                    "reason": reason,
                    "audit_ref": relative_record.as_posix(),
                },
            )
            return record

    def _launch(self, context: _Context, control: _RunControl) -> asyncio.Task[None]:
        if control.task is None or control.task.done():
            control.task = asyncio.create_task(self._run_loop(context, control))
        return control.task

    async def _run_loop(self, context: _Context, control: _RunControl) -> None:
        try:
            while True:
                await control.gate.wait()
                if control.stop_requested.is_set() or self._terminal(context):
                    return
                progress = self._progress(context)
                stop = self._stop_decision(context, progress)
                if stop.should_stop:
                    await self._handle_stop_decision(context, control, progress, stop)
                    return
                progress = await self._run_iteration(context, control, progress)
                await self._save_progress(
                    context,
                    control,
                    progress,
                    phase="iteration_completed",
                    key=f"iteration-completed:{progress.next_iteration}",
                )
                stop = self._stop_decision(context, progress)
                if stop.should_stop:
                    await self._handle_stop_decision(context, control, progress, stop)
                    return
        except BudgetExceededError as exc:
            await self._stop_for_budget(context, control, exc)
        except Exception as exc:
            await self._fail(context, control, exc)

    async def _run_iteration(
        self,
        context: _Context,
        control: _RunControl,
        progress: DiscoveryProgress,
    ) -> DiscoveryProgress:
        iteration = progress.next_iteration
        node, progress = self._ensure_iteration_child(context, progress, iteration)
        if progress.next_ordinal == 0:
            await context.events.emit(
                DiscoveryEventName.ITERATION_STARTED,
                iteration=iteration,
                child_run_id=node.child_run_id,
            )
            await self._save_progress(
                context,
                control,
                progress,
                phase="iteration_started",
                key=f"iteration-started:{iteration}",
            )

        for ordinal in range(
            progress.next_ordinal,
            context.record.spec.candidates_per_iteration,
        ):
            await control.gate.wait()
            if control.stop_requested.is_set() or self._terminal(context):
                return progress
            await self._process_candidate(context, node, iteration, ordinal)
            progress = progress.model_copy(update={"next_ordinal": ordinal + 1})
            await self._save_progress(
                context,
                control,
                progress,
                phase="candidate_completed",
                key=f"candidate-completed:{iteration}:{ordinal}",
            )

        await self._reconcile_promotion_tasks(context, node, iteration=iteration)
        await self._drain_promotion_tasks(
            context,
            control,
            node,
            iteration=iteration,
        )
        failed_promotions = context.stores.promotions.list(
            state=PromotionTaskState.FAILED
        )
        if (
            failed_promotions
            and context.contract.promotion_policy.get(
                "fail_run_on_promotion_failure",
                True,
            )
            is True
        ):
            failed_ids = ", ".join(task.task_id for task in failed_promotions[:5])
            raise RuntimeError(
                "promotion tasks exhausted their retry budget: " + failed_ids
            )

        snapshot = self.core.archive(
            contract=context.contract,
            evaluations=self._search_feedback_evaluations(context),
            iteration=iteration,
            budget=context.stores.budget.snapshot(),
            quarantined_candidate_ids=tuple(
                item.candidate_id
                for item in context.stores.candidates.list()
                if item.status == CandidateStatus.QUARANTINED
            ),
        )
        snapshot = context.stores.archive.put(snapshot)
        await self._apply_archive_states(context, snapshot.pareto_candidate_ids)
        await context.events.emit(
            DiscoveryEventName.ARCHIVE_UPDATED,
            iteration=iteration,
            child_run_id=node.child_run_id,
            payload={"snapshot_id": snapshot.snapshot_id},
        )
        await context.events.emit(
            DiscoveryEventName.ITERATION_COMPLETED,
            iteration=iteration,
            child_run_id=node.child_run_id,
        )
        nodes = tuple(
            item.model_copy(update={"status": "completed"})
            if item.child_run_id == node.child_run_id
            else item
            for item in progress.iteration_nodes
        )
        return progress.model_copy(
            update={
                "next_iteration": iteration + 1,
                "next_ordinal": 0,
                "iteration_nodes": nodes,
            }
        )

    async def _process_candidate(
        self,
        context: _Context,
        node: IterationNode,
        iteration: int,
        ordinal: int,
    ) -> None:
        proposal_tx = BudgetTransaction(
            transaction_id=f"proposal-{iteration:04d}-{ordinal:04d}",
            run_id=context.run.run_id,
            idempotency_key=f"proposal:{iteration}:{ordinal}",
            proposals=1,
        )
        before = context.stores.budget.snapshot().used
        context.stores.budget.charge(proposal_tx)
        after = context.stores.budget.snapshot().used
        if before != after:
            await context.events.emit(
                DiscoveryEventName.BUDGET_DEBITED,
                iteration=iteration,
                child_run_id=node.child_run_id,
                payload={"transaction_id": proposal_tx.transaction_id, "proposals": 1},
            )

        parents: tuple[str, ...] = ()
        archive = context.stores.archive.latest()
        if archive is not None:
            parents = archive.pareto_candidate_ids
        search_state = self._rebuild_search_state(context)
        parent_set = set(parents)
        parent_signals = tuple(
            ParentCandidateSignal(
                candidate_id=item.candidate_id,
                quality=item.quality,
                scarcity=1.0 / (1.0 + item.offspring_count),
                uncertainty=1.0 / math.sqrt(item.evaluation_count),
                recency=float(item.evaluated_iteration + 1),
                offspring_count=item.offspring_count,
            )
            for item in search_state.candidates
            if item.candidate_id in parent_set
        )
        try:
            candidate = await self.candidate_agent.propose(
                CandidateProposalRequest(
                    contract=context.contract,
                    iteration=iteration,
                    ordinal=ordinal,
                    child_run_id=node.child_run_id,
                    parent_candidate_ids=parents,
                    parent_candidates=parent_signals,
                    model_arms=tuple(
                        BanditArmSignal(
                            arm_id=item.arm_id,
                            pulls=item.pulls,
                            total_reward=item.total_reward,
                        )
                        for item in search_state.model_arms
                    ),
                    operator_arms=tuple(
                        BanditArmSignal(
                            arm_id=item.arm_id,
                            pulls=item.pulls,
                            total_reward=item.total_reward,
                        )
                        for item in search_state.operator_arms
                    ),
                )
            )
            candidate = candidate.model_copy(
                update={
                    "metadata": {
                        **candidate.metadata,
                        "discovery_iteration": iteration,
                        "discovery_ordinal": ordinal,
                    }
                }
            )
            self._validate_candidate(candidate, context, iteration)
            existing = context.stores.candidates.get(candidate.candidate_id)
            candidate = existing or context.stores.candidates.put(candidate)
            context.stores.lineage.put_candidate(candidate)
            await context.events.emit(
                DiscoveryEventName.CANDIDATE_CREATED,
                iteration=iteration,
                child_run_id=node.child_run_id,
                candidate_id=candidate.candidate_id,
            )
            await self._evaluate_candidate(context, node, candidate, iteration, ordinal)
        except BudgetExceededError:
            raise
        except Exception as exc:
            candidate_id = locals().get("candidate")
            persisted_id = candidate_id.candidate_id if isinstance(candidate_id, CandidateRecord) else ""
            if persisted_id and context.stores.candidates.get(persisted_id) is not None:
                await self._transition(
                    context,
                    persisted_id,
                    CandidateStatus.FAILED,
                    reason=str(exc),
                    iteration=iteration,
                    child_run_id=node.child_run_id,
                )
            await context.events.emit(
                DiscoveryEventName.CANDIDATE_QUARANTINED,
                iteration=iteration,
                child_run_id=node.child_run_id,
                candidate_id=persisted_id,
                error_code=DiscoveryErrorCode.CANDIDATE_QUARANTINED,
                payload={"reason": str(exc)},
            )

    async def _evaluate_candidate(
        self,
        context: _Context,
        node: IterationNode,
        candidate: CandidateRecord,
        iteration: int,
        ordinal: int,
    ) -> None:
        current = context.stores.candidates.get(candidate.candidate_id)
        if current is None:
            return
        fidelity = FidelityLevel(
            str(
                context.record.spec.project_inputs.get("fidelity")
                or FidelityLevel.F0.value
            )
        )
        evaluation_seeds = self._base_evaluation_seeds(
            context,
            iteration=iteration,
            ordinal=ordinal,
        )
        if current.status in {
            CandidateStatus.ELITE,
            CandidateStatus.DOMINATED,
            CandidateStatus.EVALUATED,
            CandidateStatus.PROMOTED,
        }:
            evaluations = context.stores.candidates.list_evaluations(
                candidate_id=current.candidate_id
            )
            for existing_evaluation in evaluations:
                await self._charge_evaluation_resources(
                    context,
                    current,
                    existing_evaluation,
                    iteration,
                )
                await self._apply_promotion_policy(
                    context,
                    node,
                    existing_evaluation,
                    iteration=iteration,
                )
            completed_seeds = {
                item.seed for item in evaluations if item.fidelity == fidelity
            }
            missing_seeds = tuple(
                seed for seed in evaluation_seeds if seed not in completed_seeds
            )
            if missing_seeds and current.status != CandidateStatus.PROMOTED:
                await self._evaluate_seed_batch(
                    context,
                    node,
                    current,
                    iteration=iteration,
                    ordinal=ordinal,
                    fidelity=fidelity,
                    seeds=missing_seeds,
                    transition_running=False,
                )
            return
        if current.status in {
            CandidateStatus.FAILED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
        }:
            return
        if current.status == CandidateStatus.DRAFT:
            code_preflight_failure = await self._prepare_code_candidate(
                context,
                current,
            )
            if code_preflight_failure is not None:
                await self._quarantine(
                    context,
                    node,
                    current,
                    iteration,
                    code_preflight_failure,
                )
                return
            report = self.core.preflight(current, context.contract)
            if not report.passed:
                reason = "; ".join(item.reason for item in report.blockers)
                await self._quarantine(context, node, current, iteration, reason)
                return
            current = await self._transition(
                context,
                current.candidate_id,
                CandidateStatus.VALIDATED,
                iteration=iteration,
                child_run_id=node.child_run_id,
            )
        if current.status == CandidateStatus.VALIDATED:
            current = await self._transition(
                context,
                current.candidate_id,
                CandidateStatus.QUEUED,
                iteration=iteration,
                child_run_id=node.child_run_id,
            )
        await self._evaluate_seed_batch(
            context,
            node,
            current,
            iteration=iteration,
            ordinal=ordinal,
            fidelity=fidelity,
            seeds=evaluation_seeds,
            transition_running=True,
        )

    async def _prepare_code_candidate(
        self,
        context: _Context,
        candidate: CandidateRecord,
    ) -> str | None:
        """Opt in only complete code artifacts; otherwise preserve config flow."""

        refs = candidate.artifact_refs
        spec_declared = _CODE_CANDIDATE_SPEC_REF in refs
        bundle_declared = _CODE_MATERIALIZATION_BUNDLE_REF in refs
        if not spec_declared and not bundle_declared:
            return None
        if spec_declared != bundle_declared:
            return (
                "code candidate must declare both code_candidate_spec and "
                "code_materialization_bundle artifact refs"
            )
        if self.code_candidate_preparer is None:
            return "secure code candidate workspace preparer is not configured"

        try:
            code_spec = CodeCandidateSpec.model_validate(
                read_json(
                    _run_local_artifact_path(
                        context.run.root,
                        refs[_CODE_CANDIDATE_SPEC_REF],
                    )
                )
            )
            bundle = CodeMaterializationBundle.model_validate(
                read_json(
                    _run_local_artifact_path(
                        context.run.root,
                        refs[_CODE_MATERIALIZATION_BUNDLE_REF],
                    )
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            return f"invalid code candidate artifacts: {exc}"

        try:
            await self.code_candidate_preparer.prepare(
                run=context.run,
                contract=context.contract,
                candidate=candidate,
                code_spec=code_spec,
                bundle=bundle,
            )
        except Exception as exc:
            return f"secure code candidate preflight failed: {exc}"
        return None

    async def _evaluate_seed_batch(
        self,
        context: _Context,
        node: IterationNode,
        candidate: CandidateRecord,
        *,
        iteration: int,
        ordinal: int,
        fidelity: FidelityLevel,
        seeds: tuple[int, ...],
        transition_running: bool,
    ) -> None:
        if not seeds:
            return
        lease_id = f"evaluation-{iteration:04d}-{ordinal:04d}"
        context.stores.budget.acquire_slot(
            lease_id=lease_id,
            candidate_id=candidate.candidate_id,
        )
        try:
            current = candidate
            if transition_running:
                current = await self._transition(
                    context,
                    candidate.candidate_id,
                    CandidateStatus.RUNNING,
                    iteration=iteration,
                    child_run_id=node.child_run_id,
                )
            explicit_seed_set = bool(context.record.spec.evaluation_seeds)
            for seed in seeds:
                execution_key = (
                    f"base-{iteration}-{ordinal}-seed-{seed}"
                    if explicit_seed_set
                    else ""
                )

                def seed_request(action: AdapterAction) -> AdapterRequest:
                    return self._adapter_request(
                        context,
                        node,
                        current,
                        action=action,
                        iteration=iteration,
                        ordinal=ordinal,
                        fidelity=fidelity,
                        seed=seed,
                        execution_key=execution_key,
                    )

                preflight_request = seed_request(AdapterAction.PREFLIGHT)
                preflight = await self.adapter.invoke(preflight_request)
                await self._charge_adapter_action_resources(
                    context,
                    current,
                    preflight_request,
                    preflight,
                    iteration,
                )
                if preflight.status not in {"ready", "ok"}:
                    await self._quarantine(
                        context,
                        node,
                        current,
                        iteration,
                        preflight.error or "adapter preflight rejected candidate",
                    )
                    return
                execute_request = seed_request(AdapterAction.EXECUTE)
                execute = await self.adapter.invoke(execute_request)
                await self._charge_adapter_action_resources(
                    context,
                    current,
                    execute_request,
                    execute,
                    iteration,
                )
                if execute.status != "ok":
                    await self._transition(
                        context,
                        current.candidate_id,
                        CandidateStatus.FAILED,
                        reason=execute.error or "adapter execution failed",
                        iteration=iteration,
                        child_run_id=node.child_run_id,
                    )
                    return
                evaluation_request = seed_request(AdapterAction.EVALUATE)
                response = await self.adapter.invoke(evaluation_request)
                if response.status != "ok":
                    await self._charge_adapter_action_resources(
                        context,
                        current,
                        evaluation_request,
                        response,
                        iteration,
                    )
                    await self._transition(
                        context,
                        current.candidate_id,
                        CandidateStatus.FAILED,
                        reason=response.error or "adapter evaluation failed",
                        iteration=iteration,
                        child_run_id=node.child_run_id,
                    )
                    return
                evaluation = self.core.evaluate(
                    candidate=current,
                    contract=context.contract,
                    response=response,
                    fidelity=fidelity,
                    seed=seed,
                )
                stored = context.stores.candidates.record_evaluation(evaluation)
                await self._charge_evaluation_resources(
                    context,
                    current,
                    stored,
                    iteration,
                )
                promotion = await self._apply_promotion_policy(
                    context,
                    node,
                    stored,
                    iteration=iteration,
                )
                self._rebuild_search_state(context)
                await context.events.emit(
                    DiscoveryEventName.CANDIDATE_EVALUATED,
                    iteration=iteration,
                    child_run_id=node.child_run_id,
                    candidate_id=current.candidate_id,
                    payload={
                        "evaluation_id": stored.evaluation_id,
                        "seed": stored.seed,
                        "promotion": promotion,
                    },
                )
        finally:
            context.stores.budget.release_slot(lease_id)

    def _base_evaluation_seeds(
        self,
        context: _Context,
        *,
        iteration: int,
        ordinal: int,
    ) -> tuple[int, ...]:
        configured = context.record.spec.evaluation_seeds
        if configured:
            return configured
        return (context.contract.seed + iteration * 10_000 + ordinal,)

    async def _charge_evaluation_resources(
        self,
        context: _Context,
        candidate: CandidateRecord,
        evaluation: CandidateEvaluation,
        iteration: int,
    ) -> None:
        usage = evaluation.resource_usage
        transaction_key = stable_key(evaluation.evaluation_id)[:24]
        transaction = BudgetTransaction(
            transaction_id=f"evaluation-resources-{transaction_key}",
            run_id=context.run.run_id,
            candidate_id=candidate.candidate_id,
            idempotency_key=f"evaluation-resources:{evaluation.evaluation_id}",
            llm_tokens=int(usage.get("llm_tokens", 0.0)),
            gpu_seconds=usage.get("gpu_seconds", 0.0),
            wall_seconds=usage.get("wall_seconds", 0.0),
            api_cost=usage.get("api_cost", 0.0),
        )
        before = context.stores.budget.snapshot().used
        context.stores.budget.charge(transaction)
        after = context.stores.budget.snapshot().used
        if before != after:
            await context.events.emit(
                DiscoveryEventName.BUDGET_DEBITED,
                iteration=iteration,
                candidate_id=candidate.candidate_id,
                payload={
                    "transaction_id": transaction.transaction_id,
                    "evaluation_id": evaluation.evaluation_id,
                    "fidelity": evaluation.fidelity.value,
                },
            )

    async def _charge_adapter_action_resources(
        self,
        context: _Context,
        candidate: CandidateRecord | None,
        request: AdapterRequest,
        response: AdapterResponse,
        iteration: int,
    ) -> None:
        usage = response.resource_usage
        llm_tokens = int(usage.get("llm_tokens", 0.0))
        gpu_seconds = usage.get("gpu_seconds", 0.0)
        wall_seconds = usage.get("wall_seconds", 0.0)
        api_cost = usage.get("api_cost", 0.0)
        if not any((llm_tokens, gpu_seconds, wall_seconds, api_cost)):
            return
        transaction_key = stable_key(request.request_id)[:24]
        transaction = BudgetTransaction(
            transaction_id=f"adapter-action-resources-{transaction_key}",
            run_id=context.run.run_id,
            candidate_id=candidate.candidate_id if candidate is not None else "",
            idempotency_key=f"adapter-action-resources:{request.request_id}",
            llm_tokens=llm_tokens,
            gpu_seconds=gpu_seconds,
            wall_seconds=wall_seconds,
            api_cost=api_cost,
        )
        before = context.stores.budget.snapshot().used
        context.stores.budget.charge(transaction)
        after = context.stores.budget.snapshot().used
        if before != after:
            await context.events.emit(
                DiscoveryEventName.BUDGET_DEBITED,
                iteration=iteration,
                candidate_id=candidate.candidate_id if candidate is not None else "",
                payload={
                    "transaction_id": transaction.transaction_id,
                    "adapter_request_id": request.request_id,
                    "action": request.action.value,
                    "fidelity": request.fidelity,
                    "seed": request.seed,
                },
            )

    async def _apply_promotion_policy(
        self,
        context: _Context,
        node: IterationNode,
        evaluation: CandidateEvaluation,
        *,
        iteration: int,
    ) -> dict[str, Any]:
        policy = context.contract.promotion_policy
        if policy.get("enabled") is not True:
            return {"enabled": False}
        schedule_next = policy.get("schedule_next_fidelity") is True
        if schedule_next and policy.get("mark_promoted") is True:
            raise ValueError(
                "promotion_policy.schedule_next_fidelity and mark_promoted "
                "cannot both be true"
            )
        raw_thresholds = policy.get("thresholds")
        if raw_thresholds is not None and not isinstance(raw_thresholds, dict):
            raise ValueError("promotion_policy.thresholds must be an object")
        thresholds = {
            str(name): _finite_number(value, f"promotion threshold '{name}'")
            for name, value in (raw_thresholds or {}).items()
        }
        decision = self._absolute_promotion_decision(
            context,
            evaluation,
            thresholds=thresholds,
        )
        statistical_passed, statistical_payload, statistical_reasons = (
            self._statistical_promotion_gate(
                context,
                evaluation,
                policy=policy,
            )
        )
        promote = decision.promote and statistical_passed
        payload: dict[str, Any] = {
            "enabled": True,
            "promote": promote,
            "eligible_for_next_fidelity": promote,
            "current_fidelity": decision.current_fidelity.value,
            "next_fidelity": (
                decision.next_fidelity.value
                if decision.next_fidelity is not None
                else ""
            ),
            "reasons": (*decision.reasons, *statistical_reasons),
            "statistical_gate": statistical_payload,
            "schedule_next_fidelity": schedule_next,
            "scheduled": False,
        }
        if promote and schedule_next and decision.next_fidelity is not None:
            maximum_fidelity = _automatic_maximum_fidelity(policy)
            payload["maximum_fidelity"] = maximum_fidelity.value
            candidate = context.stores.candidates.get(evaluation.candidate_id)
            if candidate is not None and candidate.status == CandidateStatus.PROMOTED:
                payload["schedule_reason"] = "candidate_is_terminally_promoted"
            elif _fidelity_rank(decision.next_fidelity) > _fidelity_rank(
                maximum_fidelity
            ):
                payload["schedule_reason"] = "automatic_fidelity_limit_reached"
            else:
                task, created = await self._enqueue_promotion_task(
                    context,
                    node,
                    evaluation,
                    target_fidelity=decision.next_fidelity,
                    aggregate_refs=tuple(
                        str(item)
                        for item in statistical_payload.get("aggregate_refs", ())
                    ),
                    iteration=iteration,
                )
                payload.update(
                    {
                        "scheduled": True,
                        "promotion_task_id": task.task_id,
                        "promotion_task_state": task.state.value,
                        "promotion_task_created": created,
                    }
                )
        # PROMOTED remains a terminal compatibility marker. Automated
        # scheduling uses PromotionTask state and leaves the candidate usable.
        if promote and not schedule_next and policy.get("mark_promoted") is True:
            candidate = context.stores.candidates.get(evaluation.candidate_id)
            if candidate is None:
                raise FileNotFoundError(evaluation.candidate_id)
            if candidate.status != CandidateStatus.PROMOTED:
                await self._transition(
                    context,
                    evaluation.candidate_id,
                    CandidateStatus.PROMOTED,
                    reason=f"eligible for {payload['next_fidelity']}",
                    iteration=iteration,
                    child_run_id=node.child_run_id,
                )
            payload["status_applied"] = CandidateStatus.PROMOTED.value
        return payload

    def _absolute_promotion_decision(
        self,
        context: _Context,
        evaluation: CandidateEvaluation,
        *,
        thresholds: dict[str, float],
    ) -> PromotionDecision:
        expected_seeds = context.record.spec.evaluation_seeds
        if not expected_seeds:
            return decide_promotion(
                evaluation,
                context.contract.objectives,
                thresholds=thresholds,
            )
        cohort = tuple(
            item
            for item in context.stores.candidates.list_evaluations(
                candidate_id=evaluation.candidate_id
            )
            if item.fidelity == evaluation.fidelity
            and item.evaluator_hash == evaluation.evaluator_hash
            and item.dataset_hash == evaluation.dataset_hash
        )
        observed_seeds = tuple(sorted(item.seed for item in cohort))
        if not expected_seeds or observed_seeds != tuple(sorted(expected_seeds)):
            return PromotionDecision(
                promote=False,
                current_fidelity=evaluation.fidelity,
                next_fidelity=None,
                reasons=("configured shared seed cohort is incomplete",),
            )
        decisions = tuple(
            decide_promotion(
                item,
                context.contract.objectives,
                thresholds=thresholds,
            )
            for item in sorted(cohort, key=lambda item: item.seed)
        )
        reasons = tuple(
            f"seed {item.seed}: {reason}"
            for item, decision in zip(
                sorted(cohort, key=lambda item: item.seed),
                decisions,
                strict=True,
            )
            for reason in decision.reasons
        )
        return PromotionDecision(
            promote=not reasons,
            current_fidelity=evaluation.fidelity,
            next_fidelity=(
                _next_fidelity(evaluation.fidelity) if not reasons else None
            ),
            reasons=reasons,
        )

    async def _enqueue_promotion_task(
        self,
        context: _Context,
        node: IterationNode,
        evaluation: CandidateEvaluation,
        *,
        target_fidelity: FidelityLevel,
        aggregate_refs: tuple[str, ...],
        iteration: int,
        purpose: Literal["candidate_promotion", "statistical_baseline"] = (
            "candidate_promotion"
        ),
    ) -> tuple[PromotionTask, bool]:
        identity = "|".join(
            (
                context.run.run_id,
                evaluation.candidate_id,
                evaluation.evaluation_id,
                evaluation.fidelity.value,
                target_fidelity.value,
                str(evaluation.seed),
                evaluation.evaluator_hash,
                evaluation.dataset_hash,
                purpose,
            )
        )
        digest = stable_key(identity)[:24]
        task = PromotionTask(
            task_id=f"promotion-{digest}",
            run_id=context.run.run_id,
            candidate_id=evaluation.candidate_id,
            source_evaluation_id=evaluation.evaluation_id,
            from_fidelity=evaluation.fidelity,
            to_fidelity=target_fidelity,
            seed=evaluation.seed,
            evaluator_hash=evaluation.evaluator_hash,
            dataset_hash=evaluation.dataset_hash,
            purpose=purpose,
            aggregate_refs=aggregate_refs,
            policy_hash=payload_hash(
                {"promotion_policy": context.contract.promotion_policy}
            ),
            idempotency_key=f"promotion:{digest}",
            max_attempts=_promotion_max_attempts(
                context.contract.promotion_policy
            ),
        )
        stored, created = context.stores.promotions.enqueue(task)
        if created:
            await context.events.emit(
                DiscoveryEventName.PROMOTION_ENQUEUED,
                iteration=iteration,
                child_run_id=node.child_run_id,
                candidate_id=evaluation.candidate_id,
                payload={
                    "promotion_task_id": stored.task_id,
                    "source_evaluation_id": evaluation.evaluation_id,
                    "from_fidelity": evaluation.fidelity.value,
                    "to_fidelity": target_fidelity.value,
                    "seed": evaluation.seed,
                    "purpose": stored.purpose,
                },
            )
        return stored, created

    async def _reconcile_promotion_tasks(
        self,
        context: _Context,
        node: IterationNode,
        *,
        iteration: int,
    ) -> None:
        policy = context.contract.promotion_policy
        if (
            policy.get("enabled") is not True
            or policy.get("schedule_next_fidelity") is not True
        ):
            return
        for evaluation in sorted(
            self._search_feedback_evaluations(context),
            key=lambda item: (
                _fidelity_rank(item.fidelity),
                item.candidate_id,
                item.seed,
                item.evaluation_id,
            ),
        ):
            await self._apply_promotion_policy(
                context,
                node,
                evaluation,
                iteration=iteration,
            )
        await self._reconcile_statistical_baseline_tasks(
            context,
            node,
            iteration=iteration,
        )

    async def _reconcile_statistical_baseline_tasks(
        self,
        context: _Context,
        node: IterationNode,
        *,
        iteration: int,
    ) -> None:
        """Keep the paired-reference lane aligned through the final fidelity.

        A challenger evaluated at F1 needs the same baseline seeds at F1 before
        it can be considered for F2.  A final F2 baseline cohort is also needed
        to support an F2 scientific conclusion, but never schedules F3.  These
        tasks are reference measurements, not baseline self-promotion.
        """

        policy = context.contract.promotion_policy
        raw_gate = policy.get("statistical_gate")
        if not isinstance(raw_gate, dict) or raw_gate.get("enabled") is not True:
            return
        maximum_fidelity = _automatic_maximum_fidelity(policy)
        if maximum_fidelity == FidelityLevel.F1:
            return
        baseline_candidate_id, _, _, error = self._resolve_statistical_baseline(
            context,
            raw_gate,
        )
        if error or not baseline_candidate_id:
            return
        reference_ceiling = _fidelity_rank(maximum_fidelity)
        required_targets = {
            task.to_fidelity
            for task in context.stores.promotions.list()
            if task.purpose == "candidate_promotion"
            and task.state
            not in {PromotionTaskState.FAILED, PromotionTaskState.CANCELLED}
        }
        for evaluation in sorted(
            context.stores.candidates.list_evaluations(
                candidate_id=baseline_candidate_id
            ),
            key=lambda item: (
                _fidelity_rank(item.fidelity),
                item.seed,
                item.evaluation_id,
            ),
        ):
            target_fidelity = _next_fidelity(evaluation.fidelity)
            if target_fidelity is None:
                continue
            if _fidelity_rank(target_fidelity) > reference_ceiling:
                continue
            if target_fidelity not in required_targets:
                continue
            await self._enqueue_promotion_task(
                context,
                node,
                evaluation,
                target_fidelity=target_fidelity,
                aggregate_refs=(),
                iteration=iteration,
                purpose="statistical_baseline",
            )

    async def _drain_promotion_tasks(
        self,
        context: _Context,
        control: _RunControl,
        node: IterationNode,
        *,
        iteration: int,
    ) -> None:
        while True:
            # Reconcile after every completed reference/candidate task.  This
            # closes the ordering window where a challenger F1 result exists
            # before the paired baseline F1 result is durable.
            await self._reconcile_promotion_tasks(
                context,
                node,
                iteration=iteration,
            )
            pending = context.stores.promotions.list(
                state=PromotionTaskState.PENDING
            )
            if not pending:
                return
            pending = sorted(
                pending,
                key=lambda item: (
                    item.purpose != "statistical_baseline",
                    _fidelity_rank(item.to_fidelity),
                    item.created_at,
                    item.task_id,
                ),
            )
            await control.gate.wait()
            if control.stop_requested.is_set() or self._terminal(context):
                return
            await self._process_promotion_task(
                context,
                control,
                node,
                pending[0],
                iteration=iteration,
            )

    async def _process_promotion_task(
        self,
        context: _Context,
        control: _RunControl,
        node: IterationNode,
        task: PromotionTask,
        *,
        iteration: int,
    ) -> None:
        claimed = context.stores.promotions.transition(
            task.task_id,
            PromotionTaskState.RUNNING,
            expected_state=PromotionTaskState.PENDING,
        )
        await context.events.emit(
            DiscoveryEventName.PROMOTION_STARTED,
            iteration=iteration,
            child_run_id=node.child_run_id,
            candidate_id=task.candidate_id,
            payload={
                "promotion_task_id": task.task_id,
                "attempt": claimed.attempts,
                "to_fidelity": task.to_fidelity.value,
                "seed": task.seed,
                "purpose": task.purpose,
            },
        )
        candidate = context.stores.candidates.get(task.candidate_id)
        if candidate is None:
            await self._fail_promotion_task(
                context,
                node,
                claimed,
                iteration=iteration,
                reason="promotion candidate is missing",
            )
            return
        if candidate.status in {
            CandidateStatus.PROMOTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
            CandidateStatus.FAILED,
        }:
            context.stores.promotions.transition(
                task.task_id,
                PromotionTaskState.CANCELLED,
                expected_state=PromotionTaskState.RUNNING,
                last_error=f"candidate status is {candidate.status.value}",
            )
            return

        lease_id = f"promotion-{stable_key(task.task_id)[:24]}"
        acquired = False
        try:
            context.stores.budget.acquire_slot(
                lease_id=lease_id,
                candidate_id=candidate.candidate_id,
            )
            acquired = True
            stored = self._completed_promotion_evaluation(context, task)
            if stored is None:
                preflight_request = self._adapter_request(
                    context,
                    node,
                    candidate,
                    action=AdapterAction.PREFLIGHT,
                    iteration=iteration,
                    ordinal=0,
                    fidelity=task.to_fidelity,
                    seed=task.seed,
                    execution_key=task.task_id,
                )
                preflight = await self.adapter.invoke(preflight_request)
                await self._charge_adapter_action_resources(
                    context,
                    candidate,
                    preflight_request,
                    preflight,
                    iteration,
                )
                if preflight.status not in {"ready", "ok"}:
                    await self._fail_promotion_task(
                        context,
                        node,
                        claimed,
                        iteration=iteration,
                        reason=preflight.error
                        or "promotion adapter preflight rejected candidate",
                        retryable=_retryable_promotion_response(preflight),
                    )
                    return
                execute_request = self._adapter_request(
                    context,
                    node,
                    candidate,
                    action=AdapterAction.EXECUTE,
                    iteration=iteration,
                    ordinal=0,
                    fidelity=task.to_fidelity,
                    seed=task.seed,
                    execution_key=task.task_id,
                )
                execute = await self.adapter.invoke(execute_request)
                await self._charge_adapter_action_resources(
                    context,
                    candidate,
                    execute_request,
                    execute,
                    iteration,
                )
                if execute.status != "ok":
                    await self._fail_promotion_task(
                        context,
                        node,
                        claimed,
                        iteration=iteration,
                        reason=execute.error or "promotion adapter execution failed",
                        retryable=_retryable_promotion_response(execute),
                    )
                    return
                if control.stop_requested.is_set() or self._terminal(context):
                    context.stores.promotions.transition(
                        task.task_id,
                        PromotionTaskState.CANCELLED,
                        expected_state=PromotionTaskState.RUNNING,
                        last_error="run stopped before promotion evaluation",
                    )
                    return
                evaluation_request = self._adapter_request(
                    context,
                    node,
                    candidate,
                    action=AdapterAction.EVALUATE,
                    iteration=iteration,
                    ordinal=0,
                    fidelity=task.to_fidelity,
                    seed=task.seed,
                    execution_key=task.task_id,
                )
                response = await self.adapter.invoke(evaluation_request)
                if response.status != "ok":
                    await self._charge_adapter_action_resources(
                        context,
                        candidate,
                        evaluation_request,
                        response,
                        iteration,
                    )
                    await self._fail_promotion_task(
                        context,
                        node,
                        claimed,
                        iteration=iteration,
                        reason=response.error or "promotion adapter evaluation failed",
                        retryable=_retryable_promotion_response(response),
                    )
                    return
                if control.stop_requested.is_set() or self._terminal(context):
                    context.stores.promotions.transition(
                        task.task_id,
                        PromotionTaskState.CANCELLED,
                        expected_state=PromotionTaskState.RUNNING,
                        last_error="run stopped before promotion evidence commit",
                    )
                    return
                evaluation = self.core.evaluate(
                    candidate=candidate,
                    contract=context.contract,
                    response=response,
                    fidelity=task.to_fidelity,
                    seed=task.seed,
                )
                stored = context.stores.candidates.record_evaluation(evaluation)

            await self._charge_evaluation_resources(
                context,
                candidate,
                stored,
                iteration,
            )
            if task.purpose == "statistical_baseline":
                promotion: dict[str, Any] = {
                    "enabled": True,
                    "reference_only": True,
                    "scheduled": False,
                    "purpose": task.purpose,
                }
            else:
                promotion = await self._apply_promotion_policy(
                    context,
                    node,
                    stored,
                    iteration=iteration,
                )
            completed = context.stores.promotions.transition(
                task.task_id,
                PromotionTaskState.COMPLETED,
                expected_state=PromotionTaskState.RUNNING,
                result_evaluation_id=stored.evaluation_id,
            )
            self._rebuild_search_state(context)
            await context.events.emit(
                DiscoveryEventName.CANDIDATE_EVALUATED,
                iteration=iteration,
                child_run_id=node.child_run_id,
                candidate_id=candidate.candidate_id,
                payload={
                    "evaluation_id": stored.evaluation_id,
                    "promotion_task_id": completed.task_id,
                    "promotion": promotion,
                },
            )
            await context.events.emit(
                DiscoveryEventName.PROMOTION_COMPLETED,
                iteration=iteration,
                child_run_id=node.child_run_id,
                candidate_id=candidate.candidate_id,
                payload={
                    "promotion_task_id": completed.task_id,
                    "evaluation_id": stored.evaluation_id,
                    "fidelity": stored.fidelity.value,
                    "seed": stored.seed,
                    "purpose": completed.purpose,
                },
            )
        except BudgetExceededError as exc:
            current_task = context.stores.promotions.get(task.task_id)
            if (
                current_task is not None
                and current_task.state == PromotionTaskState.RUNNING
            ):
                await self._fail_promotion_task(
                    context,
                    node,
                    current_task,
                    iteration=iteration,
                    reason=str(exc),
                )
            raise
        except Exception as exc:
            current_task = context.stores.promotions.get(task.task_id)
            if (
                current_task is not None
                and current_task.state == PromotionTaskState.RUNNING
            ):
                await self._fail_promotion_task(
                    context,
                    node,
                    current_task,
                    iteration=iteration,
                    reason=str(exc),
                )
        finally:
            if acquired:
                context.stores.budget.release_slot(lease_id)

    def _completed_promotion_evaluation(
        self,
        context: _Context,
        task: PromotionTask,
    ) -> CandidateEvaluation | None:
        return next(
            (
                evaluation
                for evaluation in context.stores.candidates.list_evaluations(
                    candidate_id=task.candidate_id
                )
                if evaluation.fidelity == task.to_fidelity
                and evaluation.seed == task.seed
                and evaluation.evaluator_hash == task.evaluator_hash
                and evaluation.dataset_hash == task.dataset_hash
            ),
            None,
        )

    async def _fail_promotion_task(
        self,
        context: _Context,
        node: IterationNode,
        task: PromotionTask,
        *,
        iteration: int,
        reason: str,
        retryable: bool = False,
    ) -> None:
        retry_scheduled = retryable and task.attempts < task.max_attempts
        target_state = (
            PromotionTaskState.PENDING
            if retry_scheduled
            else PromotionTaskState.FAILED
        )
        failed = context.stores.promotions.transition(
            task.task_id,
            target_state,
            expected_state=PromotionTaskState.RUNNING,
            last_error=reason[-4000:],
        )
        await context.events.emit(
            DiscoveryEventName.PROMOTION_FAILED,
            iteration=iteration,
            child_run_id=node.child_run_id,
            candidate_id=task.candidate_id,
            payload={
                "promotion_task_id": failed.task_id,
                "to_fidelity": failed.to_fidelity.value,
                "seed": failed.seed,
                "reason": failed.last_error,
                "state": failed.state.value,
                "attempt": task.attempts,
                "max_attempts": task.max_attempts,
                "retry_scheduled": retry_scheduled,
            },
        )

    def _resolve_statistical_baseline(
        self,
        context: _Context,
        raw_gate: dict[str, Any],
    ) -> tuple[str, dict[str, Any], str, str]:
        baseline_candidate_id = str(
            raw_gate.get("baseline_candidate_id") or ""
        ).strip()
        if baseline_candidate_id:
            return baseline_candidate_id, {}, "", ""
        try:
            baseline_ordinal = _optional_non_negative_integer(
                raw_gate.get("baseline_candidate_ordinal"),
                name="statistical_gate.baseline_candidate_ordinal",
            )
            baseline_iteration = _non_negative_integer(
                raw_gate.get("baseline_candidate_iteration"),
                default=0,
                name="statistical_gate.baseline_candidate_iteration",
            )
        except ValueError as exc:
            return "", {}, "baseline_selector_invalid", str(exc)
        if baseline_ordinal is None:
            return (
                "",
                {},
                "baseline_candidate_missing",
                "statistical_gate requires baseline_candidate_id or "
                "baseline_candidate_ordinal",
            )
        matches = tuple(
            candidate.candidate_id
            for candidate in context.stores.candidates.list()
            if candidate.metadata.get("discovery_iteration") == baseline_iteration
            and candidate.metadata.get("discovery_ordinal") == baseline_ordinal
        )
        if len(matches) != 1:
            return (
                "",
                {},
                "baseline_selector_unresolved",
                "baseline ordinal selector must resolve exactly one candidate",
            )
        return (
            matches[0],
            {
                "kind": "discovery_ordinal",
                "iteration": baseline_iteration,
                "ordinal": baseline_ordinal,
                "resolved_candidate_id": matches[0],
            },
            "",
            "",
        )

    def _statistical_promotion_gate(
        self,
        context: _Context,
        evaluation: CandidateEvaluation,
        *,
        policy: dict[str, Any],
    ) -> tuple[bool, dict[str, Any], tuple[str, ...]]:
        raw_gate = policy.get("statistical_gate")
        if raw_gate is None:
            return True, {"enabled": False}, ()
        if not isinstance(raw_gate, dict):
            return _statistical_gate_failure(
                "promotion_policy.statistical_gate must be an object"
            )
        if raw_gate.get("enabled") is not True:
            return True, {"enabled": False}, ()

        dataset_role = str(raw_gate.get("dataset_role") or "").strip().lower()
        if dataset_role == "holdout":
            return _statistical_gate_failure(
                "holdout evidence cannot drive promotion or search feedback",
                code="holdout_feedback_forbidden",
                dataset_role=dataset_role,
            )
        if dataset_role not in {"search", "train", "validation"}:
            return _statistical_gate_failure(
                "statistical_gate.dataset_role must be search, train, validation, or holdout",
                code="invalid_dataset_role",
                dataset_role=dataset_role,
            )

        (
            baseline_candidate_id,
            baseline_selector,
            resolution_code,
            resolution_error,
        ) = self._resolve_statistical_baseline(context, raw_gate)
        if resolution_error:
            return _statistical_gate_failure(
                resolution_error,
                code=resolution_code,
                dataset_role=dataset_role,
            )
        objective_name = str(raw_gate.get("objective_name") or "").strip()
        if baseline_candidate_id == evaluation.candidate_id:
            return _statistical_gate_failure(
                "candidate cannot be its own statistical baseline",
                code="baseline_candidate_matches_candidate",
                dataset_role=dataset_role,
            )
        objective = next(
            (
                item
                for item in context.contract.objectives
                if item.name == objective_name
            ),
            None,
        )
        if objective is None:
            return _statistical_gate_failure(
                "statistical_gate.objective_name is not a contract objective",
                code="objective_not_found",
                dataset_role=dataset_role,
            )

        try:
            gate = StatisticalGate(
                minimum_pairs=_positive_integer(
                    raw_gate.get("minimum_pairs"),
                    default=3,
                    name="statistical_gate.minimum_pairs",
                ),
                minimum_mean_improvement=_finite_number_or_default(
                    raw_gate.get("minimum_mean_improvement"),
                    default=0.10,
                    name="statistical_gate.minimum_mean_improvement",
                ),
                minimum_ci95_lower_exclusive=_finite_number_or_default(
                    raw_gate.get("minimum_ci95_lower_exclusive"),
                    default=0.0,
                    name="statistical_gate.minimum_ci95_lower_exclusive",
                ),
                maximum_single_seed_degradation=_finite_number_or_default(
                    raw_gate.get("maximum_single_seed_degradation"),
                    default=0.05,
                    name="statistical_gate.maximum_single_seed_degradation",
                ),
            )
            aggregates = aggregate_candidate_vs_baseline(
                candidate_evaluations=tuple(
                    context.stores.candidates.list_evaluations(
                        candidate_id=evaluation.candidate_id
                    )
                ),
                baseline_evaluations=tuple(
                    context.stores.candidates.list_evaluations(
                        candidate_id=baseline_candidate_id
                    )
                ),
                objective=objective,
                gate=gate,
                dataset_role=cast(DatasetRole, dataset_role),
            )
        except ValueError as exc:
            return _statistical_gate_failure(
                str(exc),
                code="aggregate_invalid",
                dataset_role=dataset_role,
            )

        selected = next(
            (
                item
                for item in aggregates
                if item.fidelity == evaluation.fidelity
                and item.evaluator_hash == evaluation.evaluator_hash
                and item.dataset_hash == evaluation.dataset_hash
            ),
            None,
        )
        if selected is None:
            return _statistical_gate_failure(
                "no statistical aggregate matches the current evaluation cohort",
                code="current_cohort_missing",
                dataset_role=dataset_role,
            )

        # Bind a promotion task only to the immutable aggregate for its source
        # cohort.  Persisting every currently-visible fidelity would make the
        # task payload change when a later reference cohort arrives.
        aggregate_refs = self._persist_evaluation_aggregates(context, (selected,))
        expected_seeds = tuple(sorted(context.record.spec.evaluation_seeds))
        paired_seeds = tuple(sorted(pair.seed for pair in selected.pairs))
        if not expected_seeds or paired_seeds != expected_seeds:
            payload = {
                "enabled": True,
                "passed": False,
                "dataset_role": dataset_role,
                "baseline_candidate_id": baseline_candidate_id,
                "baseline_selector": baseline_selector,
                "objective_name": objective_name,
                "aggregate_refs": aggregate_refs,
                "selected": model_payload(selected),
                "reason_codes": ("configured_seed_cohort_incomplete",),
                "expected_seeds": expected_seeds,
                "paired_seeds": paired_seeds,
            }
            return (
                False,
                payload,
                ("statistical:configured_seed_cohort_incomplete",),
            )
        reason_codes = tuple(item.code for item in selected.reasons)
        payload = {
            "enabled": True,
            "passed": selected.gate_passed,
            "dataset_role": dataset_role,
            "baseline_candidate_id": baseline_candidate_id,
            "baseline_selector": baseline_selector,
            "objective_name": objective_name,
            "aggregate_refs": aggregate_refs,
            "selected": model_payload(selected),
            "reason_codes": reason_codes,
        }
        return (
            selected.gate_passed,
            payload,
            tuple(f"statistical:{code}" for code in reason_codes),
        )

    def _persist_evaluation_aggregates(
        self,
        context: _Context,
        aggregates: tuple[EvaluationAggregate, ...],
    ) -> tuple[str, ...]:
        paths = DiscoveryPaths(run_root=context.run.root, run_id=context.run.run_id)
        references: list[str] = []
        with discovery_lock(paths):
            for aggregate in aggregates:
                identity = "|".join(
                    (
                        aggregate.candidate_id,
                        aggregate.baseline_candidate_id,
                        aggregate.objective_name,
                        aggregate.fidelity.value,
                        aggregate.evaluator_hash,
                        aggregate.dataset_hash,
                    )
                )
                aggregate_payload = model_payload(aggregate)
                content_hash = payload_hash(aggregate_payload)
                path = (
                    paths.root
                    / "evaluation_aggregates"
                    / f"{stable_key(f'{identity}|{content_hash}')}.json"
                )
                atomic_write_json(path, aggregate_payload)
                references.append(path.relative_to(context.run.root).as_posix())
        return tuple(references)

    def _rebuild_search_state(self, context: _Context) -> DiscoverySearchState:
        return context.stores.search.rebuild(
            contract=context.contract,
            candidates=tuple(context.stores.candidates.list()),
            evaluations=self._search_feedback_evaluations(context),
        )

    def _search_feedback_evaluations(
        self,
        context: _Context,
    ) -> tuple[CandidateEvaluation, ...]:
        """Exclude reference-only measurements from adaptive search decisions.

        Statistical baseline evaluations remain durable and available to paired
        aggregate construction, replay, and reporting.  They must not affect
        bandit rewards, patience, Pareto parents, or final candidate selection.
        """

        reference_evaluation_ids = {
            task.result_evaluation_id
            for task in context.stores.promotions.list()
            if task.purpose == "statistical_baseline"
            and task.result_evaluation_id
        }
        return tuple(
            evaluation
            for evaluation in context.stores.candidates.list_evaluations()
            if evaluation.evaluation_id not in reference_evaluation_ids
        )

    def _stop_decision(
        self,
        context: _Context,
        progress: DiscoveryProgress,
    ) -> StopDecision:
        state = self._rebuild_search_state(context)
        budget = context.stores.budget.snapshot().used
        raw_policy = context.contract.stop_policy
        configured_max = _optional_non_negative_integer(
            raw_policy.get("max_iterations"),
            name="stop_policy.max_iterations",
        )
        maximum = context.record.spec.max_iterations
        if configured_max is not None:
            maximum = min(maximum, configured_max)
        policy = StopPolicy(
            max_without_improvement=_non_negative_integer(
                raw_policy.get("max_without_improvement"),
                default=10,
                name="stop_policy.max_without_improvement",
            ),
            min_valid_candidates=_non_negative_integer(
                raw_policy.get("min_valid_candidates"),
                default=1,
                name="stop_policy.min_valid_candidates",
            ),
            max_iterations=maximum,
        )
        safety_violations = (
            tuple(
                sorted(
                    item.candidate_id
                    for item in context.stores.candidates.list()
                    if item.status == CandidateStatus.QUARANTINED
                )
            )
            if raw_policy.get("stop_on_quarantine") is True
            else ()
        )
        decision = evaluate_stop(
            limits=context.contract.budget,
            usage=PolicyBudgetUsage(
                proposals=budget.proposals,
                llm_tokens=budget.llm_tokens,
                gpu_seconds=budget.gpu_seconds,
                wall_seconds=budget.wall_seconds,
                api_cost=budget.api_cost,
            ),
            patience=PatienceState(
                valid_candidates=state.valid_candidates,
                since_last_improvement=state.since_last_improvement,
            ),
            policy=policy,
            iteration=progress.next_iteration,
            safety_violations=safety_violations,
        )
        if (
            decision.reason == StopReason.BUDGET_EXHAUSTED
            and progress.next_iteration >= maximum
        ):
            return StopDecision(
                should_stop=True,
                reason=StopReason.MAX_ITERATIONS,
                details=(f"iteration={progress.next_iteration}",),
            )
        return decision

    async def _handle_stop_decision(
        self,
        context: _Context,
        control: _RunControl,
        progress: DiscoveryProgress,
        decision: StopDecision,
    ) -> None:
        if decision.reason == StopReason.SAFETY_VIOLATION:
            progress = progress.model_copy(
                update={
                    "lifecycle": DiscoveryLifecycle.STOPPED,
                    "stop_reason": decision.reason.value,
                    "stop_code": decision.reason.value,
                    "stop_details": decision.details,
                }
            )
            context.stores.checkpoints.complete(state=model_payload(progress))
            await context.events.emit(
                DiscoveryEventName.RUN_STOPPED,
                payload={
                    "reason": decision.reason.value,
                    "details": decision.details,
                },
            )
            return
        await self._finish_or_request_review(
            context,
            control,
            progress,
            stop=decision,
        )

    async def _apply_archive_states(
        self,
        context: _Context,
        pareto_ids: tuple[str, ...],
    ) -> None:
        elite = set(pareto_ids)
        for candidate in context.stores.candidates.list():
            if candidate.status != CandidateStatus.EVALUATED:
                continue
            await self._transition(
                context,
                candidate.candidate_id,
                CandidateStatus.ELITE if candidate.candidate_id in elite else CandidateStatus.DOMINATED,
                iteration=candidate.iteration,
            )

    async def _quarantine(
        self,
        context: _Context,
        node: IterationNode,
        candidate: CandidateRecord,
        iteration: int,
        reason: str,
    ) -> None:
        await self._transition(
            context,
            candidate.candidate_id,
            CandidateStatus.QUARANTINED,
            reason=reason,
            iteration=iteration,
            child_run_id=node.child_run_id,
        )
        await context.events.emit(
            DiscoveryEventName.CANDIDATE_QUARANTINED,
            iteration=iteration,
            child_run_id=node.child_run_id,
            candidate_id=candidate.candidate_id,
            error_code=DiscoveryErrorCode.PREFLIGHT_REJECTED,
            payload={"reason": reason},
        )

    async def _transition(
        self,
        context: _Context,
        candidate_id: str,
        target: CandidateStatus,
        *,
        reason: str | None = None,
        iteration: int | None = None,
        child_run_id: str = "",
    ) -> CandidateRecord:
        current = context.stores.candidates.get(candidate_id)
        if current is None:
            raise FileNotFoundError(candidate_id)
        if current.status == target:
            return current
        updated = context.stores.candidates.transition(
            candidate_id,
            target,
            failure_reason=reason,
        )
        await context.events.emit(
            DiscoveryEventName.CANDIDATE_TRANSITIONED,
            iteration=iteration,
            child_run_id=child_run_id,
            candidate_id=candidate_id,
            payload={"from": current.status.value, "to": target.value, "reason": reason or ""},
        )
        return updated

    async def _finish_or_request_review(
        self,
        context: _Context,
        control: _RunControl,
        progress: DiscoveryProgress,
        *,
        stop: StopDecision,
    ) -> None:
        progress = progress.model_copy(
            update={
                "stop_reason": stop.reason.value,
                "stop_code": stop.reason.value,
                "stop_details": stop.details,
            }
        )
        if not progress.hitl_resolved:
            if not progress.hitl_pending:
                progress = progress.model_copy(update={"hitl_pending": True})
                await self._save_progress(
                    context,
                    control,
                    progress,
                    phase="hitl_requested",
                    key="hitl-requested",
                )
                await context.events.emit(DiscoveryEventName.HITL_REQUESTED)
            if not context.record.spec.auto_approve:
                progress = progress.model_copy(
                    update={"lifecycle": DiscoveryLifecycle.WAITING_HITL}
                )
                await self._save_progress(
                    context,
                    control,
                    progress,
                    phase="waiting_hitl",
                    key="waiting-hitl",
                )
                control.gate.clear()
                context.stores.checkpoints.pause(reason="waiting_hitl")
                return
            progress = progress.model_copy(
                update={"hitl_pending": False, "hitl_resolved": True}
            )
            await context.events.emit(
                DiscoveryEventName.HITL_RESOLVED,
                payload={"resolution": "auto_approve"},
            )

        archive = context.stores.archive.latest()
        candidates = {
            item.candidate_id: item for item in context.stores.candidates.list()
        }
        ineligible = {
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
            CandidateStatus.FAILED,
        }
        selected = progress.selected_candidate_id
        if selected and (
            selected not in candidates or candidates[selected].status in ineligible
        ):
            selected = ""
        selection_evidence = dict(progress.selection_evidence)
        if selected:
            selection_evidence = {
                "schema_id": "candidate_selection.v1",
                "source": "hitl_or_existing_selection",
                "candidate_id": selected,
            }
        elif archive is not None:
            ranked = _select_candidate(
                contract=context.contract,
                candidates=candidates,
                evaluations=self._search_feedback_evaluations(context),
                pareto_candidate_ids=archive.pareto_candidate_ids,
            )
            selected = ranked.candidate_id
            selection_evidence = ranked.evidence
        progress = progress.model_copy(
            update={
                "lifecycle": DiscoveryLifecycle.COMPLETED,
                "hitl_pending": False,
                "selected_candidate_id": selected,
                "selection_evidence": selection_evidence,
                "stop_reason": "completed",
            }
        )
        context.stores.checkpoints.complete(state=model_payload(progress))
        await context.events.emit(
            DiscoveryEventName.RUN_STOPPED,
            payload={
                "reason": stop.reason.value,
                "details": stop.details,
                "selected_candidate_id": selected,
                "selection": selection_evidence,
            },
        )

    async def _save_progress(
        self,
        context: _Context,
        control: _RunControl,
        progress: DiscoveryProgress,
        *,
        phase: str,
        key: str,
    ) -> None:
        while True:
            await control.gate.wait()
            async with control.lock:
                if not control.gate.is_set():
                    continue
                if self._terminal(context):
                    return
                context.stores.checkpoints.checkpoint(
                    phase=phase,
                    iteration=progress.next_iteration,
                    state=model_payload(progress),
                    idempotency_key=key,
                )
                return

    async def _stop_for_budget(
        self,
        context: _Context,
        control: _RunControl,
        exc: BudgetExceededError,
    ) -> None:
        async with control.lock:
            if self._terminal(context):
                return
            progress = self._progress(context).model_copy(
                update={
                    "lifecycle": DiscoveryLifecycle.STOPPED,
                    "stop_reason": StopReason.BUDGET_EXHAUSTED.value,
                    "stop_code": StopReason.BUDGET_EXHAUSTED.value,
                    "stop_details": (exc.resource,),
                }
            )
            context.stores.checkpoints.complete(state=model_payload(progress))
            await context.events.emit(
                DiscoveryEventName.RUN_STOPPED,
                error_code=DiscoveryErrorCode.BUDGET_EXHAUSTED,
                payload={"resource": exc.resource},
            )

    async def _fail(
        self,
        context: _Context,
        control: _RunControl,
        exc: Exception,
    ) -> None:
        async with control.lock:
            if self._terminal(context):
                return
            progress = self._progress(context).model_copy(
                update={
                    "lifecycle": DiscoveryLifecycle.FAILED,
                    "stop_reason": str(exc),
                    "stop_code": "failure",
                    "stop_details": (str(exc),),
                }
            )
            context.stores.checkpoints.fail(reason=str(exc), state=model_payload(progress))
            await context.events.emit(
                DiscoveryEventName.RUN_STOPPED,
                error_code=DiscoveryErrorCode.INVALID_STATE,
                payload={"reason": str(exc)},
            )

    async def _require_adapter_ready(self, context: _Context) -> None:
        project_inputs = dict(context.record.spec.project_inputs)
        request = AdapterRequest(
            action=AdapterAction.READINESS,
            request_id=f"readiness:{context.run.run_id}",
            project=context.contract.project,
            run_id=context.run.run_id,
            config={**project_inputs, "project_inputs": project_inputs},
        )
        response = await self.adapter.invoke(request)
        await self._charge_adapter_action_resources(
            context,
            None,
            request,
            response,
            self._progress(context).next_iteration,
        )
        if response.status not in {"ready", "ok"}:
            raise DiscoveryServiceError(
                DiscoveryErrorCode.ADAPTER_NOT_READY,
                response.error or "project adapter is not ready",
                status_code=503,
            )

    def _adapter_request(
        self,
        context: _Context,
        node: IterationNode,
        candidate: CandidateRecord,
        *,
        action: AdapterAction,
        iteration: int,
        ordinal: int,
        fidelity: FidelityLevel | None = None,
        seed: int | None = None,
        execution_key: str = "",
    ) -> AdapterRequest:
        project_inputs = dict(context.record.spec.project_inputs)
        resolved_fidelity = fidelity or FidelityLevel(
            str(project_inputs.get("fidelity") or FidelityLevel.F0.value)
        )
        candidate_seed = (
            seed
            if seed is not None
            else context.contract.seed + iteration * 10_000 + ordinal
        )
        candidate_inputs = {
            **project_inputs,
            "seed": candidate_seed,
            "fidelity": resolved_fidelity.value,
        }
        request_scope = execution_key or f"{iteration}:{ordinal}"
        output_dir = context.run.root / "execution" / candidate.candidate_id
        if execution_key:
            output_dir = (
                output_dir
                / resolved_fidelity.value
                / f"seed-{candidate_seed}"
            )
        return AdapterRequest(
            action=action,
            request_id=f"{action.value}:{request_scope}:{candidate.candidate_id}",
            project=context.contract.project,
            run_id=node.child_run_id,
            candidate_id=candidate.candidate_id,
            fidelity=resolved_fidelity.value,
            seed=candidate_seed,
            repo_snapshot_ref=context.contract.baseline_ref,
            data_manifest_ref=context.contract.dataset_ref,
            config={
                **candidate_inputs,
                "project_inputs": candidate_inputs,
                "model_genome": candidate.genome.model_dump(mode="json"),
            },
            output_dir=str(output_dir),
        )

    def _ensure_iteration_child(
        self,
        context: _Context,
        progress: DiscoveryProgress,
        iteration: int,
    ) -> tuple[IterationNode, DiscoveryProgress]:
        for node in progress.iteration_nodes:
            if node.iteration == iteration:
                return node, progress
        marker = f"discovery_child__{context.run.run_id}__{iteration}"
        child = next((run for run in self.run_store.list() if run.task == marker), None)
        if child is None:
            child = self.run_store.create(
                task=marker,
                project=context.contract.project,
                entrypoint="discovery_iteration",
                user_request=context.contract.objective,
            )
        dependencies: tuple[str, ...] = ()
        if progress.iteration_nodes:
            dependencies = (progress.iteration_nodes[-1].child_run_id,)
        node = IterationNode(
            iteration=iteration,
            child_run_id=child.run_id,
            parent_run_id=context.run.run_id,
            depends_on_run_ids=dependencies,
        )
        child_paths = DiscoveryPaths(run_root=child.root, run_id=child.run_id)
        with discovery_lock(child_paths):
            atomic_write_json(
                child_paths.root / "iteration_link.json",
                {
                    "schema_id": "discovery_iteration_link.v1",
                    **model_payload(node),
                },
            )
        return node, progress.model_copy(
            update={"iteration_nodes": (*progress.iteration_nodes, node)}
        )

    def _validate_candidate(
        self,
        candidate: CandidateRecord,
        context: _Context,
        iteration: int,
    ) -> None:
        if candidate.run_id != context.run.run_id or candidate.iteration != iteration:
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_CONTRACT,
                "candidate does not belong to the requested run and iteration",
                status_code=422,
            )

    def _context(self, run_id: str) -> _Context:
        run = self._require_run(run_id)
        service_path = run.root / "discovery" / "service.json"
        contract_path = run.root / "discovery" / "contract.json"
        if not service_path.exists() or not contract_path.exists():
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_STATE,
                f"run is not a discovery run: {run_id}",
                status_code=404,
            )
        record = _ServiceRecord.model_validate(read_json(service_path))
        contract = ResearchTaskContract.model_validate(read_json(contract_path))
        return _Context(
            run=run,
            record=record,
            contract=contract,
            stores=self.store_factory.build(run, contract),
            events=DiscoveryEventSink(run.root, run_id=run_id, bus=self.event_bus),
        )

    def _require_run(self, run_id: str) -> RunHandle:
        run = self.run_store.get(run_id)
        if run is None:
            raise DiscoveryServiceError(
                DiscoveryErrorCode.INVALID_STATE,
                f"run not found: {run_id}",
                status_code=404,
            )
        return run

    @staticmethod
    def _proposal_ref(run: RunHandle) -> str:
        authoritative = run.root / "idea" / "discovery" / "selection.v1.json"
        if authoritative.exists():
            payload = read_json(authoritative)
            if isinstance(payload, dict) and payload.get("proposal_ref"):
                return str(payload["proposal_ref"])
        candidates = [
            item
            for item in ArtifactStore(run).list_versions(
                agent_dir="idea",
                stem="idea_proposal",
            )
            if item.version != "approved"
        ]
        if not candidates:
            return ""
        return candidates[-1].path.relative_to(run.root).as_posix()

    @staticmethod
    def _run_request_extra(run: RunHandle) -> dict[str, Any]:
        path = run.subdir("input") / "run_request_options.v1.json"
        if not path.is_file():
            return {}
        try:
            raw = read_json(path)
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict) or raw.get("schema_id") != "run_request_options.v1":
            return {}
        extra = raw.get("extra")
        return (
            {str(key): value for key, value in extra.items()}
            if isinstance(extra, dict)
            else {}
        )

    @staticmethod
    def _artifact_mapping(
        artifacts: dict[str, Any],
        name: str,
    ) -> dict[str, Any]:
        value = artifacts.get(name)
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _record_items(
        cls,
        artifacts: dict[str, Any],
        name: str,
    ) -> tuple[dict[str, Any], ...]:
        wrapper = cls._artifact_mapping(artifacts, name)
        items = wrapper.get("items")
        if not isinstance(items, list):
            return ()
        return tuple(dict(item) for item in items if isinstance(item, dict))

    @staticmethod
    def _string_items(value: Any) -> tuple[str, ...]:
        if not isinstance(value, list | tuple):
            return ()
        return tuple(item for item in value if isinstance(item, str) and item)

    def _find_create(self, idempotency_key: str) -> _Context | None:
        for run in self.run_store.list():
            path = run.root / "discovery" / "service.json"
            if not path.exists():
                continue
            record = _ServiceRecord.model_validate(read_json(path))
            if record.create_idempotency_key == idempotency_key:
                return self._context(run.run_id)
        return None

    def _control(self, run_id: str) -> _RunControl:
        return self._controls.setdefault(run_id, _RunControl())

    @staticmethod
    def _lifecycle(
        status: CheckpointStatus,
        progress: DiscoveryProgress,
    ) -> DiscoveryLifecycle:
        if status == CheckpointStatus.FAILED:
            return DiscoveryLifecycle.FAILED
        if status == CheckpointStatus.COMPLETED:
            return progress.lifecycle
        if status == CheckpointStatus.PAUSED:
            if progress.lifecycle == DiscoveryLifecycle.CREATED:
                return DiscoveryLifecycle.CREATED
            if progress.hitl_pending:
                return DiscoveryLifecycle.WAITING_HITL
            return DiscoveryLifecycle.PAUSED
        return DiscoveryLifecycle.RUNNING

    @staticmethod
    def _invalid_state(detail: str) -> DiscoveryServiceError:
        return DiscoveryServiceError(DiscoveryErrorCode.INVALID_STATE, detail)

    @staticmethod
    def _terminal(context: _Context) -> bool:
        latest = context.stores.checkpoints.latest()
        return latest is not None and latest.status in {
            CheckpointStatus.COMPLETED,
            CheckpointStatus.FAILED,
        }

    @staticmethod
    def _progress(context: _Context) -> DiscoveryProgress:
        latest = context.stores.checkpoints.latest()
        if latest is None:
            raise RuntimeError("discovery checkpoint is missing")
        return DiscoveryProgress.model_validate(latest.state)


def _select_candidate(
    *,
    contract: ResearchTaskContract,
    candidates: dict[str, CandidateRecord],
    evaluations: tuple[CandidateEvaluation, ...],
    pareto_candidate_ids: tuple[str, ...],
) -> _CandidateSelection:
    if not contract.objectives:
        return _CandidateSelection(
            candidate_id="",
            evidence={
                "schema_id": "candidate_selection.v1",
                "source": "automatic_ranker",
                "reason": "no_primary_objective",
            },
        )
    primary = contract.objectives[0]
    pool = set(pareto_candidate_ids)
    grouped: dict[str, list[CandidateEvaluation]] = {}
    for evaluation in evaluations:
        if evaluation.candidate_id not in pool:
            continue
        candidate = candidates.get(evaluation.candidate_id)
        metric = evaluation.canonical_metrics.get(primary.name)
        if (
            candidate is None
            or candidate.status
            in {
                CandidateStatus.QUARANTINED,
                CandidateStatus.REJECTED,
                CandidateStatus.FAILED,
            }
            or not evaluation.hard_constraints_passed
            or metric is None
            or not math.isfinite(metric.value)
        ):
            continue
        grouped.setdefault(evaluation.candidate_id, []).append(evaluation)

    rankings: list[tuple[int, float, int, str, dict[str, Any]]] = []
    for candidate_id, candidate_evaluations in grouped.items():
        fidelity = max(
            (item.fidelity for item in candidate_evaluations),
            key=_fidelity_rank,
        )
        values = [
            item.canonical_metrics[primary.name].value
            for item in candidate_evaluations
            if item.fidelity == fidelity
        ]
        mean = statistics.fmean(values)
        standard_error = (
            statistics.stdev(values) / math.sqrt(len(values))
            if len(values) > 1
            else 0.0
        )
        critical = t_critical_95(len(values)) if len(values) > 1 else 0.0
        conservative_value = (
            mean + critical * standard_error
            if primary.direction == ObjectiveDirection.MINIMIZE
            else mean - critical * standard_error
        )
        quality = (
            -conservative_value
            if primary.direction == ObjectiveDirection.MINIMIZE
            else conservative_value
        )
        evidence = {
            "candidate_id": candidate_id,
            "fidelity": fidelity.value,
            "evidence_count": len(values),
            "primary_objective": primary.name,
            "mean": mean,
            "standard_error": standard_error,
            "critical_value": critical,
            "conservative_value": conservative_value,
            "hard_constraints_passed": True,
        }
        rankings.append(
            (_fidelity_rank(fidelity), quality, len(values), candidate_id, evidence)
        )

    rankings.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    selected = rankings[0][3] if rankings else ""
    return _CandidateSelection(
        candidate_id=selected,
        evidence={
            "schema_id": "candidate_selection.v1",
            "source": "automatic_ranker",
            "candidate_id": selected,
            "ordering": (
                "hard_constraints",
                "highest_fidelity",
                "conservative_primary_objective",
                "evidence_count",
                "candidate_id",
            ),
            "rankings": tuple(item[4] for item in rankings),
            "reason": "ranked_pareto_candidates" if rankings else "no_eligible_candidate",
        },
    )


def _fidelity_rank(value: FidelityLevel) -> int:
    return {
        FidelityLevel.F0: 0,
        FidelityLevel.F1: 1,
        FidelityLevel.F2: 2,
        FidelityLevel.F3: 3,
        FidelityLevel.F4: 4,
    }[value]


def _run_local_artifact_path(run_root: Path, reference: str) -> Path:
    """Resolve one non-symlink JSON artifact strictly below its owning run."""

    normalized = reference.strip().replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("code artifact ref must be a safe run-relative path")
    try:
        resolved_root = run_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"run root is unavailable: {exc}") from exc
    candidate = run_root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError("code artifact ref must not contain symlinks")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"code artifact ref is unavailable or escapes its run: {exc}") from exc
    if not resolved.is_file():
        raise ValueError("code artifact ref must resolve to a regular file")
    return resolved


def _next_fidelity(value: FidelityLevel) -> FidelityLevel | None:
    return {
        FidelityLevel.F0: FidelityLevel.F1,
        FidelityLevel.F1: FidelityLevel.F2,
        FidelityLevel.F2: FidelityLevel.F3,
        FidelityLevel.F3: FidelityLevel.F4,
        FidelityLevel.F4: None,
    }[value]


def _automatic_maximum_fidelity(policy: dict[str, Any]) -> FidelityLevel:
    raw = str(policy.get("maximum_fidelity") or FidelityLevel.F1.value)
    try:
        fidelity = FidelityLevel(raw)
    except ValueError as exc:
        raise ValueError(
            "promotion_policy.maximum_fidelity must be one of F1 or F2"
        ) from exc
    if fidelity not in {FidelityLevel.F1, FidelityLevel.F2}:
        raise ValueError(
            "automatic promotion is bounded to F1/F2; F3/F4 require a sealed HITL path"
        )
    return fidelity


def _promotion_max_attempts(policy: dict[str, Any]) -> int:
    attempts = _positive_integer(
        policy.get("max_attempts"),
        default=3,
        name="promotion_policy.max_attempts",
    )
    if attempts > 5:
        raise ValueError("promotion_policy.max_attempts must not exceed 5")
    return attempts


def _retryable_promotion_response(response: AdapterResponse) -> bool:
    return response.error_code in {
        "adapter_timeout",
        "remote_adapter_transport_failed",
        "remote_heartbeat_stale",
        "remote_poll_timeout",
        "remote_readiness_failed",
        "remote_status_unavailable",
    }


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite number")
    resolved = float(value)
    if not math.isfinite(resolved):
        raise ValueError(f"{name} must be a finite number")
    return resolved


def _finite_number_or_default(
    value: object,
    *,
    default: float,
    name: str,
) -> float:
    return default if value is None else _finite_number(value, name)


def _positive_integer(value: object, *, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _statistical_gate_failure(
    message: str,
    *,
    code: str = "invalid_configuration",
    dataset_role: str = "",
    aggregate_refs: tuple[str, ...] = (),
) -> tuple[bool, dict[str, Any], tuple[str, ...]]:
    payload: dict[str, Any] = {
        "enabled": True,
        "passed": False,
        "reason_codes": (code,),
        "error": message,
    }
    if dataset_role:
        payload["dataset_role"] = dataset_role
    if aggregate_refs:
        payload["aggregate_refs"] = aggregate_refs
    return False, payload, (f"statistical:{code}",)


def _non_negative_integer(value: object, *, default: int, name: str) -> int:
    resolved = _optional_non_negative_integer(value, name=name)
    return default if resolved is None else resolved


def _optional_non_negative_integer(value: object, *, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value
