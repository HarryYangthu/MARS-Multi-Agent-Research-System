from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from app.bridge.discovery_service import DiscoveryService
from app.bridge.discovery_types import CandidateProposalRequest, DiscoveryLifecycle, DiscoveryRunSpec
from app.execution.adapters.base import AdapterAction, AdapterRequest, AdapterResponse
from app.harness.discovery.candidate_builder import build_candidate_record
from app.harness.discovery.models import (
    BudgetLimits,
    CandidateRecord,
    ModelGenome,
    ObjectiveDirection,
    ObjectiveSpec,
)
from app.harness.discovery.protocol import DiscoveryEventName
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore


class FakeCandidateAgent:
    async def propose(self, request: CandidateProposalRequest) -> CandidateRecord:
        genome = ModelGenome(
            family="synthetic_regression",
            hyperparameters={"candidate_index": request.ordinal},
            mutable_zones=("hyperparameters.candidate_index",),
        )
        return build_candidate_record(
            run_id=request.contract.run_id,
            genome=genome,
            creator="fake_candidate_agent",
            operator="sample",
            parent_ids=request.parent_candidate_ids[:1],
            generation=request.iteration,
            iteration=request.iteration,
            metadata={"candidate_index": request.ordinal},
        )


class FakeAdapter:
    def __init__(self, *, fail_index: int | None = 7) -> None:
        self.fail_index = fail_index
        self.calls: list[AdapterRequest] = []

    @property
    def name(self) -> str:
        return "fake_synthetic_adapter"

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        self.calls.append(request)
        if request.action == AdapterAction.READINESS:
            return AdapterResponse(request_id=request.request_id, status="ready")
        index = int(request.config["hyperparameters"]["candidate_index"])
        if request.action == AdapterAction.EXECUTE and index == self.fail_index:
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="synthetic_failure",
                error="isolated synthetic execution failure",
            )
        if request.action == AdapterAction.EVALUATE:
            return AdapterResponse(
                request_id=request.request_id,
                status="ok",
                raw_metrics={"validation_loss": float(index + 1)},
                artifacts={"metrics": f"artifact://metrics/{index}"},
                resource_usage={
                    "llm_tokens": 0.0,
                    "gpu_seconds": 0.0,
                    "wall_seconds": 0.0,
                    "api_cost": 0.0,
                },
            )
        return AdapterResponse(request_id=request.request_id, status="ok")


class BlockingAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(fail_index=None)
        self.execution_started = asyncio.Event()
        self.release_execution = asyncio.Event()
        self._blocked_once = False

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if request.action == AdapterAction.EXECUTE and not self._blocked_once:
            self._blocked_once = True
            self.execution_started.set()
            await self.release_execution.wait()
        return await super().invoke(request)


def discovery_spec(
    *,
    candidates: int = 20,
    iterations: int = 1,
    idea_mode: str = "auto",
) -> DiscoveryRunSpec:
    payload: dict[str, Any] = {
        "task": "synthetic_model_discovery",
        "project": "synthetic_regression",
        "objective": "minimize validation loss",
        "evaluator_hash": "sha256:synthetic-evaluator",
        "dataset_hash": "sha256:synthetic-dataset",
        "objectives": (
            ObjectiveSpec(
                name="validation_loss",
                direction=ObjectiveDirection.MINIMIZE,
            ),
        ),
        "budget": BudgetLimits(proposals=candidates * iterations),
        "candidates_per_iteration": candidates,
        "max_iterations": iterations,
        "auto_approve": True,
        "idea_mode": idea_mode,
    }
    return DiscoveryRunSpec.model_validate(payload)


def build_service(tmp_path: Path, *, adapter: FakeAdapter | None = None) -> DiscoveryService:
    return DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=FakeCandidateAgent(),
        adapter=adapter or FakeAdapter(),
    )


