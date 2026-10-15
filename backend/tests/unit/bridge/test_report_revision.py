"""Report correction admission and actual missing-agent failure, no success substitute."""
from pathlib import Path

import pytest

from app.bridge.report_revision import append_report_revision
from app.bridge.workflow_service import build_pipeline
from app.harness.runtime.state_machine import NodeState


def test_report_revision_keeps_history_and_only_schedules_new_report() -> None:
    graph = build_pipeline('pipeline')
    for node in graph.nodes:
        graph.restore_state(node, NodeState.DONE)
    before = graph.to_dict()
    revised, node = append_report_revision(graph, 'writing', 'Correct evidence citations')
    assert node == 'writing_attempt_2'
    assert graph.to_dict() == before
    assert revised.state('writing') == NodeState.DONE
    assert revised.state('execution') == NodeState.DONE
    assert revised.ready_nodes() == [node]
    restored = type(graph).from_dict(revised.to_dict())
    assert restored.metadata(node)['report_revision']['reason'] == 'Correct evidence citations'
    with pytest.raises(ValueError):
        append_report_revision(revised, 'writing', 'Cannot duplicate a pending correction')


@pytest.mark.parametrize('stage,state', [('execution', NodeState.FAILED), ('coding', NodeState.RUNNING),
                                       ('writing', NodeState.WAITING_REVIEW)])
def test_report_revision_rejects_incomplete_run_without_mutation(stage: str, state: NodeState) -> None:
    graph = build_pipeline('pipeline')
    for node in graph.nodes:
        graph.restore_state(node, NodeState.DONE)
    graph.restore_state(stage, state)
    before = graph.to_dict()
    with pytest.raises(ValueError):
        append_report_revision(graph, 'writing', 'Invalid correction boundary')
    assert graph.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('after_clean_stop', [False, True])
async def test_failed_report_correction_never_restarts_completed_work(tmp_path: Path, after_clean_stop: bool) -> None:
    import asyncio
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest
    from app.storage.run_store import RunStore

    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry())
    session = orch.create_session(RunRequest(task='report-correction-admission',
        project='synthetic_regression', entrypoint='pipeline', review_mode='commander'))
    for node in session.graph.nodes:
        session.graph.restore_state(node, NodeState.DONE)
    artifact = session.run.root / 'writing/research_report.approved.md'
    artifact.write_text('Archived document bytes, not a model or experimental result.')
    before = artifact.read_bytes()
    if after_clean_stop:
        # Admission fixture for the persisted clean-shutdown state, not a
        # substitute execution or approval response.
        session.termination = {'type': 'cancelled', 'scope': 'owned_async_tasks',
            'cleanup_complete': True, 'interrupted_nodes': [], 'reason': 'server_shutdown'}
        blocked = await orch.request_artifact_revision(run_id=session.run.run_id, agent='writing', reason='No restart')
        assert not blocked['ok']
    result = await orch.request_artifact_revision(run_id=session.run.run_id, agent='writing',
                                                 reason='Correct citations with no configured agent',
                                                 restart_stopped=after_clean_stop)
    assert result['ok'] and result['node'] == 'writing_attempt_2'
    owner = orch.owned_tasks.active(session.run.run_id)
    assert owner is not None
    await asyncio.wait_for(owner, timeout=20)
    assert session.graph.state('writing_attempt_2') == NodeState.FAILED
    assert session.graph.state('writing') == session.graph.state('execution') == NodeState.DONE
    assert not (session.run.root / 'execution/local_commands').exists()
    assert artifact.read_bytes() == before
