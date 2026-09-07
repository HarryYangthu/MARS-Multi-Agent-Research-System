"""Discovery orchestration over real CPU fits, processes and persisted receipts.

No fabricated candidates, metrics, successful retries or GPU consumption. F2
and external secure-code execution are not supported by this public pack.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.discovery_service import DiscoveryServiceError
from app.bridge.discovery_types import DiscoveryLifecycle, DiscoveryRunSpec, IterationNode
from app.harness.discovery.models import CandidateStatus, FidelityLevel, ObjectiveSpec, ObjectiveDirection
from app.harness.discovery.protocol import DiscoveryEventName
from tests.real_discovery import build_service, discovery_spec


@pytest.mark.asyncio
async def test_twenty_real_candidates_replay_without_reexecution(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    spec = discovery_spec()
    created = await service.create(spec, idempotency_key="twenty-fits")
    repeated_create = await service.create(spec, idempotency_key="twenty-fits")
    assert repeated_create.run_id == created.run_id
    completed = await service.start(created.run_id, wait=True)
    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.candidate_count == completed.evaluated_count == 20
    assert completed.failed_count == completed.quarantined_count == 0
    assert completed.budget.used.proposals == 20
    assert completed.budget.used.wall_seconds > 0
    assert completed.budget.used.gpu_seconds == 0
    replay = service.replay(created.run_id)
    assert len({x.candidate_id for x in replay.candidates}) == 20
    for evaluation in replay.evaluations:
        assert evaluation.hard_constraints_passed
        assert evaluation.canonical_metrics["validation_mse"].value >= 0
        assert evaluation.evaluator_hash and evaluation.dataset_hash
    best = min(replay.evaluations, key=lambda e: e.canonical_metrics["validation_mse"].value)
    assert completed.selected_candidate_id == best.candidate_id
    events = {x["name"] for x in replay.events}
    assert {DiscoveryEventName.RUN_CREATED.value, DiscoveryEventName.CANDIDATE_EVALUATED.value,
            DiscoveryEventName.BUDGET_DEBITED.value, DiscoveryEventName.ARCHIVE_UPDATED.value} <= events
    recovered = build_service(tmp_path)
    resumed = await recovered.resume(created.run_id, wait=True)
    assert resumed.budget.used == completed.budget.used
    assert recovered.replay(created.run_id).evaluations == replay.evaluations


@pytest.mark.asyncio
async def test_actual_units_are_required_for_selection(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    spec = discovery_spec(candidates=2).model_copy(update={"objectives": (
        ObjectiveSpec(name="validation_mse", direction=ObjectiveDirection.MINIMIZE, unit="wrong_unit"),
    )})
    created = await service.create(spec, idempotency_key="unit-mismatch")
    completed = await service.start(created.run_id, wait=True)
    assert not completed.selected_candidate_id
    evaluations = service.replay(created.run_id).evaluations
    assert evaluations and all(not e.hard_constraints_passed for e in evaluations)
    assert all(not e.canonical_metrics for e in evaluations)


@pytest.mark.asyncio
async def test_actual_fits_share_seed_cohorts_and_persist_search_state(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    seeds = (10, 20, 30)
    spec = discovery_spec(candidates=3).model_copy(update={"evaluation_seeds": seeds})
    created = await service.create(spec, idempotency_key="shared-seeds")
    completed = await service.start(created.run_id, wait=True)
    assert completed.evaluated_count == 9
    replay = service.replay(created.run_id)
    for candidate in replay.candidates:
        evaluations = [e for e in replay.evaluations if e.candidate_id == candidate.candidate_id]
        assert tuple(e.seed for e in evaluations) == seeds
        assert len({e.evaluation_id for e in evaluations}) == 3
    context = service._context(created.run_id)
    state = service._rebuild_search_state(context)
    assert state.model_dump(exclude={"updated_at"}) == service._rebuild_search_state(context).model_dump(exclude={"updated_at"})
    assert context.stores.budget.snapshot().used.proposals == 3


@pytest.mark.asyncio
async def test_committed_real_seed_batch_is_not_reexecuted(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    created = await service.create(discovery_spec(candidates=1).model_copy(
        update={"evaluation_seeds": (10, 20, 30)}), idempotency_key="real-seed-replay")
    await service.start(created.run_id, wait=True)
    context = service._context(created.run_id)
    before = service.replay(created.run_id)
    used = context.stores.budget.snapshot().used
    candidate = before.candidates[0]
    node = IterationNode(iteration=0, child_run_id=created.run_id, parent_run_id=created.run_id)
    await service._evaluate_candidate(context, node, candidate, 0, 0)
    assert service.replay(created.run_id).evaluations == before.evaluations
    assert context.stores.budget.snapshot().used == used


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", ["F1", "F2"])
async def test_actual_fidelity_promotion_or_explicit_unsupported_failure(tmp_path: Path, maximum: str) -> None:
    service = build_service(tmp_path)
    spec = discovery_spec(candidates=1).model_copy(update={"evaluation_seeds": (10, 20, 30),
        "promotion_policy": {"enabled": True, "schedule_next_fidelity": True,
                             "maximum_fidelity": maximum, "thresholds": {"validation_mse": 100.0},
                             "max_attempts": 2}})
    created = await service.create(spec, idempotency_key="real-promotion")
    completed = await service.start(created.run_id, wait=True)
    replay = service.replay(created.run_id)
    valid = [e for e in replay.evaluations if e.hard_constraints_passed]
    assert {e.fidelity for e in valid} == {FidelityLevel.F0, FidelityLevel.F1}
    for fidelity in (FidelityLevel.F0, FidelityLevel.F1):
        assert {e.seed for e in valid if e.fidelity == fidelity} == {10, 20, 30}
    if maximum == "F1":
        assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
        assert completed.promotion_completed_count == 3
    else:
        assert completed.lifecycle == DiscoveryLifecycle.FAILED
        assert completed.promotion_failed_count == 3
        assert not any(e.fidelity == FidelityLevel.F2 and e.hard_constraints_passed for e in replay.evaluations)


@pytest.mark.asyncio
async def test_pause_resume_and_stop_are_idempotent_at_real_boundaries(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    created = await service.create(discovery_spec(candidates=3), idempotency_key="pause-real")
    await service.start(created.run_id)
    paused = await service.pause(created.run_id, reason="user requested")
    repeated = await service.pause(created.run_id)
    assert paused.lifecycle == DiscoveryLifecycle.PAUSED
    assert paused.checkpoint_sequence == repeated.checkpoint_sequence
    resumed = await service.resume(created.run_id, wait=True)
    assert resumed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert resumed.budget.used.proposals == 3
    other = await service.create(discovery_spec(candidates=2), idempotency_key="stop-real")
    await service.start(other.run_id)
    stopped = await service.stop(other.run_id, reason="user requested")
    again = await service.stop(other.run_id)
    assert stopped.lifecycle == DiscoveryLifecycle.STOPPED
    assert again.checkpoint_sequence == stopped.checkpoint_sequence
    await service.wait(other.run_id)

@pytest.mark.asyncio
async def test_iterations_are_child_run_dag_nodes(tmp_path: Path) -> None:
    service = build_service(tmp_path)
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

def test_old_request_defaults_to_fast_idea_mode(tmp_path: Path) -> None:
    del tmp_path
    spec = discovery_spec().model_dump(mode="json")
    spec.pop("idea_mode")
    assert DiscoveryRunSpec.model_validate(spec).idea_mode == "fast"

@pytest.mark.asyncio
async def test_candidate_hitl_decisions_are_audited_idempotent_and_selectable(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    manual_spec = discovery_spec(candidates=3).model_copy(update={"auto_approve": False})
    created = await service.create(manual_spec, idempotency_key="candidate-hitl")

    waiting = await service.start(created.run_id, wait=True)

    assert waiting.lifecycle == DiscoveryLifecycle.WAITING_HITL
    replay = service.replay(created.run_id)
    elite = next(item for item in replay.candidates if item.status == CandidateStatus.ELITE)
    dominated = [
        item for item in replay.candidates if item.status == CandidateStatus.DOMINATED
    ]
    assert len(dominated) == 2

    rejected = await service.decide_candidate(
        created.run_id,
        elite.candidate_id,
        action="reject",
        actor="researcher",
        reason="reject archive leader",
        idempotency_key="decision-reject",
    )
    approved = await service.decide_candidate(
        created.run_id,
        dominated[0].candidate_id,
        action="approve",
        actor="researcher",
        reason="approve alternative",
        idempotency_key="decision-approve",
    )
    promoted = await service.decide_candidate(
        created.run_id,
        dominated[1].candidate_id,
        action="promote",
        actor="researcher",
        reason="promote diverse candidate",
        idempotency_key="decision-promote",
    )
    repeated = await service.decide_candidate(
        created.run_id,
        dominated[1].candidate_id,
        action="promote",
        actor="researcher",
        reason="promote diverse candidate",
        idempotency_key="decision-promote",
    )

    assert rejected.candidate.status == CandidateStatus.REJECTED
    assert approved.candidate.status == CandidateStatus.DOMINATED
    assert promoted.candidate.status == CandidateStatus.PROMOTED
    assert repeated == promoted
    run = service.run_store.get(created.run_id)
    assert run is not None
    assert (run.root / promoted.audit_ref).exists()
    with pytest.raises(DiscoveryServiceError, match="idempotency key"):
        await service.decide_candidate(
            created.run_id,
            dominated[1].candidate_id,
            action="reject",
            actor="researcher",
            reason="promote diverse candidate",
            idempotency_key="decision-promote",
        )

    completed = await service.resume(created.run_id, wait=True)

    assert completed.lifecycle == DiscoveryLifecycle.COMPLETED
    assert completed.selected_candidate_id == dominated[1].candidate_id
    events = service.replay(created.run_id).events
    decision_events = [
        item
        for item in events
        if item["name"] == DiscoveryEventName.HITL_RESOLVED.value
        and item["payload"].get("scope") == "candidate"
    ]
    assert [item["payload"]["action"] for item in decision_events] == [
        "reject",
        "approve",
        "promote",
    ]
