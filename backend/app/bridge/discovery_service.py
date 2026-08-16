"""Product orchestration for durable, project-agnostic model discovery."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.bridge.discovery_core import DefaultDiscoveryCore, DiscoveryCore
from app.bridge.discovery_events import DiscoveryEventSink
from app.bridge.discovery_types import (
    CandidateProposalRequest,
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
)
from app.execution.adapters.base import (
    AdapterAction,
    AdapterRequest,
    AdapterResponse,
    ProjectAdapter,
)
from app.harness.discovery.models import (
    BudgetTransaction,
    CandidateRecord,
    CandidateStatus,
    FidelityLevel,
    ResearchTaskContract,
)
from app.harness.discovery.protocol import DiscoveryErrorCode, DiscoveryEventName
from app.harness.runtime.event_bus import EventBus
from app.storage.discovery_budget_ledger import BudgetExceededError
from app.storage.discovery_checkpoint_store import CheckpointStatus
from app.storage.discovery_common import (
    DiscoveryPaths,
    atomic_write_json,
    discovery_lock,
    model_payload,
    read_json,
    stable_key,
)
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RunStore


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


class DiscoveryService:
    """The only write path for a Discovery run and its iteration children."""

    def __init__(
        self,
        *,
        run_store: RunStore,
        event_bus: EventBus,
        candidate_agent: DiscoveryCandidateAgent,
        adapter: ProjectAdapter,
        core: DiscoveryCore | None = None,
        store_factory: DiscoveryStoreFactory | None = None,
    ) -> None:
        self.run_store = run_store
        self.event_bus = event_bus
        self.candidate_agent = candidate_agent
        self.adapter = adapter
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
            hitl_pending=progress.hitl_pending,
            selected_candidate_id=progress.selected_candidate_id,
            iteration_nodes=progress.iteration_nodes,
            budget=context.stores.budget.snapshot(),
            latest_archive=context.stores.archive.latest(),
            stop_reason=progress.stop_reason,
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
            progress = DiscoveryProgress.model_validate(latest.state).model_copy(
                update={"lifecycle": DiscoveryLifecycle.STOPPED, "stop_reason": reason}
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
        idea_mode = "fast"
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
        selection = IdeaSelectionRequest(
            run_id=run_id,
            hypothesis_id=hypothesis_id,
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            selection_request_ref=relative_record.as_posix(),
            proposal_ref=self._proposal_ref(run),
        )
        record_path = run.root / relative_record
        paths = DiscoveryPaths(run_root=run.root, run_id=run_id)
        with discovery_lock(paths):
            if record_path.exists():
                existing = IdeaSelectionRequest.model_validate(read_json(record_path))
                if existing != selection:
                    raise DiscoveryServiceError(
                        DiscoveryErrorCode.IDEMPOTENCY_CONFLICT,
                        "selection idempotency key is already bound",
                    )
                return existing
            atomic_write_json(record_path, model_payload(selection))
        return selection

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
                if progress.next_iteration >= context.record.spec.max_iterations:
                    await self._finish_or_request_review(context, control, progress)
                    return
                progress = await self._run_iteration(context, control, progress)
                await self._save_progress(
                    context,
                    control,
                    progress,
                    phase="iteration_completed",
                    key=f"iteration-completed:{progress.next_iteration}",
                )
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

        snapshot = self.core.archive(
            contract=context.contract,
            evaluations=tuple(context.stores.candidates.list_evaluations()),
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
        try:
            candidate = await self.candidate_agent.propose(
                CandidateProposalRequest(
                    contract=context.contract,
                    iteration=iteration,
                    ordinal=ordinal,
                    child_run_id=node.child_run_id,
                    parent_candidate_ids=parents,
                )
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
        if current.status in {
            CandidateStatus.ELITE,
            CandidateStatus.DOMINATED,
            CandidateStatus.EVALUATED,
            CandidateStatus.PROMOTED,
        }:
            evaluations = context.stores.candidates.list_evaluations(
                candidate_id=current.candidate_id
            )
            if evaluations:
                await self._charge_resources(
                    context,
                    current,
                    evaluations[-1].resource_usage,
                    iteration,
                    ordinal,
                )
            return
        if current.status in {
            CandidateStatus.FAILED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
        }:
            return
        if current.status == CandidateStatus.DRAFT:
            report = self.core.preflight(current, context.contract)
            if not report.passed:
                reason = "; ".join(item.reason for item in report.blockers)
                await self._quarantine(context, node, current, iteration, reason)
                return
            preflight = await self.adapter.invoke(
                self._adapter_request(
                    context,
                    node,
                    current,
                    action=AdapterAction.PREFLIGHT,
                    iteration=iteration,
                    ordinal=ordinal,
                )
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
        lease_id = f"evaluation-{iteration:04d}-{ordinal:04d}"
        context.stores.budget.acquire_slot(
            lease_id=lease_id,
            candidate_id=current.candidate_id,
        )
        try:
            current = await self._transition(
                context,
                current.candidate_id,
                CandidateStatus.RUNNING,
                iteration=iteration,
                child_run_id=node.child_run_id,
            )
            execute = await self.adapter.invoke(
                self._adapter_request(
                    context,
                    node,
                    current,
                    action=AdapterAction.EXECUTE,
                    iteration=iteration,
                    ordinal=ordinal,
                )
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
            response = await self.adapter.invoke(
                self._adapter_request(
                    context,
                    node,
                    current,
                    action=AdapterAction.EVALUATE,
                    iteration=iteration,
                    ordinal=ordinal,
                )
            )
            if response.status != "ok":
                await self._transition(
                    context,
                    current.candidate_id,
                    CandidateStatus.FAILED,
                    reason=response.error or "adapter evaluation failed",
                    iteration=iteration,
                    child_run_id=node.child_run_id,
                )
                return
            seed = context.contract.seed + iteration * 10_000 + ordinal
            evaluation = self.core.evaluate(
                candidate=current,
                contract=context.contract,
                response=response,
                fidelity=FidelityLevel(
                    str(
                        context.record.spec.project_inputs.get("fidelity")
                        or FidelityLevel.F0.value
                    )
                ),
                seed=seed,
            )
            stored = context.stores.candidates.record_evaluation(evaluation)
            await self._charge_resources(context, current, stored.resource_usage, iteration, ordinal)
            await context.events.emit(
                DiscoveryEventName.CANDIDATE_EVALUATED,
                iteration=iteration,
                child_run_id=node.child_run_id,
                candidate_id=current.candidate_id,
                payload={"evaluation_id": stored.evaluation_id},
            )
        finally:
            context.stores.budget.release_slot(lease_id)

    async def _charge_resources(
        self,
        context: _Context,
        candidate: CandidateRecord,
        usage: dict[str, float],
        iteration: int,
        ordinal: int,
    ) -> None:
        transaction = BudgetTransaction(
            transaction_id=f"resources-{iteration:04d}-{ordinal:04d}",
            run_id=context.run.run_id,
            candidate_id=candidate.candidate_id,
            idempotency_key=f"resources:{iteration}:{ordinal}",
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
                payload={"transaction_id": transaction.transaction_id},
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
    ) -> None:
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
        selected = archive.pareto_candidate_ids[0] if archive and archive.pareto_candidate_ids else ""
        progress = progress.model_copy(
            update={
                "lifecycle": DiscoveryLifecycle.COMPLETED,
                "hitl_pending": False,
                "selected_candidate_id": selected,
                "stop_reason": "completed",
            }
        )
        context.stores.checkpoints.complete(state=model_payload(progress))
        await context.events.emit(
            DiscoveryEventName.RUN_STOPPED,
            payload={"reason": "completed", "selected_candidate_id": selected},
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
                    "stop_reason": "budget_exhausted",
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
                update={"lifecycle": DiscoveryLifecycle.FAILED, "stop_reason": str(exc)}
            )
            context.stores.checkpoints.fail(reason=str(exc), state=model_payload(progress))
            await context.events.emit(
                DiscoveryEventName.RUN_STOPPED,
                error_code=DiscoveryErrorCode.INVALID_STATE,
                payload={"reason": str(exc)},
            )

    async def _require_adapter_ready(self, context: _Context) -> None:
        project_inputs = dict(context.record.spec.project_inputs)
        response = await self.adapter.invoke(
            AdapterRequest(
                action=AdapterAction.READINESS,
                request_id=f"readiness:{context.run.run_id}",
                project=context.contract.project,
                run_id=context.run.run_id,
                config={**project_inputs, "project_inputs": project_inputs},
            )
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
    ) -> AdapterRequest:
        project_inputs = dict(context.record.spec.project_inputs)
        fidelity = FidelityLevel(
            str(project_inputs.get("fidelity") or FidelityLevel.F0.value)
        )
        return AdapterRequest(
            action=action,
            request_id=f"{action.value}:{iteration}:{ordinal}:{candidate.candidate_id}",
            project=context.contract.project,
            run_id=node.child_run_id,
            candidate_id=candidate.candidate_id,
            fidelity=fidelity.value,
            seed=context.contract.seed + iteration * 10_000 + ordinal,
            repo_snapshot_ref=context.contract.baseline_ref,
            data_manifest_ref=context.contract.dataset_ref,
            config={
                **project_inputs,
                "project_inputs": project_inputs,
                "model_genome": candidate.genome.model_dump(mode="json"),
            },
            output_dir=str(context.run.root / "execution" / candidate.candidate_id),
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
