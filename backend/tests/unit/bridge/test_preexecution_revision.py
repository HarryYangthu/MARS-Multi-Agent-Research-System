"""Real graph/persistence admission only; no agents or research results are fabricated."""
from pathlib import Path

import pytest

from app.bridge.preexecution_revision import enqueue_revision, finish_revision, pending_revision
from app.bridge.workflow_service import build_pipeline, ready_batch
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunStore


def test_data_revision_preserves_history_and_requires_both_reviews(tmp_path: Path) -> None:
    import json
    from app.bridge.preexecution_revision import apply_revision_data
    from app.storage.data_source_store import sha256_file
    run = RunStore(tmp_path / 'runs').create(task='data-change', project='synthetic_regression')
    graph = build_pipeline('pipeline')
    for stage in ('idea', 'experiment', 'coding'):
        graph.restore_state(stage, NodeState.DONE)
    graph.restore_state('execution', NodeState.APPROVED)
    artifact = run.root / 'experiment/experiment_plan.approved.md'
    artifact.write_text('Historical human-authored plan')
    data = tmp_path / 'data.json'
    data.write_text('[1, 2, 3]')
    source = {'id': 'selected', 'project': run.project, 'stored_path': str(data), 'checksum': sha256_file(data)}
    row = enqueue_revision(run, graph, 'coding', 'execution', 'Change dataset and check the loader', data_source=source)
    assert row['nodes'] == ['experiment_attempt_2', 'coding_attempt_2']
    assert graph.ready_nodes() == ['experiment_attempt_2']
    assert row['previous_data_source'] is None
    assert apply_revision_data(run, row) == source
    assert json.loads((run.root / 'input/selected_data_source.json').read_text()) == source
    graph.restore_state('experiment_attempt_2', NodeState.FAILED)
    assert ready_batch(graph, 2) == []
    graph.restore_state('experiment_attempt_2', NodeState.WAITING_REVIEW)
    assert ready_batch(graph, 2) == []
    graph.restore_state('experiment_attempt_2', NodeState.DONE)
    assert ready_batch(graph, 2) == ['coding_attempt_2']
    graph.restore_state('coding_attempt_2', NodeState.FAILED)
    assert ready_batch(graph, 2) == []
    assert artifact.read_text() == 'Historical human-authored plan'
    with pytest.raises(ValueError, match='未完成'):
        enqueue_revision(run, graph, 'coding', 'execution', 'Different selection', data_source={**source, 'id': 'another'})


@pytest.mark.parametrize('invalid', ['other_project', 'missing', 'changed'])
def test_bad_data_selection_cannot_change_graph_or_inputs(tmp_path: Path, invalid: str) -> None:
    from app.storage.data_source_store import sha256_file
    run = RunStore(tmp_path / 'runs').create(task='bad-data', project='synthetic_regression')
    graph = build_pipeline('pipeline')
    for stage in ('idea', 'experiment', 'coding'):
        graph.restore_state(stage, NodeState.DONE)
    graph.restore_state('execution', NodeState.APPROVED)
    data = tmp_path / 'data.json'
    data.write_text('[1, 2]')
    source = {'project': run.project, 'stored_path': str(data), 'checksum': sha256_file(data)}
    if invalid == 'other_project':
        source['project'] = 'other'
    elif invalid == 'missing':
        data.unlink()
    else:
        data.write_text('[3, 4]')
    before = graph.to_dict()
    with pytest.raises(ValueError):
        enqueue_revision(run, graph, 'coding', 'execution', 'Check file', data_source=source)
    assert graph.to_dict() == before
    assert pending_revision(run) is None
    assert not (run.root / 'input/selected_data_source.json').exists()


