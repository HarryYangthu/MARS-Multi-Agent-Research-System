"""Real host files, project directories and state journals; no simulated training."""
from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import project_management
from app.api.dependencies import reset_for_tests
from app.bridge.project_lifecycle import removal_preview, remove_project, restore_project
from app.bridge.project_training import save_training, training_status, require_training_for_run
from app.harness.project_workspace import open_folder, list_folder_projects
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.harness.tools.config import tool_config
from app.harness.tools.execution.handoff import execution_delivery_blocker, execution_delivery_context
from app.harness.tools.execution.registrations import registered_commands, registration_path
from app.settings import reset_settings_cache
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    config = tmp_path / 'host.yaml'
    config.write_text(yaml.safe_dump({'execution': {'local_commands': []}}), encoding='utf-8')
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(config))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'host-state/folders.json'))
    reset_settings_cache()
    reset_for_tests()
    yield config
    reset_for_tests()
    reset_settings_cache()


def _save(project: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = dict(python=sys.executable, entrypoint='mars_job.py', config_pattern='configs/*.yaml',
                   required_metrics=['RES'], consumes_data_path=True)
    payload.update(overrides)
    return save_training(project, **payload)


def test_new_projects_register_independently_without_editing_host_yaml(tmp_path: Path, host: Path) -> None:
    first = open_folder(str(tmp_path / 'first'), create=True)
    second = open_folder(str(tmp_path / 'second'), create=True)
    before = host.read_bytes()
    assert not training_status(first.name)['configured']
    status = _save(first.name)
    assert status['configured'] and not status['entrypoint_exists'] and not status['execution_verified']
    assert execution_delivery_blocker(first.name) == ''
    assert '项目配置' in execution_delivery_blocker(second.name)
    _save(second.name, entrypoint='tools/mars_job.py')
    for project in (first.name, second.name):
        rows = execution_delivery_context(project=project)['local_commands']
        assert len(rows) == 1 and rows[0]['project_ids'] == [project]
        assert tuple(rows[0]['argv']) in tool_config('execution.simulation_runner').command_allowlist
    assert host.read_bytes() == before
    assert registration_path().parent == host.parent
    assert not (first.metadata_root / 'training.json').exists()


@pytest.mark.parametrize('path', ['../outside.py', 'D:/outside.py', '/outside.py', '{code_root}/a.py', 'train.cmd'])
def test_reject_bad_entrypoints_without_persisting(tmp_path: Path, host: Path, path: str) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    with pytest.raises(ValueError):
        _save(project.name, entrypoint=path)
    assert not registered_commands()


def test_project_local_file_cannot_register_execution(tmp_path: Path, host: Path) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    (project.metadata_root / 'training.json').write_text('{"argv": ["anything"]}')
    assert not training_status(project.name)['configured']


def test_training_admission_does_not_block_standalone_research_or_coding(tmp_path: Path, host: Path) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    for stage in ('idea', 'experiment', 'coding', 'writing'):
        require_training_for_run(project.name, entrypoint=stage, standalone=True)
    for stage in ('pipeline', 'idea', 'experiment', 'coding', 'execution'):
        with pytest.raises(ValueError, match='训练启动'):
            require_training_for_run(project.name, entrypoint=stage, standalone=False)
    _save(project.name)
    require_training_for_run(project.name, entrypoint='pipeline', standalone=False)


@pytest.mark.parametrize('mode', ['detach', 'recycle'])
def test_remove_restore_preserves_bytes_identity_and_external_data(tmp_path: Path, host: Path, mode: str) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    (project.root / 'original.bin').write_bytes(b'original\x00data')
    external = tmp_path / 'outside.bin'
    external.write_bytes(b'keep')
    store = RunStore(tmp_path / 'runstore')
    _save(project.name)
    before = {str(p.relative_to(project.root)): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
    record = remove_project(project.name, mode=mode, expected_path=str(project.root),
                            confirmation=project.display_name, store=store)
    assert not list_folder_projects()
    assert Path(record['stored_path']).is_dir()
    assert project.root.exists() == (mode == 'detach')
    restore_project(record['id'])
    assert list_folder_projects()[0].name == project.name
    assert external.read_bytes() == b'keep'
    assert before == {str(p.relative_to(project.root)): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
    assert training_status(project.name)['configured']


def test_recycle_refuses_overlapping_projects_and_wrong_confirmation(tmp_path: Path, host: Path) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    open_folder(str(project.root / 'nested'), create=True)
    store = RunStore(tmp_path / 'runstore')
    preview = removal_preview(project.name, store)
    assert not preview['blockers'] and preview['recycle_blockers']
    with pytest.raises(ValueError, match='重叠'):
        remove_project(project.name, mode='recycle', expected_path=str(project.root), confirmation=project.display_name, store=store)
    with pytest.raises(ValueError, match='确认'):
        remove_project(project.name, mode='detach', expected_path=str(project.root), confirmation='wrong', store=store)
    assert project.root.exists() and len(list_folder_projects()) == 2


def test_restore_never_overwrites_replacement_folder(tmp_path: Path, host: Path) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    record = remove_project(project.name, mode='recycle', expected_path=str(project.root),
                            confirmation=project.display_name, store=RunStore(tmp_path / 'runs'))
    project.root.mkdir()
    with pytest.raises(ValueError, match='不能覆盖'):
        restore_project(record['id'])
    assert Path(record['stored_path']).exists()


def test_recycle_refuses_code_shared_by_another_project(tmp_path: Path, host: Path) -> None:
    source = open_folder(str(tmp_path / 'source'), create=True)
    other = open_folder(str(tmp_path / 'other'), create=True)
    (other.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({
        'repo_mode': 'local_path', 'repo_path': str(source.root), 'read_only': False,
    }), encoding='utf-8')
    preview = removal_preview(source.name, RunStore(tmp_path / 'runstore'))
    assert not preview['blockers'] and any('引用' in item for item in preview['recycle_blockers'])


def test_missing_folder_can_be_detached_but_not_recycled(tmp_path: Path, host: Path) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    moved = tmp_path / 'moved-externally'
    project.root.rename(moved)
    store = RunStore(tmp_path / 'runstore')
    preview = removal_preview(project.name, store)
    assert preview['recycle_blockers'] and not preview['blockers']
    remove_project(project.name, mode='detach', expected_path=str(project.root),
                   confirmation=project.display_name, store=store)
    assert moved.is_dir() and not list_folder_projects()


def test_running_state_blocks_removal_with_real_journal(tmp_path: Path, host: Path) -> None:
    project = open_folder(str(tmp_path / 'project'), create=True)
    store = RunStore(tmp_path / 'runstore')
    run = store.create(task='active', project=project.name)
    graph = RunGraph()
    graph.add_node('coding')
    graph.restore_state('coding', NodeState.RUNNING)
    RunStateStore(run).write(graph=graph, request={'project': project.name}, status='running')
    before = (run.root / 'run_state.json').read_bytes()
    with pytest.raises(ValueError, match='运行中'):
        remove_project(project.name, mode='detach', expected_path=str(project.root), confirmation=project.display_name, store=store)
    assert (run.root / 'run_state.json').read_bytes() == before
    assert list_folder_projects()[0].name == project.name


def test_real_api_setup_removal_restore(tmp_path: Path, host: Path) -> None:
    project = open_folder(str(tmp_path / 'api-project'), create=True)
    app = FastAPI()
    app.include_router(project_management.router)
    with TestClient(app) as client:
        base = '/api/projects/' + project.name
        draft = client.get(base + '/training').json()['draft']
        draft['python'] = sys.executable
        saved = client.put(base + '/training', json=draft)
        assert saved.status_code == 200 and saved.json()['configured']
        preview = client.get(base + '/removal').json()
        removed = client.request('DELETE', base, json={'mode': 'detach', 'expected_path': preview['path'], 'confirmation': preview['display_name']})
        assert removed.status_code == 200
        rows = client.get('/api/projects/removed/list').json()
        assert rows[0]['id'] == removed.json()['id']
        assert client.post('/api/projects/removed/' + rows[0]['id'] + '/restore').status_code == 200
        assert client.get(base + '/training').json()['configured']
