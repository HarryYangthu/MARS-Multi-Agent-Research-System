"""Real-file structural binding contracts; no model/tool substitutes or training."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.delivery import delivery_errors
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.bridge.handoff_context_bindings import bind_legacy_input
from app.bridge.task_runtime import admit_handoffs, inspect_handoffs
from app.harness.runtime.task_contract import HandoffBlockedError, HandoffPrerequisite, missing_prerequisites
from app.harness.schema.frontmatter_parser import dumps
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunHandle, RunStore


def requirement(**extra: Any) -> dict[str, Any]:
    return {'kind': 'other', 'description': 'Caller-owned initialization file',
            'reason': 'Explicitly required by the caller', 'blocks_execution': True, **extra}


def saved_input(root: Path) -> tuple[RunHandle, dict[str, str]]:
    run = RunStore(root / 'runs').create(task='binding-contract', project='pimc')
    source = run.root / 'idea/idea_proposal.approved.md'
    source.parent.mkdir(exist_ok=True)
    from backend.tests.unit.test_idea_delivery import authored_metadata
    metadata = authored_metadata()
    metadata['handoff']['required_context'] = [requirement()]
    source.write_text(dumps(metadata, metadata['human_summary']))
    path = root / 'caller-input.txt'
    path.write_text('Caller-authored input; no weights or execution result claimed.')
    return run, {'checkpoint': str(path)}


def test_category_is_never_a_wildcard_or_a_prose_inferred_key() -> None:
    unresolved = HandoffPrerequisite.model_validate(requirement())
    supplied = {'checkpoint': '/caller/weights', 'other': 'Arbitrary unrelated input'}
    assert missing_prerequisites([unresolved], stage='execution', supplied_context=supplied) == ['other (unbound)']
    resolved = HandoffPrerequisite.model_validate(requirement(context_ref='checkpoint'))
    assert missing_prerequisites([resolved], stage='execution', supplied_context=supplied) == []
    assert missing_prerequisites([resolved], stage='execution', supplied_context={'other': 'Input'}) == ['checkpoint']
    supplied['checkpoint'] = '   '
    assert missing_prerequisites([resolved], stage='execution', supplied_context=supplied) == ['checkpoint']


def test_new_proposals_require_reference_but_old_approved_documents_remain_readable(tmp_path: Path) -> None:
    from backend.tests.unit.test_idea_delivery import authored_metadata
    metadata = authored_metadata()
    metadata['handoff']['required_context'].append(requirement())
    text = dumps(metadata, metadata['human_summary'])
    assert validate_document(text, expected_schema='proposal.v1').valid
    assert any('context_ref' in error for error in delivery_errors(metadata, 'method_proposal'))
    for agent in (IdeaAgent(), FocusedIdeaAgent()):
        schema = agent.submission_schema(RunRequest('pimc', 'Authored input'))
        assert schema is not None
        item = schema['properties']['handoff']['properties']['required_context']['items']
        validator = Draft202012Validator(item)
        assert list(validator.iter_errors(requirement()))
        assert list(validator.iter_errors(requirement(context_ref=None)))
        assert not list(validator.iter_errors(requirement(context_ref='checkpoint')))
    metadata['handoff']['required_context'][-1]['context_ref'] = 'checkpoint'
    assert not delivery_errors(metadata, 'method_proposal')


def test_explicit_binding_preserves_approved_bytes_and_rejects_unknown_inputs(tmp_path: Path) -> None:
    run, supplied = saved_input(tmp_path)
    path = run.root / 'idea/idea_proposal.approved.md'
    original = path.read_bytes()
    before = set(run.root.rglob('*'))
    view = inspect_handoffs(run, 'execution', supplied_context=supplied)
    assert view[0].missing_context == ['other (unbound)']
    assert set(run.root.rglob('*')) == before
    with pytest.raises(HandoffBlockedError, match='context_ref'):
        admit_handoffs(run, 'execution', supplied_context=supplied)
    with pytest.raises(ValueError, match='缺失'):
        bind_legacy_input(run, source_ref='idea/idea_proposal.approved.md', prerequisite_index=0,
            context_ref='unknown', supplied_context=supplied, reason='Explicit structural repair', authorized_by='caller')
    assert not (run.root / 'input/handoff_context_bindings.json').exists()
    resource_hash = hashlib.sha256(Path(supplied['checkpoint']).read_bytes()).hexdigest()
    args = dict(source_ref='idea/idea_proposal.approved.md', prerequisite_index=0, context_ref='checkpoint',
                supplied_context=supplied, reason='Explicit structural repair', authorized_by='caller',
                resource_sha256=resource_hash)
    bind_legacy_input(run, **args)
    receipt = run.root / 'input/handoff_context_bindings.json'
    saved = receipt.read_bytes()
    bind_legacy_input(run, **args)
    assert receipt.read_bytes() == saved
    view = admit_handoffs(run, 'execution', supplied_context=supplied)
    assert not view[0].missing_context and view[0].prerequisites[0].context_ref == 'checkpoint'
    assert view[0].binding_receipts and path.read_bytes() == original
    with pytest.raises(ValueError, match='不能静默覆盖'):
        bind_legacy_input(run, **{**args, 'reason': 'Conflicting replacement'})
    assert receipt.read_bytes() == saved


def test_binding_cannot_relax_requirements_or_override_explicit_references(tmp_path: Path) -> None:
    run, supplied = saved_input(tmp_path)
    path = run.root / 'idea/idea_proposal.approved.md'
    from app.harness.schema.frontmatter_parser import parse
    metadata = parse(path.read_text()).metadata
    for row in (requirement(context_ref='checkpoint'), requirement(kind='baseline_code'),
                requirement(blocks_execution=False)):
        metadata['handoff']['required_context'] = [row]
        path.write_text(dumps(metadata, metadata['human_summary']))
        before = path.read_bytes()
        with pytest.raises(ValueError, match='不能覆盖明确引用或放宽条件'):
            bind_legacy_input(run, source_ref='idea/idea_proposal.approved.md', prerequisite_index=0,
                context_ref='checkpoint', supplied_context=supplied, reason='Unauthorized override', authorized_by='caller')
        assert path.read_bytes() == before
        assert not (run.root / 'input/handoff_context_bindings.json').exists()


def test_recovery_preflights_named_inputs_and_retains_all_upstream_work(tmp_path: Path) -> None:
    from app.agents.execution.agent import ExecutionAgent
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest as WorkflowRequest
    from app.bridge.run_recovery import recovery_status
    from app.harness.runtime.state_machine import NodeState
    from backend.tests.unit.test_idea_delivery import authored_metadata
    registry = AgentRegistry()
    registry.register('execution', ExecutionAgent())
    orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), registry=registry)
    data = tmp_path / 'initialization.txt'
    data.write_text('Caller supplied actual file; not an experiment result.')
    supplied = {'checkpoint': str(data), 'data_description': 'Caller-authored input description'}
    session = orch.create_session(WorkflowRequest(task='handoff-admission', project='pimc',
        entrypoint='execution', standalone=True, extra={'execution_context': supplied}))
    session.graph.restore_state('execution', NodeState.FAILED)
    run = session.run
    source = run.root / 'idea/idea_proposal.approved.md'
    metadata = authored_metadata()
    metadata['handoff']['required_context'] = [requirement()]
    source.write_text(dumps(metadata, metadata['human_summary']))
    failure = run.root / 'input/node_failures/execution.json'
    failure.parent.mkdir(exist_ok=True)
    failure.write_text(json.dumps({'code': 'handoff_context_missing'}))
    original = source.read_bytes()
    paths = set(run.root.rglob('*'))
    blocked = recovery_status(orch, run.run_id, project=run.project)
    assert blocked['status'] == 'blocked' and not blocked['actions']
    assert 'context_ref' in blocked['message']
    assert paths == set(run.root.rglob('*'))
    bind_legacy_input(run, source_ref='idea/idea_proposal.approved.md', prerequisite_index=0,
        context_ref='checkpoint', supplied_context=supplied, reason='Explicit file binding', authorized_by='caller',
        resource_sha256=hashlib.sha256(data.read_bytes()).hexdigest())
    repaired = recovery_status(orch, run.run_id, project=run.project)
    assert repaired['status'] == 'recoverable' and repaired['actions'][0]['action'] == 'retry'
    assert '绑定已核验' in repaired['message'] and repaired['token'] != blocked['token']
    assert session.graph.state('execution') == NodeState.FAILED and source.read_bytes() == original
    data.write_text('Changed source: permission must be invalidated.')
    stale = recovery_status(orch, run.run_id, project=run.project)
    assert stale['status'] == 'blocked' and not stale['actions'] and '内容已改变' in stale['message']


@pytest.mark.parametrize('change', ['proposal', 'input', 'file', 'project', 'duplicate'])
def test_stale_binding_never_authorizes_changed_inputs(tmp_path: Path, change: str) -> None:
    run, supplied = saved_input(tmp_path)
    path = run.root / 'idea/idea_proposal.approved.md'
    bind_legacy_input(run, source_ref='idea/idea_proposal.approved.md', prerequisite_index=0,
        context_ref='checkpoint', supplied_context=supplied, reason='Explicit selection', authorized_by='caller',
        resource_sha256=hashlib.sha256(Path(supplied['checkpoint']).read_bytes()).hexdigest())
    receipt = run.root / 'input/handoff_context_bindings.json'
    if change == 'proposal':
        path.write_text(path.read_text() + '\nChanged approved bytes\n')
    elif change == 'input':
        supplied['checkpoint'] = '/different/file'
    elif change == 'file':
        Path(supplied['checkpoint']).write_text('Changed caller-owned input')
    else:
        raw = json.loads(receipt.read_text())
        if change == 'project':
            raw['project'] = 'another-project'
        else:
            raw['bindings'].append(raw['bindings'][0])
        receipt.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        inspect_handoffs(run, 'execution', supplied_context=supplied)


def test_actual_archived_failure_requires_explicit_checkpoint_binding() -> None:
    configured = os.environ.get('MARS_TEST_HANDOFF_RUN')
    if not configured:
        pytest.skip('requires the actual approved project handoff and input archive')
    from app.bridge.agent_runner import load_agent_handoff_context
    root = Path(configured).resolve()
    run = RunStore(root.parent).get(root.name)
    assert run is not None
    paths = [root / 'run_state.json', root / 'resources/model_budget.v1.json',
             *root.glob('*/**/*.approved.md')]
    before = {path: path.read_bytes() for path in paths}
    supplied, _ = load_agent_handoff_context(run, 'execution')
    view = inspect_handoffs(run, 'execution', supplied_context=supplied)
    if (root / 'input/handoff_context_bindings.json').is_file():
        assert not view[0].missing_context and view[0].binding_receipts
        assert any(item.context_ref == 'checkpoint' for item in view[0].prerequisites)
    else:
        assert view[0].missing_context == ['other (unbound)']
    assert {path: path.read_bytes() for path in paths} == before