def test_prelaunch_repair_preserves_history_and_blocks_downstream(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='prelaunch-admission', project='synthetic_regression', entrypoint='pipeline')
    graph = build_pipeline('pipeline')
    for node in ('idea', 'experiment', 'coding'):
        graph.restore_state(node, NodeState.DONE)
    graph.restore_state('execution', NodeState.APPROVED)
    original = graph.to_dict()
    artifact = run.root / 'coding/code_spec.approved.md'
    artifact.write_text('Human-authored archival bytes, not an execution result.')
    before = artifact.read_bytes()
    row = enqueue_revision(run, graph, 'coding', 'execution', 'Repair an incompatible receipt parser')
    assert row['node'] == 'coding_attempt_2'
    assert row['previous_graph'] == original
    assert graph.state('coding') == NodeState.DONE
    assert graph.state('execution') == NodeState.APPROVED
    assert graph.ready_nodes() == ['coding_attempt_2']
    assert pending_revision(run) == row
    assert enqueue_revision(run, graph, 'coding', 'execution', 'Duplicate') == row
    assert artifact.read_bytes() == before
    graph.transition(row['node'], NodeState.RUNNING)
    graph.transition(row['node'], NodeState.FAILED)
    assert 'writing' not in graph.ready_nodes()
    # Reattaching a persisted request after a crash restores graph edges, without
    # replaying any agent or changing the original completed node.
    restored = type(graph).from_dict(original)
    enqueue_revision(run, restored, 'coding', 'execution', 'Explicit recovery')
    assert restored.ready_nodes() == ['coding_attempt_2']
    finish_revision(run, row)
    assert pending_revision(run) is None
    assert artifact.read_bytes() == before


def test_review_recovery_resumes_execution_before_report(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='resume-reviewed-repair', project='synthetic_regression')
    graph = build_pipeline('coding')
    graph.restore_state('coding', NodeState.DONE)
    graph.restore_state('execution', NodeState.APPROVED)
    row = enqueue_revision(run, graph, 'coding', 'execution', 'Complete the pending data adaptation')
    assert ready_batch(graph, 2) == [row['node']]
    graph.restore_state(row['node'], NodeState.WAITING_REVIEW)
    assert ready_batch(graph, 2) == []
    graph.restore_state(row['node'], NodeState.APPROVED)
    assert ready_batch(graph, 2) == [row['node']]
    graph.restore_state(row['node'], NodeState.DONE)
    restored = type(graph).from_dict(graph.to_dict())
    assert ready_batch(restored, 2) == ['execution']
    for state in (NodeState.RUNNING, NodeState.WAITING_REVIEW, NodeState.FAILED):
        restored.restore_state('execution', state)
        assert ready_batch(restored, 2) == []
    restored.restore_state('execution', NodeState.DONE)
    assert ready_batch(restored, 2) == ['writing']


@pytest.mark.asyncio
@pytest.mark.parametrize('entry', ['scheduler', 'report_retry', 'shutdown_report_retry'])
async def test_missing_execution_intake_does_not_start_report_after_review(tmp_path: Path, entry: str) -> None:
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest

    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry())
    session = orch.create_session(RunRequest(task='resume-missing-intake',
        project='synthetic_regression', entrypoint='coding', review_mode='commander'))
    session.graph.restore_state('coding', NodeState.DONE)
    session.graph.restore_state('execution', NodeState.APPROVED)
    row = enqueue_revision(session.run, session.graph, 'coding', 'execution', 'Inspect recovered continuation')
    session.graph.restore_state(row['node'], NodeState.DONE)
    if entry == 'scheduler':
        await orch._run(session.run.run_id)
        assert session.graph.state('writing') == NodeState.PENDING
    else:
        session.graph.restore_state('writing', NodeState.FAILED)
        if entry == 'shutdown_report_retry':
            import asyncio
            session.termination = {'type': 'cancelled', 'reason': 'server_shutdown',
                'scope': 'owned_async_tasks', 'cleanup_complete': True, 'interrupted_nodes': []}
            result = await orch.request_artifact_revision(run_id=session.run.run_id, agent='writing',
                reason='Explicit retry after service update', restart_stopped=True)
            assert result['ok']
            owner = orch.owned_tasks.active(session.run.run_id)
            assert owner is not None
            await asyncio.wait_for(owner, timeout=20)
        else:
            await orch._run_revision_flow(session=session, node_key='writing', reason='Explicit recovery')
        assert session.graph.state('writing') == NodeState.FAILED
    # Real missing-agent failure. No provider response, training result or
    # approval is substituted, and downstream task binding never happens.
    assert session.graph.state('execution') == NodeState.FAILED
    assert not (session.run.root / 'input/task_contracts/writing.json').exists()
    assert not (session.run.root / 'execution/batch_summary.json').exists()


@pytest.mark.parametrize('record', ['confirmations/approval.json', 'jobs/admitted.json',
                                  'local_commands/experiment/invocation/job.json', 'batch_summary.json'])
