"""Unapproved source reuse from actual quota-failure archives, without model calls."""
import json
import os
from pathlib import Path

import pytest

from app.bridge.idea_revision_context import failed_idea_revision_context


def test_unknown_model_outcome_cannot_become_revision_material(tmp_path: Path) -> None:
    path = tmp_path / 'agent_traces/idea/unresolved/checkpoint.json'
    path.parent.mkdir(parents=True)
    # Caller-authored negative state, with no execution or successful response claimed.
    path.write_text(json.dumps({'status': 'model_error', 'pending': 'model',
                              'counts': {'model_responses': 0}, 'candidate': 'No accepted source'}))
    assert failed_idea_revision_context(tmp_path, 'pimc') == {}


def test_real_quota_draft_is_reused_only_as_unapproved_candidate_and_historical_index() -> None:
    configured = os.environ.get('MARS_TEST_QUOTA_TRACE')
    if not configured:
        pytest.skip('requires an actual quota-failure checkpoint and receipts')
    trace = Path(configured).resolve()
    root = trace.parents[2]
    state_path, ledger_path = trace / 'checkpoint.json', root / 'resources/model_budget.v1.json'
    before = state_path.read_bytes(), ledger_path.read_bytes()
    state = json.loads(before[0])
    project = state['context_metadata']['task_contract']['project']
    material = failed_idea_revision_context(root, project)
    assert state['candidate'] in material['revision_candidate']
    assert 'NOT accepted or approved' in material['revision_candidate']
    assert 'not observations of this new invocation' in material['revision_reading_index']
    assert trace.name in material['revision_reading_index']
    assert failed_idea_revision_context(root, 'another-project') == {}
    assert (state_path.read_bytes(), ledger_path.read_bytes()) == before
