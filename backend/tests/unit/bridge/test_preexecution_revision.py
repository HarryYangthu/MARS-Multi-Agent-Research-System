"""Real graph/persistence admission only; no agents or research results are fabricated."""
from pathlib import Path

import pytest

from app.bridge.preexecution_revision import enqueue_revision, finish_revision, pending_revision
from app.bridge.workflow_service import build_pipeline
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunStore


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
