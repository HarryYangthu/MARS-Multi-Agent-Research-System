"""Real ASGI, durable state and live process controls; no research substitutes."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
import sqlite3
import sys

import httpx
import pytest

from app.api import dependencies
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.harness.runtime.state_machine import NodeState
from app.main import create_app
from app.storage.run_state_store import RunStateSnapshot, RunStateStore
from tests.unit.test_execution_stage_boundaries import actual_project


@pytest.fixture
async def boundary(tmp_path: Path) -> AsyncIterator[tuple[Orchestrator, RunSession, httpx.AsyncClient]]:
    with actual_project(tmp_path) as ctx:
        session = ctx.orchestrator.create_session(RunRequest(task='actual-boundary',
            project=ctx.session.project, entrypoint='execution',
            extra={'frozen_input_note': 'retain exact original confirmation inputs'}))
        previous = dependencies._run_store, dependencies._orchestrator
        dependencies._run_store, dependencies._orchestrator = ctx.run_store, ctx.orchestrator
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                         base_url='http://actual-api') as client:
                yield ctx.orchestrator, session, client
        finally:
            dependencies._run_store, dependencies._orchestrator = previous


def endpoint(session: RunSession) -> str:
    return f'/api/runs/{session.run.run_id}/execution-boundary'


def assert_same_state(actual: RunStateSnapshot | None, expected: RunStateSnapshot) -> None:
    assert actual is not None
    assert actual.revision == expected.revision and actual.status == expected.status
    assert actual.request == expected.request and actual.termination == expected.termination
    assert actual.graph.to_dict() == expected.graph.to_dict()


@pytest.mark.asyncio
async def test_boundary_api_does_not_need_a_preview_and_is_durable_and_idempotent(
        boundary: tuple[Orchestrator, RunSession, httpx.AsyncClient]) -> None:
    orch, session, client = boundary
    options = session.run.root / 'input/run_request_options.v1.json'
    original = options.read_bytes()
    before = RunStateStore(session.run).load(); assert before is not None
    # There is deliberately no approved delivery or execution plan. This real
    # control must not attempt a scientific preflight or create any job.
    response = await client.get(endpoint(session), params={'project': session.run.project})
    assert response.status_code == 200 and response.json()['can_update']
    payload = {'project': session.run.project, 'stop_after_execution': True}
    responses = await asyncio.gather(*(client.post(endpoint(session), json=payload) for _ in range(12)))
    assert all(item.status_code == 200 and item.json()['stop_after_execution'] for item in responses)
    saved = RunStateStore(session.run).load(); assert saved is not None
    assert saved.revision == before.revision + 1 and saved.status == before.status
    assert saved.graph.to_dict() == before.graph.to_dict() and saved.termination == before.termination
    assert options.read_bytes() == original
    recovered = Orchestrator(run_store=orch.run_store).execution_boundary(session.run.run_id)
    assert recovered['stop_after_execution'] and recovered['can_update']
    assert len(orch.run_store.list()) == 1
    assert not (session.run.root / 'execution/jobs').exists()
    assert not (session.run.root / 'execution/local_commands').exists()


@pytest.mark.asyncio
async def test_running_real_process_is_not_interrupted_and_completion_blocks_writing(
        boundary: tuple[Orchestrator, RunSession, httpx.AsyncClient], tmp_path: Path) -> None:
    orch, session, client = boundary
    ready, release, result = (tmp_path / name for name in ('ready', 'release', 'calculation.json'))
    script = '''import json,os,sys,time
from pathlib import Path
ready,release,result = map(Path,sys.argv[1:])
ready.write_text(str(os.getpid()))
while not release.exists(): time.sleep(.01)
result.write_text(json.dumps({'sum_of_squares':sum(i*i for i in range(100))}))
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', script,
        str(ready), str(release), str(result))
    async def wait_actual_process() -> None:
        assert await process.wait() == 0
    await orch._transition(session, 'execution', NodeState.RUNNING)
    assert orch._spawn_owned(session, 'actual-process', wait_actual_process)
    owner = orch.owned_tasks.active(session.run.run_id); assert owner is not None
    try:
        for _ in range(1000):
            if ready.exists(): break
            assert process.returncode is None
            await asyncio.sleep(.01)
        assert ready.exists() and int(ready.read_text()) == process.pid
        before = RunStateStore(session.run).load(); assert before is not None
        response = await client.post(endpoint(session), json={'project': session.run.project,
                                                            'stop_after_execution': True})
        assert response.status_code == 200, response.text
        saved = RunStateStore(session.run).load(); assert saved is not None
        assert saved.status == before.status == 'running' and saved.termination == before.termination
        assert saved.graph.to_dict() == before.graph.to_dict()
        assert process.returncode is None and orch.owned_tasks.active(session.run.run_id) is owner
        release.write_text('allow the actual calculation to finish')
        await asyncio.wait_for(asyncio.shield(owner), timeout=10)
        assert json.loads(result.read_text()) == {'sum_of_squares': 328350}
        # This is an explicit host state-machine boundary check after a real
        # local process, not a claim of a training or scientific measurement.
        await orch._transition(session, 'execution', NodeState.DONE)
        await orch._after_execution(session, 'execution')
        await orch._advance(session, 'writing')
        snapshot = RunStateStore(session.run).load(); assert snapshot is not None
        assert snapshot.status == 'waiting_feedback'
        assert snapshot.graph.state('writing') == NodeState.PENDING
        assert not (session.run.root / 'writing/research_report.v1.md').exists()
        assert not (session.run.root / 'agent_traces/writing').exists()
    finally:
        release.touch(exist_ok=True)
        if process.returncode is None:
            process.terminate()
        await process.wait()
        if not owner.done():
            await asyncio.wait_for(asyncio.shield(owner), timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', [NodeState.RUNNING, NodeState.WAITING_REVIEW, NodeState.DONE, NodeState.FAILED])
async def test_started_downstream_stage_cannot_be_retroactively_paused(
        boundary: tuple[Orchestrator, RunSession, httpx.AsyncClient], state: NodeState) -> None:
    orch, session, client = boundary
    session.graph.restore_state('writing', state)
    orch._persist_state(session, status='running')
    before = RunStateStore(session.run).load(); assert before is not None
    view = await client.get(endpoint(session), params={'project': session.run.project})
    assert view.status_code == 200 and not view.json()['can_update'] and view.json()['reason']
    response = await client.post(endpoint(session), json={'project': session.run.project,
                                                        'stop_after_execution': True})
    assert response.status_code == 409
    after = RunStateStore(session.run).load(); assert after is not None
    assert_same_state(after, before)
    assert 'stop_after' not in session.request.extra


@pytest.mark.asyncio
async def test_storage_failure_never_acknowledges_or_leaves_speculative_control(
        boundary: tuple[Orchestrator, RunSession, httpx.AsyncClient]) -> None:
    _, session, client = boundary
    before = RunStateStore(session.run).load(); assert before is not None
    # A real SQLite rejection exercises failed persistence, without replacing
    # the store, service or tool with a successful/failed response substitute.
    with sqlite3.connect(session.run.root / 'run_state.sqlite3') as connection:
        connection.execute("CREATE TRIGGER reject_state_update BEFORE UPDATE ON run_state BEGIN SELECT RAISE(ABORT, 'actual storage rejection'); END")
    response = await client.post(endpoint(session), json={'project': session.run.project,
                                                        'stop_after_execution': True})
    assert response.status_code != 200
    assert 'stop_after' not in session.request.extra
    saved = RunStateStore(session.run).load(); assert saved is not None
    assert_same_state(saved, before)
    view = await client.get(endpoint(session), params={'project': session.run.project})
    assert view.status_code == 200 and not view.json()['stop_after_execution']


@pytest.mark.asyncio
async def test_boundary_rejects_another_project_without_changes(
        boundary: tuple[Orchestrator, RunSession, httpx.AsyncClient]) -> None:
    _, session, client = boundary
    before = RunStateStore(session.run).load(); assert before is not None
    assert (await client.get(endpoint(session), params={'project': 'other'})).status_code == 409
    assert (await client.post(endpoint(session), json={'project': 'other',
                    'stop_after_execution': True})).status_code == 409
    assert_same_state(RunStateStore(session.run).load(), before)
