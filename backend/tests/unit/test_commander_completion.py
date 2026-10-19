"""Real local tool/state receipts and pure reducer inputs; no model substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander_completion import (
    TurnGoal, admitted_existing_model_goal, assess_completion, current_turn_ledger, goal_state, parse_turn_goal,
    pending_continuation_prompt, proposed_action_goal, receipt_intervention, restore_goal,
)
from app.bridge.commander_session import ChatMessage, CommanderSession, CommanderSessionStore
from app.bridge.commander_dispatch import pending_existing_admission
from app.bridge.commander import _completion_unfinished_reply
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.kb.stores import reset_for_tests as reset_kb
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.schema.frontmatter_parser import dumps
from app.hitl.review_session import ReviewSession, get_registry
from app.settings import reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


def _user(session: CommanderSession, text: str) -> ChatMessage:
    message = ChatMessage(role='user', content=text)
    session.active_turn_id = message.id
    return session.add(message)


def _goal(session: CommanderSession, effects: tuple[str, ...] = ('run_started',),
          *, target: str = 'new', intent: str = 'action', tools: tuple[str, ...] = ()) -> TurnGoal:
    goal = parse_turn_goal({'intent': intent, 'required_effects': list(effects),
        'run_target': target, 'expected_tools': list(tools)}, session)
    assert goal is not None
    return goal


def _observe(session: CommanderSession, tool: str, result: dict[str, Any],
             args: dict[str, Any] | None = None) -> ChatMessage:
    return session.add(ChatMessage(role='tool', content=json.dumps(result),
        tool_name=tool, tool_args=args or {}, tool_result=result))


@contextmanager
def _actual_project(tmp_path: Path) -> Iterator[ToolContext]:
    values = {'MARS_FOLDER_PROJECTS_REGISTRY': str(tmp_path/'registry.json'),
              'MARS_EXECUTION_CONFIG_PATH': str(tmp_path/'execution.yaml'),
              'MARS_EXECUTION_BACKEND': 'local_command', 'MARS_RUNTIME_MODE': 'development'}
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values); reset_settings_cache()
    try:
        project = open_folder(str(tmp_path/'project'), create=True)
        source = project.root/'code/source'; source.mkdir(parents=True)
        (source/'config.yaml').write_text('model_name: ActualModel\nseed: 2026\n')
        (project.metadata_root/'repo_link.yaml').write_text(yaml.safe_dump({
            'repo_path': str(source), 'allowed_paths': ['config.yaml'], 'read_only': True}))
        (tmp_path/'execution.yaml').write_text(yaml.safe_dump({'execution': {
            'backend': 'local_command', 'local_commands': []}}))
        store = RunStore(tmp_path/'runs', project_storage=True)
        session = CommanderSession(conv_id='completion-real', project=project.name)
        _user(session, '把参数换成另一组，跑完两轮；先准备并保留审核。')
        yield ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()), session, store)
    finally:
        for key, value in saved.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        reset_settings_cache()


@pytest.mark.parametrize('request_text', [
    '把现有五层模型学习率从0.0004改为0.0002，各跑完整2个epoch。',
    '换个学习率，和原来比一下，跑满两轮。',
    'Compare the existing configuration after halving its learning rate.',
    '参数设成这个，来一组对照。',
])
def test_goal_binds_actual_user_without_any_action_word_matching(request_text: str) -> None:
    session = CommanderSession('goal', 'project'); user = _user(session, request_text)
    goal = _goal(session)
    assert goal.source_turn_id == user.id
    assert goal.source_user_sha256 == hashlib.sha256(request_text.encode()).hexdigest()
    verdict = assess_completion(session, goal, '可以，已完成。', 'answer_sufficient')
    assert verdict.status == 'pending' and verdict.missing == ('run_started',)
    assert '未取得' in pending_continuation_prompt(verdict)


@pytest.mark.parametrize('raw', [
    None, [], {'intent': {}, 'run_target': 'new'},
    {'intent': 'action', 'run_target': 'new', 'required_effects': ['imaginary_success']},
    {'intent': 'answer', 'run_target': 'none', 'required_effects': ['run_started']},
    {'intent': 'action', 'run_target': 'new', 'expected_tools': ['invented.export']},
])
def test_unknown_or_model_supplied_source_contract_is_not_authority(raw: Any) -> None:
    session = CommanderSession('goal', 'project'); _user(session, '解释这段文字，不执行。')
    assert parse_turn_goal(raw, session) is None


def test_source_text_change_or_another_project_invalidates_obligations() -> None:
    session = CommanderSession('goal', 'project'); user = _user(session, '跑两轮。'); goal = _goal(session)
    user.content = '只解释，不执行。'
    assert assess_completion(session, goal).status == 'unknown'
    user.content = '跑两轮。'
    assert assess_completion(session, replace(goal, project='other')).status == 'unknown'


def test_model_extra_description_or_source_is_not_execution_authority() -> None:
    session = CommanderSession('goal', 'project'); user = _user(session, '跑两轮。')
    goal = parse_turn_goal({'intent': 'action', 'run_target': 'new',
        'required_effects': ['run_started', 'run_started'], 'reason': 'semantic interpretation',
        'source_turn_id': 'model-selected-old-turn', 'seed': 999}, session)
    assert goal is not None and goal.required_effects == ('run_started',)
    assert goal.source_turn_id == user.id and not hasattr(goal, 'seed')


def test_real_session_persistence_retains_contract_without_claiming_processing(tmp_path: Path) -> None:
    store = CommanderSessionStore(tmp_path/'conversations', project_storage=False)
    session = store.create(project='actual'); _user(session, '先准备两组，再运行。')
    goal = _goal(session); session.completion_state = {'goal': goal_state(goal), 'status': 'pending'}
    session.processing = True; store.persist(session)
    loaded = CommanderSessionStore(tmp_path/'conversations', project_storage=False).get(session.conv_id)
    assert loaded is not None and loaded.processing is False
    assert restore_goal(loaded.completion_state) == goal
    path = tmp_path/'conversations'/session.conv_id/'session.json'
    meta = json.loads(path.read_text()); del meta['completion_state']; path.write_text(json.dumps(meta))
    legacy = CommanderSessionStore(tmp_path/'conversations', project_storage=False).get(session.conv_id)
    assert legacy is not None and legacy.completion_state == {} and restore_goal({}) is None


@pytest.mark.asyncio
async def test_real_read_and_real_create_never_certify_a_run_started(tmp_path: Path) -> None:
    with _actual_project(tmp_path) as ctx:
        goal = _goal(ctx.session)
        result = await execute_tool('code.repo_read_lines', {'path': 'config.yaml'}, ctx)
        assert result['ok']; read = _observe(ctx.session, 'code.repo_read_lines', result, {'path': 'config.yaml'})
        assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'pending'
        observed = _goal(ctx.session, ('observed',), intent='observe', target='none')
        incomplete = assess_completion(ctx.session, observed, run_store=ctx.run_store)
        assert incomplete.status == 'pending' and incomplete.evidence_ids == (read.id,)
        assert assess_completion(ctx.session, observed, '配置已实际读取。',
            'answer_sufficient', run_store=ctx.run_store).status == 'fulfilled'
        created = await execute_tool('run.create', {'entrypoint': 'coding', 'user_request': '仅准备现有参数对照。'}, ctx)
        assert created['ok']; record = _observe(ctx.session, 'run.create', created)
        pending = assess_completion(ctx.session, goal, run_store=ctx.run_store)
        assert pending.status == 'pending' and pending.missing == ('run_started',)
        assert pending.run_id == created['run_id'] and '不得重复创建' in pending_continuation_prompt(pending)
        only_create = replace(goal, required_effects=('run_created',))
        assert assess_completion(ctx.session, only_create, run_store=ctx.run_store).evidence_ids == (record.id,)
        run = ctx.run_store.get(str(created['run_id'])); assert run is not None
        assert not list(run.subdir('execution').iterdir())  # No model or simulation was started.


@pytest.mark.asyncio
async def test_parse_after_actual_create_keeps_start_of_turn_link_snapshot(tmp_path: Path) -> None:
    with _actual_project(tmp_path) as ctx:
        ctx.session.completion_state = {'initial_linked_run_id': None}
        read = await execute_tool('code.repo_read_lines', {'path': 'config.yaml'}, ctx)
        assert read['ok']; _observe(ctx.session, 'code.repo_read_lines', read, {'path': 'config.yaml'})
        created = await execute_tool('run.create', {'entrypoint': 'coding', 'user_request': '仅准备。'}, ctx)
        assert created['ok']; _observe(ctx.session, 'run.create', created)
        assert ctx.session.linked_run_id == created['run_id']
        late = _goal(ctx.session, ('run_created',))
        assert late.target_run_id is None
        assert assess_completion(ctx.session, late, run_store=ctx.run_store).status == 'fulfilled'
        # Explicit old link also wins over a link changed by the current tool.
        ctx.session.completion_state['initial_linked_run_id'] = 'prior-owned-task'
        assert _goal(ctx.session).target_run_id == 'prior-owned-task'
        # Legacy sessions have no snapshot and retain their previous fallback.
        ctx.session.completion_state = {}
        assert _goal(ctx.session).target_run_id == created['run_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('start', [False, True])
async def test_real_pending_admission_generates_only_its_declared_completion_goal(tmp_path: Path, start: bool) -> None:
    with _actual_project(tmp_path) as ctx:
        args = {'config_path': 'config.yaml', 'model_name': 'ActualModel',
                'max_iters': 2, 'budget_unit': 'epochs', 'start': start}
        failed = await execute_tool('run.existing_model', args, ctx)
        assert failed.get('status') == 'execution_registration_required', failed
        _observe(ctx.session, 'run.existing_model', failed, args)
        continued = _user(ctx.session, '继续')
        action = pending_existing_admission(ctx.session)
        assert action == {'tool': 'run.existing_model', 'args': args}
        before = json.dumps(action, sort_keys=True)
        goal = admitted_existing_model_goal(ctx.session, action)
        assert goal is not None and goal.source_turn_id == continued.id
        assert goal.required_effects == (('run_started',) if start else ('run_created',))
        assert goal.expected_tools == ('run.existing_model',)
        assert json.dumps(action, sort_keys=True) == before
        assert not ctx.run_store.list()  # The helper did not create, start or approve anything.


@pytest.mark.parametrize('action', [None, {'tool': 'run.create', 'args': {}},
    {'tool': 'run.existing_model', 'args': {'start': 'false'}}])
def test_pending_goal_helper_refuses_an_unrelated_tool_or_ambiguous_start(action: Any) -> None:
    session = CommanderSession('goal', 'project'); _user(session, '继续')
    assert admitted_existing_model_goal(session, action) is None


@pytest.mark.parametrize(('actions', 'effects', 'target'), [
    ([{'tool': 'run.create', 'args': {}}], ('run_created',), 'new'),
    ([{'tool': 'run.create', 'args': {}}, {'tool': 'run.start', 'args': {}}],
        ('run_created', 'run_started'), 'new'),
    ([{'tool': 'create_and_start_run', 'args': {}}], ('run_created', 'run_started'), 'new'),
    ([{'tool': 'run.existing_model', 'args': {'start': False}}], ('run_created',), 'new'),
    ([{'tool': 'run.existing_model', 'args': {}}], ('run_created', 'run_started'), 'new'),
    ([{'tool': 'run.start', 'args': {}}], ('run_started',), 'existing'),
    ([{'tool': 'run.recover', 'args': {}}], ('run_started',), 'existing'),
])
def test_known_proposed_actions_bind_goal_before_execution_without_rewriting_args(
        actions: list[dict[str, Any]], effects: tuple[str, ...], target: str) -> None:
    session = CommanderSession('proposed', 'project', linked_run_id='initial')
    user = _user(session, '按原来的目标执行，保留真实审批。')
    session.completion_state = {'initial_linked_run_id': 'initial'}
    session.linked_run_id = 'later-link'
    original = json.dumps(actions, sort_keys=True)
    goal = proposed_action_goal(session, actions)
    assert goal is not None and goal.required_effects == effects and goal.run_target == target
    assert goal.source_turn_id == user.id and goal.source_user_sha256 == hashlib.sha256(user.content.encode()).hexdigest()
    assert goal.target_run_id == 'initial' and goal.project == session.project
    assert goal.expected_tools == tuple(action['tool'] for action in actions)
    assert json.dumps(actions, sort_keys=True) == original
    assert assess_completion(session, goal).status == 'pending' and len(session.messages) == 1


@pytest.mark.parametrize('actions', [
    [], None, [{'tool': 'code.repo_read_lines', 'args': {'path': 'config.yaml'}}],
    [{'tool': 'run.unknown', 'args': {}}], [{'tool': 'run.create', 'args': None}],
    [{'tool': 'run.existing_model', 'args': {'start': 'false'}}],
    [{'tool': 'run.create', 'args': {}}, {'tool': 'run.recover', 'args': {}}],
    [{'tool': 'run.start', 'args': {}}, {'tool': 'run.create', 'args': {}}],
    [{'tool': 'run.create', 'args': {}}, {'tool': 'set_metric_targets', 'args': {'targets': {'RES': 1}}}],
])
def test_unsupported_or_incompatible_proposals_do_not_invent_completion(actions: Any) -> None:
    session = CommanderSession('proposed', 'project'); _user(session, '核对这些操作。')
    assert proposed_action_goal(session, actions) is None


@pytest.mark.asyncio
async def test_proposed_creation_goal_is_satisfied_only_by_actual_created_run(tmp_path: Path) -> None:
    with _actual_project(tmp_path) as ctx:
        ctx.session.completion_state = {'initial_linked_run_id': None}
        actions: list[dict[str, Any]] = [{'tool': 'run.create',
            'args': {'entrypoint': 'coding', 'user_request': '只准备，不启动。'}}]
        goal = proposed_action_goal(ctx.session, actions)
        assert goal is not None and goal.target_run_id is None
        assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'pending'
        read = await execute_tool('code.repo_read_lines', {'path': 'config.yaml'}, ctx)
        assert read['ok']; _observe(ctx.session, 'code.repo_read_lines', read, {'path': 'config.yaml'})
        result = await execute_tool('run.create', actions[0]['args'], ctx)
        assert result['ok']; record = _observe(ctx.session, 'run.create', result, actions[0]['args'])
        verdict = assess_completion(ctx.session, goal, run_store=ctx.run_store)
        assert verdict.status == 'fulfilled' and verdict.evidence_ids == (record.id,)
        assert assess_completion(ctx.session, replace(goal, required_effects=('run_started',)),
            run_store=ctx.run_store).status == 'pending'
        run = ctx.run_store.get(str(result['run_id'])); assert run is not None
        assert not list(run.subdir('execution').iterdir())


@pytest.mark.asyncio
async def test_real_registered_state_effect_must_match_predeclared_tool(tmp_path: Path) -> None:
    with _actual_project(tmp_path) as ctx:
        goal = _goal(ctx.session, ('tool_effect',), target='none', tools=('set_metric_targets',))
        result = await execute_tool('set_metric_targets', {'targets': {'RES': 1.25}}, ctx)
        assert result['ok']; record = _observe(ctx.session, 'set_metric_targets', result, {'targets': {'RES': 1.25}})
        assert ctx.session.metric_targets == {'RES': 1.25}
        assert assess_completion(ctx.session, goal).evidence_ids == (record.id,)
        assert assess_completion(ctx.session, replace(goal, expected_tools=())).status == 'pending'
        assert assess_completion(ctx.session, replace(goal, required_effects=('run_started',))).status == 'pending'
        no_request = replace(ctx.session, messages=list(ctx.session.messages[:-1]))
        _observe(no_request, 'set_metric_targets', result, {})
        assert assess_completion(no_request, goal).status == 'pending'


@pytest.mark.asyncio
@pytest.mark.parametrize(('tool', 'action'), [
    ('approve_node', 'approve'), ('reject_node', 'reject'),
    ('artifact.review', 'approve'), ('user.approval', 'reject'),
    ('artifact.review', 'comment'),
])
async def test_actual_review_effect_requires_durable_decision_not_only_ok(
        tmp_path: Path, tool: str, action: str) -> None:
    reset_kb(base=tmp_path / 'knowledge', store='file')
    with _actual_project(tmp_path) as ctx:
        workflow = ctx.orchestrator.create_session(RunRequest(task='real-review',
            project=ctx.session.project, entrypoint='idea', review_mode='manual'))
        ctx.session.linked_run_id = workflow.run.run_id
        _user(ctx.session, '批准这个提案。' if action == 'approve' else '退回这个提案并说明原因。')
        goal = _goal(ctx.session, ('tool_effect',), target='existing', tools=(tool,))
        ref = ArtifactStore(workflow.run).write(text=dumps({
            'schema': 'proposal.v1', 'project': ctx.session.project, 'agent': 'idea',
            'research_question': 'Can a bounded intervention reduce validation error?',
            'hypothesis': 'Moving interpolation nodes may reduce validation error.',
            'novelty': 'A human-authored local review fixture without a scientific result.',
        }, 'Human-authored review input.'))
        review = ReviewSession(workflow.run, 'idea', ref)
        registry = get_registry(); await registry.register(review)
        args = {'run_id': workflow.run.run_id, 'agent': 'idea', 'action': action,
                'reason': 'Needs a concrete experiment', 'text': 'Retain the original metric definition.'}
        try:
            result = await execute_tool(tool, args, ctx)
            assert result.get('ok') is True, result
            record = _observe(ctx.session, tool, result, args)
            if action == 'approve':
                assert review.approval_event.is_set() and review.decision == 'approve'
                approved = ref.path.parent / f'{ref.stem}.approved.md'
                assert approved.read_bytes() == ref.path.read_bytes()
            elif action == 'reject':
                assert review.rejection_event.is_set() and review.decision == 'reject'
            verdict = assess_completion(ctx.session, goal, run_store=ctx.run_store)
            assert verdict.status == 'fulfilled' and verdict.evidence_ids == (record.id,)
            assert assess_completion(ctx.session, replace(goal, expected_tools=()),
                run_store=ctx.run_store).status == 'pending'
            # The real effect remains verifiable after the in-memory review leaves the queue.
            await registry.unregister(workflow.run.run_id, 'idea')
            assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'fulfilled'
            audit = workflow.run.root / 'hitl' / 'review_log.jsonl'
            audit_bytes = audit.read_bytes(); audit.unlink()
            assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'pending'
            audit.write_bytes(audit_bytes)
            if action == 'approve':
                approved.write_text('Changed bytes cannot certify the approved source.')
                assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'pending'
            # Copying an old actual receipt into a later turn cannot reuse its old audit action.
            _user(ctx.session, '再处理这份提案。')
            later = _goal(ctx.session, ('tool_effect',), target='existing', tools=(tool,))
            _observe(ctx.session, tool, result, args)
            assert assess_completion(ctx.session, later, run_store=ctx.run_store).status == 'pending'
            assert not list(workflow.run.subdir('execution').iterdir())
        finally:
            await registry.unregister(workflow.run.run_id, 'idea')


def test_pure_receipt_policy_requires_start_for_same_new_allocation() -> None:
    # Hypothetical policy inputs verify the reducer, not a real executed pipeline.
    session = CommanderSession('goal', 'project'); _user(session, '启动一组任务。'); goal = _goal(session)
    session.linked_run_id = 'new'
    created = _observe(session, 'run.create', {'ok': True, 'run_id': 'new', 'entrypoint': 'coding', 'states': {}})
    assert assess_completion(session, goal).status == 'pending'
    started = _observe(session, 'run.start', {'ok': True, 'run_id': 'new', 'status': 'started'})
    verdict = assess_completion(session, goal)
    assert verdict.status == 'fulfilled' and verdict.evidence_ids == (started.id,)
    assert assess_completion(session, replace(goal, required_effects=('run_created', 'run_started'))).evidence_ids == (
        created.id, started.id)
    assert assess_completion(session, replace(goal, required_effects=('run_created', 'run_started')),
        evidence_ids=(started.id, 'made-up-id')).status == 'fulfilled'


def test_old_link_and_unrelated_success_cannot_fulfil_a_new_goal() -> None:
    session = CommanderSession('goal', 'project', linked_run_id='old'); _user(session, '再比较另一组。')
    goal = _goal(session)
    _observe(session, 'run.start', {'ok': True, 'run_id': 'old', 'status': 'already_running'})
    _observe(session, 'invented.magic', {'ok': True, 'status': 'done'})
    assert assess_completion(session, goal).status == 'pending'
    assert assess_completion(session, replace(goal, run_target='existing')).status == 'fulfilled'


@pytest.mark.parametrize('status', ['resuming', 'revision_started'])
def test_actual_recovery_status_vocabulary_is_limited_to_same_existing_run(status: str) -> None:
    # Reducer inputs use the real resume/revision status vocabulary, without
    # substituting a model, a running process or successful tool execution.
    session = CommanderSession('recovery', 'project', linked_run_id='retained')
    _user(session, '在原任务继续。')
    goal = _goal(session, target='existing')
    record = _observe(session, 'run.recover', {'ok': True, 'run_id': 'retained', 'status': status})
    verdict = assess_completion(session, goal)
    assert verdict.status == 'fulfilled' and verdict.evidence_ids == (record.id,)
    assert assess_completion(session, replace(goal, run_target='new')).status == 'pending'
    record.tool_name = 'run.start'
    assert assess_completion(session, goal).status == 'pending'
    record.tool_name = 'run.recover'; record.tool_result = {'ok': False, 'run_id': 'retained', 'status': status}
    assert assess_completion(session, goal).status == 'pending'
    record.tool_result = {'ok': True, 'run_id': 'unrelated', 'status': status}
    assert assess_completion(session, goal).status == 'pending'


def test_pure_handoff_projection_keeps_child_outcome_pending_in_real_temp_run(tmp_path: Path) -> None:
    # Hypothetical receipt reducer inputs against a real allocated RunStore;
    # this does not substitute a tool call, model or running scientific job.
    with _actual_project(tmp_path) as ctx:
        workflow = ctx.orchestrator.create_session(RunRequest(task='delegation-policy',
            project=ctx.session.project, entrypoint='coding'))
        goal = _goal(ctx.session, ('run_created', 'run_started', 'artifact_updated'))
        ctx.session.linked_run_id = workflow.run.run_id
        states = {key: value.value for key, value in workflow.graph.all_states().items()}
        created = _observe(ctx.session, 'run.create', {'ok': True, 'run_id': workflow.run.run_id,
            'entrypoint': 'coding', 'states': states})
        assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'pending'
        started = _observe(ctx.session, 'run.start', {'ok': True, 'run_id': workflow.run.run_id,
            'status': 'started', 'states': states})
        before = goal_state(goal)
        verdict = assess_completion(ctx.session, goal, run_store=ctx.run_store)
        assert verdict.status == 'delegated' and verdict.missing == ('artifact_updated',)
        assert verdict.evidence_ids == (created.id, started.id) and goal_state(goal) == before
        assert '尚未确认完成' in _completion_unfinished_reply(verdict)
        assert '训练已' not in _completion_unfinished_reply(verdict)
        assert not list(workflow.run.subdir('coding').iterdir())
        assert not list(workflow.run.subdir('execution').iterdir())
        assert assess_completion(ctx.session, goal).status == 'pending'  # No real project/run registry proof.
        assert assess_completion(ctx.session, replace(goal, run_target='existing'),
            run_store=ctx.run_store).status == 'pending'
        assert assess_completion(ctx.session, replace(goal, required_effects=('artifact_updated',)),
            run_store=ctx.run_store).status == 'pending'
        assert assess_completion(ctx.session, replace(goal, required_effects=(
            'run_created', 'run_started', 'exported')), run_store=ctx.run_store).status == 'pending'
        assert assess_completion(ctx.session, replace(goal, required_effects=(
            'run_created', 'run_started', 'artifact_updated', 'tool_effect')),
            run_store=ctx.run_store).status == 'pending'
        assert started.tool_result is not None
        for state in ('skipped', 'failed', None):
            started.tool_result['states'] = {'coding': state}
            assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'pending'
        started.tool_result['states'] = states
        started.tool_result['project'] = 'another-project'
        assert assess_completion(ctx.session, goal, run_store=ctx.run_store).status == 'pending'


@pytest.mark.parametrize('result,status', [
    ({'ok': False, 'requires_approval': True}, 'blocked'),
    ({'ok': False, 'blocked_by_gate': 'gate5'}, 'blocked'),
    ({'ok': False, 'status': 'waiting_review'}, 'blocked'),
    ({'ok': False, 'status': 'model_selection_required'}, 'needs_user'),
])
def test_real_boundary_markers_survive_semantic_answer_or_omitted_citation(result: dict[str, Any], status: str) -> None:
    session = CommanderSession('goal', 'project'); _user(session, '执行任务。'); goal = _goal(session)
    blocked = _observe(session, 'run.existing_model', result)
    verdict = assess_completion(session, goal, '完成。', 'answer_sufficient', evidence_ids=('unrelated',))
    assert verdict.status == status and verdict.evidence_ids == (blocked.id,)


@pytest.mark.parametrize(('result', 'expected'), [
    ({'ok': False, 'requires_approval': True}, 'blocked'),
    ({'ok': False, 'blocked_by_gate': 'baseline'}, 'blocked'),
    ({'ok': False, 'status': 'waiting_review'}, 'blocked'),
    ({'ok': False, 'status': 'entrypoint_selection_required'}, 'needs_user'),
    ({'ok': True, 'status': 'model_selection_required'}, 'needs_user'),
    ({'ok': False, 'requires_approval': True, 'status': 'needs_user'}, 'blocked'),
    ({'ok': True, 'status': 'started'}, None),
    ({'ok': False, 'status': {}, 'requires_approval': False}, None),
])
def test_actual_receipt_boundary_can_stop_a_batch_without_inventing_a_goal(
        result: dict[str, Any], expected: str | None) -> None:
    assert receipt_intervention(result) == expected


def test_failed_allocated_receipt_retains_identity_but_does_not_complete() -> None:
    session = CommanderSession('goal', 'project'); _user(session, '启动任务。'); goal = _goal(session)
    session.linked_run_id = 'retained'
    _observe(session, 'create_and_start_run', {'ok': False, 'run_id': 'retained', 'error': 'start failed'})
    verdict = assess_completion(session, goal)
    assert verdict.status == 'pending' and verdict.run_id == 'retained'
    assert '不得重复创建' in pending_continuation_prompt(verdict)


def test_semantics_can_finish_answers_or_ask_questions_but_never_certify_actions() -> None:
    session = CommanderSession('goal', 'project'); _user(session, '这是什么原理？')
    answer = _goal(session, (), target='none', intent='answer')
    assert assess_completion(session, answer, '这是已有配置的解释。', 'answer_sufficient').status == 'fulfilled'
    action = replace(answer, intent='action', required_effects=('run_started',), run_target='new')
    assert assess_completion(session, action, '已启动。', 'answer_sufficient').status == 'pending'
    assert assess_completion(session, action, '你要选择哪个模型？', 'needs_user').status == 'needs_user'
    assert assess_completion(session, action, '受限制。', 'blocked').status == 'pending'


def test_saved_previous_goal_is_explicit_and_current_ledger_does_not_fake_history() -> None:
    session = CommanderSession('goal', 'project'); _user(session, '启动任务。'); goal = _goal(session)
    old = _observe(session, 'code.repo_list', {'ok': True, 'output': {'entries': []}})
    _user(session, '继续。')
    assert assess_completion(session, goal).status == 'unknown'
    session.completion_state = {'goal': goal_state(goal), 'status': 'pending'}
    assert assess_completion(session, goal).status == 'pending'
    assert current_turn_ledger(session) == []
    current = _observe(session, 'code.repo_search', {'ok': True, 'output': {'matches': []}})
    assert [row['id'] for row in current_turn_ledger(session)] == [current.id]
    copy = current_turn_ledger(session); copy[0]['result']['output']['matches'].append('changed')
    assert current.tool_result is not None and current.tool_result['output']['matches'] == []
    assert old.id != current.id
