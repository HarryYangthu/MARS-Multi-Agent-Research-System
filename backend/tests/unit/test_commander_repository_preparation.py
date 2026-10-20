"""Real file/tool coverage of creation prerequisites; no provider substitutes."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander_code import creation_source_preparation, repository_admission_state
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


@contextmanager
def _project(tmp_path: Path) -> Iterator[tuple[ToolContext, Path]]:
    key = 'MARS_FOLDER_PROJECTS_REGISTRY'
    prior = os.environ.get(key)
    os.environ[key] = str(tmp_path / 'registry.json')
    reset_settings_cache()
    try:
        project = open_folder(str(tmp_path / 'project'), create=True)
        source = project.root / 'code/source'
        (source / 'configs').mkdir(parents=True)
        (source / 'configs/model.yaml').write_text('model_name: Existing\nlearning_rate: 0.0004\n')
        (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({
            'repo_path': str(source), 'allowed_paths': ['configs/'], 'read_only': True}))
        store = RunStore(tmp_path / 'runs', project_storage=True)
        session = CommanderSession(conv_id='admission-planning', project=project.name)
        user = session.add(ChatMessage(role='user', content='基于已有模型，做一轮学习率寻优'))
        user.turn_id = session.active_turn_id = user.id
        yield ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()), session, store), source
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior
        reset_settings_cache()


async def _observe(ctx: ToolContext, action: dict[str, Any]) -> dict[str, Any]:
    result = await execute_tool(action['tool'], action['args'], ctx)
    ctx.session.add(ChatMessage(role='tool', content=json.dumps(result), tool_name=action['tool'],
                                tool_args=action['args'], tool_result=result))
    return result


@pytest.mark.asyncio
async def test_premature_create_prepares_real_repository_then_requires_real_read(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, source):
        action = {'tool': 'create_and_start_run', 'args': {
            'entrypoint': 'experiment', 'user_request': '基于已有模型做学习率寻优'}}
        original = deepcopy(action)
        state = repository_admission_state(ctx.session)
        assert state['status'] == 'source_read_required' and not state['creation_ready']
        prepare = creation_source_preparation(ctx.session, action)
        assert prepare == {'tool': 'code.repo_list', 'args': {'path': '.'}}
        assert action == original and not ctx.run_store.list() and ctx.session.linked_run_id is None
        listed = await _observe(ctx, prepare)
        assert listed['ok'] and listed['output']['repo_root'] == str(source)
        assert listed['output']['entries'] == [{'path': 'configs', 'type': 'directory'}]
        assert creation_source_preparation(ctx.session, action) is None  # No repeated listing.
        assert repository_admission_state(ctx.session)['creation_ready'] is False
        rejected = await execute_tool(action['tool'], action['args'], ctx)
        assert not rejected['ok'] and rejected['status'] == 'repository_inspection_required'
        assert not ctx.run_store.list()  # Listing never certifies source inspection.


@pytest.mark.asyncio
async def test_discovered_explicit_source_is_really_read_before_route_is_reconsidered(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, source):
        listed = await _observe(ctx, {'tool': 'code.repo_list', 'args': {'path': 'configs'}})
        assert listed['ok']
        action = {'tool': 'run.create', 'args': {'entrypoint': 'experiment',
                  'user_request': '根据 configs/model.yaml 设计学习率对照，预算待审核'}}
        prepare = creation_source_preparation(ctx.session, action)
        assert prepare == {'tool': 'code.repo_read_lines', 'args': {'path': 'configs/model.yaml'}}
        read = await _observe(ctx, prepare)
        assert read['ok'] and read['output']['content'] == (source / 'configs/model.yaml').read_text()
        assert repository_admission_state(ctx.session)['status'] == 'ready'
        assert creation_source_preparation(ctx.session, action) is None
        assert not ctx.run_store.list()
        # Only the later real creation allocates a run, preserving the route.
        created = await execute_tool(action['tool'], action['args'], ctx)
        assert created['ok'] and created['entrypoint'] == 'experiment'
        assert len(ctx.run_store.list()) == 1 and created['states']['experiment'] == 'pending'
        run = ctx.run_store.get(created['run_id'])
        assert run is not None and not list(run.subdir('execution').iterdir())
        (source / 'configs/model.yaml').write_text('model_name: Changed\n')
        assert repository_admission_state(ctx.session)['status'] == 'source_read_required'


def test_noncode_writing_and_existing_run_are_not_replanned(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _):
        for args in ({'repository_scope': 'not_applicable', 'routing_reason': '纯文字报告'},
                     {'entrypoint': 'writing'}, {'repository_scope': 'invalid'}):
            assert creation_source_preparation(ctx.session, {'tool': 'run.create', 'args': args}) is None
        ctx.session.linked_run_id = 'retained-real-identity'
        assert creation_source_preparation(ctx.session, {'tool': 'run.create', 'args': {}}) is None
        assert creation_source_preparation(ctx.session, {'tool': 'run.recover', 'args': {}}) is None


def test_missing_repository_remains_an_actual_blocker(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, source):
        (source / 'configs/model.yaml').unlink()
        (source / 'configs').rmdir()
        source.rmdir()
        assert repository_admission_state(ctx.session)['status'] == 'unavailable'
        assert creation_source_preparation(ctx.session, {'tool': 'run.create', 'args': {}}) is None