@pytest.mark.asyncio
async def test_twenty_candidate_loop_isolates_one_failure_and_replays(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    created = await service.create(discovery_spec(), idempotency_key="create-20")

    completed = await service.start(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.candidate_count == 20
    assert completed.evaluated_count == 19
    assert completed.failed_count == 1
    assert completed.budget.used.proposals == 20
    assert completed.latest_archive is not None
    assert len(completed.latest_archive.pareto_candidate_ids) == 1
    assert completed.selected_candidate_id in completed.latest_archive.pareto_candidate_ids

    replay = service.replay(created.run_id)
    names = {str(event["name"]) for event in replay.events}
    assert DiscoveryEventName.RUN_CREATED.value in names
    assert DiscoveryEventName.BUDGET_DEBITED.value in names
    assert DiscoveryEventName.CANDIDATE_EVALUATED.value in names
    assert DiscoveryEventName.ARCHIVE_UPDATED.value in names
    assert DiscoveryEventName.HITL_REQUESTED.value in names
    assert DiscoveryEventName.HITL_RESOLVED.value in names
    assert DiscoveryEventName.RUN_STOPPED.value in names

    resumed = await service.resume(created.run_id, wait=True)
    assert resumed.budget.used.proposals == 20
    assert resumed.evaluated_count == 19


@pytest.mark.asyncio
async def test_iterations_are_child_run_dag_nodes(tmp_path: Path) -> None:
    service = build_service(tmp_path, adapter=FakeAdapter(fail_index=None))
    created = await service.create(
        discovery_spec(candidates=2, iterations=2),
        idempotency_key="create-dag",
    )

    completed = await service.start(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert len(completed.iteration_nodes) == 2
    first, second = completed.iteration_nodes
    assert first.parent_run_id == created.run_id
    assert first.status == "completed"
    assert second.depends_on_run_ids == (first.child_run_id,)
    assert service.run_store.get(first.child_run_id) is not None
    assert service.run_store.get(second.child_run_id) is not None


@pytest.mark.asyncio
async def test_pause_resume_is_idempotent_and_does_not_double_debit(tmp_path: Path) -> None:
    adapter = BlockingAdapter()
    service = build_service(tmp_path, adapter=adapter)
    created = await service.create(
        discovery_spec(candidates=3),
        idempotency_key="create-pause",
    )
    await service.start(created.run_id)
    await asyncio.wait_for(adapter.execution_started.wait(), timeout=2.0)

    paused = await service.pause(created.run_id, reason="test_pause")
    repeated = await service.pause(created.run_id, reason="test_pause")
    assert paused.lifecycle == DiscoveryLifecycle.PAUSED
    assert repeated.checkpoint_sequence == paused.checkpoint_sequence

    adapter.release_execution.set()
    resumed = await service.resume(created.run_id, wait=True)
    resumed_again = await service.resume(created.run_id, wait=True)
    assert resumed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert resumed.budget.used.proposals == 3
    assert resumed_again.budget.used.proposals == 3


@pytest.mark.asyncio
async def test_stop_is_terminal_and_repeated_stop_is_safe(tmp_path: Path) -> None:
    adapter = BlockingAdapter()
    service = build_service(tmp_path, adapter=adapter)
    created = await service.create(
        discovery_spec(candidates=3),
        idempotency_key="create-stop",
    )
    await service.start(created.run_id)
    await asyncio.wait_for(adapter.execution_started.wait(), timeout=2.0)

    stopped = await service.stop(created.run_id, reason="test_stop")
    stopped_again = await service.stop(created.run_id, reason="ignored")
    adapter.release_execution.set()
    await service.wait(created.run_id)

    assert stopped.lifecycle == DiscoveryLifecycle.STOPPED
    assert stopped.stop_reason == "test_stop"
    assert stopped_again.checkpoint_sequence == stopped.checkpoint_sequence


@pytest.mark.asyncio
async def test_new_service_recovers_interrupted_candidate_without_double_debit(
    tmp_path: Path,
) -> None:
    adapter = BlockingAdapter()
    first = build_service(tmp_path, adapter=adapter)
    created = await first.create(
        discovery_spec(candidates=3),
        idempotency_key="create-recovery",
    )
    await first.start(created.run_id)
    await asyncio.wait_for(adapter.execution_started.wait(), timeout=2.0)
    task = first._controls[created.run_id].task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    recovered = DiscoveryService(
        run_store=first.run_store,
        event_bus=InProcessEventBus(),
        candidate_agent=FakeCandidateAgent(),
        adapter=FakeAdapter(fail_index=None),
    )
    completed = await recovered.resume(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.evaluated_count == 3
    assert completed.budget.used.proposals == 3


def test_old_request_defaults_to_fast_idea_mode(tmp_path: Path) -> None:
    del tmp_path
    spec = discovery_spec().model_dump(mode="json")
    spec.pop("idea_mode")
    assert DiscoveryRunSpec.model_validate(spec).idea_mode == "fast"
