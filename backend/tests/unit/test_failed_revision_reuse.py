"""Read-only regression against the actual failed task, not a new Agent run."""
import json
import os
from pathlib import Path

import pytest

from app.bridge.idea_revision_context import failed_idea_revision_context
from app.harness.agent_loop.revision_seed import load_failed_revision_seed


def test_missing_or_unresolved_execution_does_not_gain_reading_evidence(tmp_path: Path) -> None:
    assert load_failed_revision_seed(tmp_path, project='p', agent='idea', schema='proposal.v1') is None
    path = tmp_path / 'agent_traces/idea/unresolved/checkpoint.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'status': 'protocol_exhausted', 'pending': 'tool',
                               'counts': {'model_responses': 0}, 'candidate': 'Authored negative input'}))
    assert failed_idea_revision_context(tmp_path, 'p') == {}


def test_actual_rejected_draft_restores_verified_literature_and_not_acceptance() -> None:
    configured = os.environ.get('MARS_TEST_PROTOCOL_RECOVERY_RUN')
    if not configured:
        pytest.skip('requires actual rejected candidate and reading receipts')
    root = Path(configured).resolve()
    files = [root / 'run_state.json', root / 'resources/model_budget.v1.json',
             *list((root / 'agent_traces/idea').glob('*/checkpoint.json'))]
    before = {path: path.read_bytes() for path in files}
    project = json.loads((root / 'run_meta.json').read_text())['project']
    seed = load_failed_revision_seed(root, project=project, agent='idea', schema='proposal.v1')
    assert seed is not None and seed.observations
    assert seed.receipt['source_status'] == 'protocol_exhausted'
    assert seed.receipt['acceptance_inherited'] is False and seed.receipt['tool_calls_replayed'] is False
    assert seed.receipt['candidate_ref'].endswith('#candidate')
    for reading in seed.observations:
        assert reading['tool'].startswith('search.')
        assert not any(key.startswith('native_') for key in reading)
        original = json.loads(Path(reading['raw_ref']).read_text())
        assert reading['output'] == original['output']
        assert reading['args'] == original['args']
        assert reading['evidence_origin']['invocation_id']
    material = failed_idea_revision_context(root, project)
    assert seed.candidate in material['revision_candidate']
    assert 'NOT accepted or approved' in material['revision_candidate']
    assert load_failed_revision_seed(root, project='another-project', agent='idea', schema='proposal.v1') is None
    assert {path: path.read_bytes() for path in files} == before


@pytest.mark.asyncio
async def test_restored_actual_draft_is_revalidated_against_current_requirements() -> None:
    configured = os.environ.get('MARS_TEST_PROTOCOL_RECOVERY_RUN')
    if not configured:
        pytest.skip('requires actual rejected candidate and verified source receipts')
    from app.agents.base import RunRequest
    from app.agents.idea.focused_agent import FocusedIdeaAgent
    root = Path(configured).resolve()
    project = json.loads((root / 'run_meta.json').read_text())['project']
    seed = load_failed_revision_seed(root, project=project, agent='idea', schema='proposal.v1')
    assert seed is not None
    agent = FocusedIdeaAgent()
    request = RunRequest(project=project, user_request='Revalidate the original unapproved draft',
        extra={'run_root': str(root), 'idea_requirements': {'require_evaluation_protocol': True}})
    errors = await agent.validate_candidate(request, seed.candidate, list(seed.observations))
    assert any('train and held-out datasets cannot share' in issue for issue in errors)
    assert any('training objectives' in issue for issue in errors)