def test_existing_launch_evidence_rejects_repair_without_mutation(tmp_path: Path, record: str) -> None:
    run = RunStore(tmp_path).create(task='admission-block', project='synthetic_regression', entrypoint='pipeline')
    graph = build_pipeline('pipeline')
    for node in ('idea', 'experiment', 'coding'):
        graph.restore_state(node, NodeState.DONE)
    graph.restore_state('execution', NodeState.APPROVED)
    before = graph.to_dict()
    evidence = run.root / 'execution' / record
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('Uninterpreted existing record; conservative rejection.')
    with pytest.raises(ValueError):
        enqueue_revision(run, graph, 'coding', 'execution', 'Must not relaunch experiments')
    assert graph.to_dict() == before
    assert pending_revision(run) is None


def test_other_active_stage_prevents_repair(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='active-stage', project='synthetic_regression', entrypoint='pipeline')
    graph = build_pipeline('pipeline')
    graph.restore_state('coding', NodeState.DONE)
    graph.restore_state('execution', NodeState.APPROVED)
    graph.restore_state('experiment', NodeState.RUNNING)
    with pytest.raises(ValueError, match='活动'):
        enqueue_revision(run, graph, 'coding', 'execution', 'Not a safe review boundary')
    assert pending_revision(run) is None


@pytest.mark.asyncio
async def test_real_missing_agent_failure_never_advances_execution(tmp_path: Path) -> None:
    import asyncio
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest

    # Explicitly missing dependencies must fail through the real driver. No
    # provider, tool, review response or scientific result is substituted.
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry())
    session = orch.create_session(RunRequest(task='prelaunch-missing-agent',
        project='synthetic_regression', entrypoint='pipeline', review_mode='commander'))
    for node in ('idea', 'experiment', 'coding'):
        session.graph.restore_state(node, NodeState.DONE)
    session.graph.restore_state('execution', NodeState.APPROVED)
    result = await orch.request_artifact_revision(run_id=session.run.run_id, agent='coding',
                                                 reason='Explicit admission test with no configured agent')
    assert result['ok'] and result['node'] == 'coding_attempt_2'
    owner = orch.owned_tasks.active(session.run.run_id)
    assert owner is not None
    await asyncio.wait_for(owner, timeout=20)
    assert session.graph.state('coding_attempt_2') == NodeState.FAILED
    assert session.graph.state('coding') == NodeState.DONE
    assert session.graph.state('execution') == NodeState.APPROVED
    assert session.graph.state('writing') == NodeState.PENDING
    assert not (session.run.root / 'execution/local_commands').exists()
    from app.bridge.managed_review import review_state
    state = review_state(session.run)
    assert state is not None and state['node'] == 'coding_attempt_2'


@pytest.mark.asyncio
async def test_data_revision_after_shutdown_cannot_skip_failed_design(tmp_path: Path) -> None:
    import asyncio
    import json
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest
    from app.storage.data_source_store import sha256_file
    orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), registry=AgentRegistry())
    session = orch.create_session(RunRequest(task='data-revision-failure', project='synthetic_regression',
                                            entrypoint='experiment', review_mode='commander'))
    for stage in ('experiment', 'coding'):
        session.graph.restore_state(stage, NodeState.DONE)
    session.graph.restore_state('execution', NodeState.APPROVED)
    session.termination = {'type': 'cancelled', 'scope': 'owned_async_tasks', 'cleanup_complete': True,
                           'interrupted_nodes': [], 'reason': 'server_shutdown'}
    data = tmp_path / 'data.json'
    data.write_text('[1, 2]')
    source = {'project': session.run.project, 'stored_path': str(data), 'checksum': sha256_file(data)}
    result = await orch.request_artifact_revision(run_id=session.run.run_id, agent='experiment',
        reason='User-selected data requires design and code review', restart_stopped=True, data_source=source)
    assert result['ok']
    owner = orch.owned_tasks.active(session.run.run_id)
    assert owner is not None
    await asyncio.wait_for(owner, timeout=20)
    assert session.graph.state('experiment_attempt_2') == NodeState.FAILED
    assert session.graph.state('coding_attempt_2') == NodeState.PENDING
    assert session.graph.state('experiment') == NodeState.DONE
    assert session.graph.state('execution') == NodeState.APPROVED
    assert session.graph.state('writing') == NodeState.PENDING
    assert json.loads((session.run.root / 'input/selected_data_source.json').read_text()) == source
    assert not (session.run.root / 'execution/confirmations').exists()
