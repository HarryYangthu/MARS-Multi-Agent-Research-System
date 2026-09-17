"""Real CPU composition, OS leases and asyncio cleanup; no execution substitutes."""
from __future__ import annotations

import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from app.bridge.discovery_service import DiscoveryServiceError
from app.bridge.discovery_types import DiscoveryLifecycle
from app.harness.discovery.protocol import DiscoveryEventName
from app.storage.discovery_checkpoint_store import CheckpointStatus
from tests.real_discovery import build_service, discovery_spec


def _persisted_records(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*.json")}


async def _attempt_foreign_control(root: Path, run_id: str) -> tuple[str, ...]:
    other = build_service(root)
    errors: list[str] = []
    for operation in (other.resume, other.pause, other.stop):
        try:
            await operation(run_id)
        except DiscoveryServiceError as exc:
            errors.append(exc.detail)
        else:
            raise AssertionError("a foreign service changed an owned execution")
    assert other._control(run_id).task is None
    assert other._control(run_id).lease is None
    return tuple(errors)


def _foreign_worker(root: Path, run_id: str) -> tuple[str, ...]:
    return asyncio.run(_attempt_foreign_control(root, run_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_process", [False, True])
async def test_foreign_worker_cannot_resume_pause_stop_or_recover_owned_records(
    tmp_path: Path, separate_process: bool,
) -> None:
    service = build_service(tmp_path)
    created = await service.create(discovery_spec(candidates=1), idempotency_key="execution-owner")
    run_id = created.run_id
    await service.start(run_id)
    control = service._control(run_id)
    assert control.task is not None and not control.task.done() and control.lease is not None
    # Preserve the actual run task while a separate process starts up. Its gate
    # is the production pause mechanism, and the execution lease must survive it.
    if separate_process:
        await service.pause(run_id)
    context = service._context(run_id)
    before = _persisted_records(context.run.root / "discovery")
    before_budget = context.stores.budget.snapshot()
    try:
        if separate_process:
            with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
                future = pool.submit(_foreign_worker, tmp_path, run_id)
                errors = await asyncio.wait_for(asyncio.to_thread(future.result, 15), timeout=20)
        else:
            errors = await _attempt_foreign_control(tmp_path, run_id)
        assert len(errors) == 3 and all("execution_owned_elsewhere" in error for error in errors)
        assert _persisted_records(context.run.root / "discovery") == before
        assert context.stores.budget.snapshot() == before_budget
        assert control.task is not None and not control.task.done() and control.lease is not None
        # The original owner can still perform the real CPU fit afterwards.
        finished = await service.resume(run_id, wait=True)
        assert finished.lifecycle == DiscoveryLifecycle.COMPLETED
        assert finished.evaluated_count == finished.budget.used.proposals == 1
        assert finished.budget.used.wall_seconds > 0
        assert control.lease is None
    finally:
        if control.task is not None and not control.task.done():
            await service.stop(run_id)


@pytest.mark.asyncio
async def test_stop_before_first_iteration_cancels_task_and_commits_cleanup_before_stopped(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    created = await service.create(discovery_spec(candidates=1), idempotency_key="cancel-before-first-step")
    run_id = created.run_id
    await service.start(run_id)
    control = service._control(run_id)
    task = control.task
    assert task is not None and not task.done()
    assert service.status(run_id).candidate_count == 0
    assert service.status(run_id).iteration_nodes == ()
    stopped = await service.stop(run_id, reason="contract stop")
    assert task.done() and task.cancelled()
    assert control.cleanup_task is not None and control.cleanup_task.done()
    assert control.cleanup_task.exception() is None
    assert stopped.lifecycle == DiscoveryLifecycle.STOPPED
    assert "cleanup_complete" in stopped.stop_details
    assert stopped.evaluated_count == stopped.candidate_count == stopped.budget.used.proposals == 0
    assert stopped.budget.active_slots == ()
    assert control.lease is None
    context = service._context(run_id)
    with FileLock(context.run.root / "discovery" / ".execution.lock", timeout=0):
        pass
    events = [event for event in context.events.replay() if event.name == DiscoveryEventName.RUN_STOPPED]
    assert len(events) == 1 and events[0].payload["cleanup_complete"] is True
    again = await service.stop(run_id)
    waited = await service.wait(run_id)
    assert again.checkpoint_sequence == waited.checkpoint_sequence == stopped.checkpoint_sequence
    assert again.lifecycle == waited.lifecycle == DiscoveryLifecycle.STOPPED


async def _wait_for_task(task: asyncio.Task[None]) -> None:
    done, _ = await asyncio.wait({task}, timeout=3)
    assert task in done


@pytest.mark.asyncio
async def test_incomplete_stop_and_disconnected_waiters_do_not_interrupt_real_cleanup(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    created = await service.create(discovery_spec(candidates=1), idempotency_key="cleanup-lifecycle")
    run_id = created.run_id
    context, control = service._context(run_id), service._control(run_id)
    # Exercise lifecycle ownership directly with actual asyncio tasks and file
    # leases. No model, candidate generator, adapter or fit result is replaced.
    context.stores.checkpoints.resume()
    service._acquire_execution_lease(context, control)
    context.stores.budget.acquire_slot(lease_id="lifecycle-reservation", candidate_id="lifecycle-owner")
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0

    async def lifecycle_wait() -> None:
        nonlocal cancellations
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cleaning.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellations += 1
                raise
            raise

    task = asyncio.create_task(lifecycle_wait())
    control.task = task
    task.add_done_callback(lambda completed: service._execution_finished(context, control, completed))
    await started.wait()
    try:
        with pytest.raises(DiscoveryServiceError, match="stop_incomplete"):
            await service.stop(run_id, grace_seconds=0)
        await cleaning.wait()
        first_checkpoint = context.stores.checkpoints.latest()
        assert first_checkpoint is not None
        assert first_checkpoint.status == CheckpointStatus.RUNNING and first_checkpoint.reason == "stop_requested"
        assert service.status(run_id).lifecycle == DiscoveryLifecycle.RUNNING
        assert len(context.stores.budget.snapshot().active_slots) == 1
        assert task.cancelling() == cancellations == 1 and not task.done()
        assert control.lease is not None
        with pytest.raises(Timeout):
            FileLock(context.run.root / "discovery" / ".execution.lock", timeout=0).acquire()
        with pytest.raises(DiscoveryServiceError, match="stop_incomplete"):
            await service.stop(run_id, grace_seconds=0)
        assert context.stores.checkpoints.latest() == first_checkpoint
        assert task.cancelling() == cancellations == 1 and not task.done()
        for operation in (service.start, service.resume, service.pause):
            with pytest.raises(DiscoveryServiceError, match="stop_incomplete"):
                await operation(run_id)

        waiter = asyncio.create_task(service.stop(run_id, grace_seconds=10))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert task.cancelling() == cancellations == 1 and not task.done()
        assert service.status(run_id).lifecycle == DiscoveryLifecycle.RUNNING
        assert context.stores.checkpoints.latest() == first_checkpoint

        # The public wait method must likewise propagate its caller's own
        # cancellation without forwarding that cancellation to the owned task.
        status_waiter = asyncio.create_task(service.wait(run_id))
        await asyncio.sleep(0)
        status_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await status_waiter
        assert task.cancelling() == cancellations == 1 and not task.done()
    finally:
        release.set()
        await _wait_for_task(task)
        assert control.cleanup_task is not None
        await _wait_for_task(control.cleanup_task)
        control.cleanup_task.result()
    assert task.cancelled() and cancellations == 1
    stopped = await service.wait(run_id)
    assert stopped.lifecycle == DiscoveryLifecycle.STOPPED and "cleanup_complete" in stopped.stop_details
    assert stopped.budget.active_slots == () and control.lease is None
    assert stopped.evaluated_count == stopped.candidate_count == stopped.budget.used.proposals == 0
    events = [event for event in context.events.replay() if event.name == DiscoveryEventName.RUN_STOPPED]
    assert len(events) == 1 and events[0].payload["cleanup_complete"] is True
    with FileLock(context.run.root / "discovery" / ".execution.lock", timeout=0):
        pass
