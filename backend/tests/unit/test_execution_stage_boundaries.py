"""Real local creation and confirmation waiters; no model or training substitutes."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, _create_run_with_routing, _stage_limit_user_sources, execute_tool
from app.bridge.commander import _begin_turn_completion, _store_turn_completion
from app.bridge.commander_completion import CompletionVerdict, parse_turn_goal
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.settings import reset_settings_cache
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


@contextmanager
def actual_project(tmp_path: Path) -> Iterator[ToolContext]:
    values = {'MARS_FOLDER_PROJECTS_REGISTRY': str(tmp_path/'registry.json'),
              'MARS_EXECUTION_CONFIG_PATH': str(tmp_path/'execution.yaml'),
              'MARS_EXECUTION_BACKEND': 'local_command', 'MARS_RUNTIME_MODE': 'development'}
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values); reset_settings_cache()
    try:
        project = open_folder(str(tmp_path/'project'), create=True)
        source = project.root/'code/source'; source.mkdir(parents=True)
        (source/'model.yaml').write_text('model_name: ActualModel\nseed: 2026\n')
        (project.metadata_root/'repo_link.yaml').write_text(yaml.safe_dump({
            'repo_path': str(source), 'allowed_paths': ['model.yaml'], 'read_only': True}))
        (tmp_path/'execution.yaml').write_text(yaml.safe_dump({'execution': {
            'backend': 'local_command', 'local_commands': []}}))
        store = RunStore(tmp_path/'runs', project_storage=True)
        yield ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()),
            CommanderSession('stage-boundary-real', project.name), store)
    finally:
        for key, value in saved.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        reset_settings_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize(('original', 'rewritten', 'expected'), [
    ('改参数并仿真。完成后暂停，先不生成报告。', '运行参数对照并生成报告。', 'execution'),
    ('改参数并仿真，然后生成报告。', '模型改写说不生成报告。', None),
])
async def test_actual_creation_freezes_only_original_user_stage_limit(
        tmp_path: Path, original: str, rewritten: str, expected: str | None) -> None:
    with actual_project(tmp_path) as ctx:
        user = ChatMessage(role='user', content=original)
        ctx.session.active_turn_id = user.id; ctx.session.add(user)
        read = await execute_tool('code.repo_read_lines', {'path': 'model.yaml'}, ctx)
        assert read['ok']
        ctx.session.add(ChatMessage(role='tool', content=json.dumps(read), tool_name='code.repo_read_lines',
            tool_args={'path': 'model.yaml'}, tool_result=read))
        result = await execute_tool('run.create', {'entrypoint': 'coding', 'user_request': rewritten}, ctx)
        assert result['ok'], result
        session = ctx.orchestrator.session(result['run_id'])
        assert session.request.extra.get('stop_after') == expected
        loaded = RunStateStore(session.run).load(); assert loaded is not None
        assert loaded.request['extra'].get('stop_after') == expected
        assert not list(session.run.subdir('execution').iterdir())


@pytest.mark.asyncio
async def test_trusted_frozen_boundary_survives_absent_ordinary_source(tmp_path: Path) -> None:
    with actual_project(tmp_path) as ctx:
        result = await _create_run_with_routing({'entrypoint': 'coding', 'user_request': 'Host-bound original.'},
            ctx, {'ok': True, 'verification': 'trusted test admission'}, additional_extra={'stop_after': 'execution'})
        assert result['ok']
        assert ctx.orchestrator.session(result['run_id']).request.extra['stop_after'] == 'execution'


@pytest.mark.asyncio
@pytest.mark.parametrize(('repeats', 'latest', 'previous_status', 'expected'), [
    (0, '继续', 'pending', 'execution'),
    (1, '继续', 'pending', 'execution'),
    (0, '新建另一个任务，运行后生成报告。', 'pending', None),
    (0, '继续', 'fulfilled', None),
])
async def test_actual_pending_retry_keeps_original_boundary_only_for_unallocated_chain(
        tmp_path: Path, repeats: int, latest: str, previous_status: str, expected: str | None) -> None:
    with actual_project(tmp_path) as ctx:
        original = ChatMessage(role='user', content='只改配置并训练两轮；仿真完成后暂停，先不生成报告。')
        ctx.session.active_turn_id = original.id; ctx.session.add(original)
        goal = parse_turn_goal({'intent': 'action', 'run_target': 'new',
            'required_effects': ['run_created', 'run_started']}, ctx.session)
        assert goal is not None
        # Actual failed source admission creates no run and retains the request.
        failed = await execute_tool('run.create', {'entrypoint': 'coding', 'user_request': original.content}, ctx)
        assert failed.get('ok') is False and not failed.get('run_id')
        ctx.session.add(ChatMessage(role='tool', content=json.dumps(failed), tool_name='run.create',
            tool_args={'entrypoint': 'coding'}, tool_result=failed))
        _store_turn_completion(ctx.session, goal, CompletionVerdict(status=previous_status, reason='actual admission pending'))
        for _ in range(repeats):
            user = ChatMessage(role='user', content='继续')
            ctx.session.active_turn_id = user.id; ctx.session.add(user)
            ctx.session.completion_state = _begin_turn_completion(ctx.session)
            continued_goal = parse_turn_goal({'intent': 'action', 'run_target': 'new',
                'required_effects': ['run_created', 'run_started']}, ctx.session)
            assert continued_goal is not None
            _store_turn_completion(ctx.session, continued_goal, CompletionVerdict(status='pending', reason='still unallocated'))
        user = ChatMessage(role='user', content=latest)
        ctx.session.active_turn_id = user.id; ctx.session.add(user)
        ctx.session.completion_state = _begin_turn_completion(ctx.session)
        sources = _stage_limit_user_sources(ctx.session)
        assert (sources[0]['id'] == original.id) is (expected == 'execution')
        read = await execute_tool('code.repo_read_lines', {'path': 'model.yaml'}, ctx)
        assert read['ok']; ctx.session.add(ChatMessage(role='tool', content=json.dumps(read),
            tool_name='code.repo_read_lines', tool_args={'path': 'model.yaml'}, tool_result=read))
        created = await execute_tool('run.create', {'entrypoint': 'coding',
            'user_request': 'Prepare the original parameter comparison.'}, ctx)
        assert created['ok'], created
        workflow = ctx.orchestrator.session(created['run_id'])
        assert workflow.request.extra.get('stop_after') == expected
        snapshot = RunStateStore(workflow.run).load(); assert snapshot is not None
        assert snapshot.request['extra'].get('stop_after') == expected
        assert not list(workflow.run.subdir('execution').iterdir())
        assert _stage_limit_user_sources(ctx.session) == [{'id': user.id, 'content': user.content}]


@pytest.mark.parametrize(('field', 'value'), [
    ('source_user_sha256', '0' * 64), ('project', 'another-project'),
    ('source_turn_id', 'missing-user-turn'), ('run_target', 'existing'),
])
def test_pending_stage_boundary_refuses_unbound_prior_goal(
        tmp_path: Path, field: str, value: str) -> None:
    with actual_project(tmp_path) as ctx:
        original = ChatMessage(role='user', content='运行后不生成报告。')
        ctx.session.active_turn_id = original.id; ctx.session.add(original)
        goal = parse_turn_goal({'intent': 'action', 'run_target': 'new',
            'required_effects': ['run_created', 'run_started']}, ctx.session)
        assert goal is not None
        _store_turn_completion(ctx.session, goal, CompletionVerdict(status='pending', reason='unallocated'))
        user = ChatMessage(role='user', content='继续')
        ctx.session.active_turn_id = user.id; ctx.session.add(user)
        ctx.session.completion_state = _begin_turn_completion(ctx.session)
        ctx.session.completion_state['previous_goal'][field] = value
        assert _stage_limit_user_sources(ctx.session) == [{'id': user.id, 'content': user.content}]
        assert not ctx.run_store.list()


async def wait_for_confirmation(orch: Orchestrator, session: RunSession) -> asyncio.Task[None]:
    session.graph.restore_state('execution', NodeState.APPROVED)
    orch._persist_state(session, status='created')
    async def waiter() -> None:
        # This is the real confirmation loop with no coding delivery. Its real
        # blockers retain the coordinator before any job can be admitted.
        await orch._await_execution_confirmation(session, 'execution')
    assert orch._spawn_owned(session, 'confirmation', waiter)
    task = orch.owned_tasks.active(session.run.run_id); assert task is not None
    for _ in range(100):
        snapshot = RunStateStore(session.run).load()
        if snapshot is not None and snapshot.status == 'waiting_execution_confirmation':
            return task
        await asyncio.sleep(.01)
    raise AssertionError('Actual confirmation waiter did not persist its boundary')


@pytest.mark.asyncio
@pytest.mark.parametrize('blocker', [None, 'writing_started', 'read_only'])
async def test_actual_confirmation_owner_can_set_boundary_before_downstream_dispatch(
        tmp_path: Path, blocker: str | None) -> None:
    with actual_project(tmp_path) as ctx:
        session = ctx.orchestrator.create_session(RunRequest(task='actual-confirmation',
            project=ctx.session.project, entrypoint='execution', review_mode='manual'))
        task = await wait_for_confirmation(ctx.orchestrator, session)
        try:
            if blocker == 'writing_started':
                session.graph.restore_state('writing', NodeState.RUNNING)
                ctx.orchestrator._persist_state(session, status='waiting_execution_confirmation')
            elif blocker == 'read_only':
                session.read_only = True
            if blocker is not None:
                with pytest.raises(ValueError):
                    ctx.orchestrator.set_stage_limit(session.run.run_id, stop_after='execution')
                assert 'stop_after' not in session.request.extra
            else:
                ctx.orchestrator.set_stage_limit(session.run.run_id, stop_after='execution')
                assert ctx.orchestrator.owned_tasks.active(session.run.run_id) is task
                saved = RunStateStore(session.run).load(); assert saved is not None
                assert saved.request['extra']['stop_after'] == 'execution'
                assert saved.status == 'waiting_execution_confirmation'
            assert not (session.run.subdir('execution')/'local_commands').exists()
        finally:
            session.read_only = False
            ctx.orchestrator.owned_tasks.cancel_once(session.run.run_id)
            with suppress(asyncio.CancelledError):
                await task
