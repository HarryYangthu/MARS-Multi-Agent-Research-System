"""Execution admission using real files and human-authored approved schemas."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from collections.abc import Iterator

from fastapi import HTTPException
import pytest
import yaml

from app.api import dependencies
from app.api.runs import ExecutionConfirmationPayload, confirm_execution_configuration, get_execution_configuration
from app.bridge.agent_registry import AgentRegistry
from app.bridge.agent_runner import _run_execution_batch
from app.bridge.execution_batch_plan import prepare_execution
from app.bridge.execution_confirmation import execution_preview, require_confirmation, save_confirmation
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps
from app.settings import reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


@pytest.fixture
def execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Orchestrator, RunSession, Path, Path]]:
    configuration = tmp_path / 'execution.yaml'
    configuration.write_text(yaml.safe_dump({'execution': {'backend': 'local_command', 'max_concurrency': 1,
        'batch_steps': 50, 'command_timeout_seconds': 5,
        'local_commands': [{'id': 'python-environment', 'argv': [sys.executable, '--version']}]}}))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(configuration))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    store = RunStore(tmp_path / 'runs')
    orch = Orchestrator(run_store=store, registry=AgentRegistry())
    session = orch.create_session(RunRequest(task='configuration-confirmation', project='regression',
        entrypoint='execution', standalone=True, auto_approve=False))
    data = tmp_path / 'input.json'
    data.write_text('[1,2,3]')
    metadata = {'schema': 'run_log.v1', 'project': session.run.project, 'agent': 'execution',
        'run_id': session.run.run_id, 'status': 'interrupted', 'metrics': {'planned_experiments': 2},
        'fingerprint_hash': 'sha256:00', 'planned_experiments': [
            {'name': name, 'config': {'seed': 2026, 'data_path': str(data), 'budget_steps': 50}}
            for name in ('baseline', 'candidate')]}
    artifacts = ArtifactStore(session.run)
    artifacts.approve(artifacts.write(text=dumps(metadata, 'Human-authored execution plan; no measured result is asserted.')))
    previous = dependencies._run_store, dependencies._orchestrator
    dependencies._run_store, dependencies._orchestrator = store, orch
    try:
        yield orch, session, configuration, data
    finally:
        dependencies._run_store, dependencies._orchestrator = previous
        reset_settings_cache()


def test_preview_and_execution_share_all_prepared_experiments(execution: tuple[Orchestrator, RunSession, Path, Path]) -> None:
    _, session, _, _ = execution
    view = execution_preview(session.run, 'execution')
    plan = prepare_execution(session.run, 'execution')
    assert not view['blockers'] and not view['confirmed']
    assert [(row['name'], row['seed'], row['config']) for row in view['experiments']] == [
        (spec.experiment_id, spec.seed, spec.config) for spec in plan.specs]
    assert view['defaults']['batch_steps'] == 50
    assert not (session.run.root / 'execution/confirmations').exists()
    assert not (session.run.root / 'execution/batch_summary.json').exists()


@pytest.mark.parametrize('change', ['data', 'configuration', 'plan'])
def test_confirmation_is_durable_idempotent_and_invalidated_by_changes(
    execution: tuple[Orchestrator, RunSession, Path, Path], change: str,
) -> None:
    _, session, configuration, data = execution
    view = execution_preview(session.run, 'execution')
    save_confirmation(session.run, 'execution', view['token'])
    receipt = session.run.root / 'execution/confirmations' / (view['token'] + '.json')
    before = receipt.read_bytes()
    save_confirmation(session.run, 'execution', view['token'])
    assert receipt.read_bytes() == before
    assert execution_preview(session.run, 'execution')['confirmed']
    require_confirmation(session.run, 'execution')
    if change == 'data':
        data.write_text('[4,5]')
    elif change == 'configuration':
        raw = yaml.safe_load(configuration.read_text())
        raw['execution']['batch_steps'] = 100
        configuration.write_text(yaml.safe_dump(raw))
    else:
        path = session.run.root / 'execution/run_log.approved.md'
        path.write_text(path.read_text() + '\nAdditional human note.\n')
    after = execution_preview(session.run, 'execution')
    assert after['token'] != view['token'] and not after['confirmed']
    with pytest.raises(ValueError, match='变化'):
        save_confirmation(session.run, 'execution', view['token'])
    with pytest.raises(ValueError, match='未启动'):
        require_confirmation(session.run, 'execution')
    assert receipt.read_bytes() == before


@pytest.mark.asyncio
async def test_batch_cannot_start_without_confirmation(execution: tuple[Orchestrator, RunSession, Path, Path]) -> None:
    _, session, _, _ = execution
    before = set(session.run.root.rglob('*'))
    with pytest.raises(ValueError, match='未启动'):
        await _run_execution_batch(run=session.run, node_key='execution')
    assert not (session.run.root / 'execution/tensorboard').exists()
    assert not (session.run.root / 'execution/local_commands').exists()
    assert set(session.run.root.rglob('*')) - before <= {
        session.run.root / 'resources', session.run.root / 'resources/.model_budget.v1.json.lock'}


def test_backend_mismatch_and_missing_approved_plan_block_launch(
    execution: tuple[Orchestrator, RunSession, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session, configuration, _ = execution
    raw = yaml.safe_load(configuration.read_text())
    raw['execution']['backend'] = 'paper_static'
    configuration.write_text(yaml.safe_dump(raw))
    view = execution_preview(session.run, 'execution')
    assert any('实际运行环境' in item for item in view['blockers'])
    with pytest.raises(ValueError):
        save_confirmation(session.run, 'execution', view['token'])
    (session.run.root / 'execution/run_log.approved.md').unlink()
    view = execution_preview(session.run, 'execution')
    assert any('尚未生成并审核通过' in item for item in view['blockers'])


def test_credential_values_never_reach_preview(execution: tuple[Orchestrator, RunSession, Path, Path]) -> None:
    _, session, _, _ = execution
    path = session.run.root / 'execution/run_log.approved.md'
    path.write_text(path.read_text().replace('budget_steps: 50', 'budget_steps: 50\n    api_key: test-private-credential'))
    view = execution_preview(session.run, 'execution')
    assert 'test-private-credential' not in json.dumps(view)
    assert '[已隐藏]' in json.dumps(view, ensure_ascii=False)


@pytest.mark.asyncio
async def test_failed_or_wrong_project_confirmation_never_launches(execution: tuple[Orchestrator, RunSession, Path, Path]) -> None:
    orch, session, _, _ = execution
    await orch._transition(session, 'execution', NodeState.RUNNING)
    await orch._transition(session, 'execution', NodeState.FAILED)
    view = get_execution_configuration(session.run.run_id, session.run.project)
    assert view['visible'] and not view['can_confirm']
    with pytest.raises(HTTPException) as failure:
        await confirm_execution_configuration(session.run.run_id, ExecutionConfirmationPayload(project=session.run.project, token=view['token']))
    assert failure.value.status_code == 409
    assert not (await orch.resume_after_artifact_approval(run_id=session.run.run_id, agent='execution'))['ok']
    with pytest.raises(HTTPException):
        get_execution_configuration(session.run.run_id, 'other-project')
    assert not (session.run.root / 'execution/confirmations').exists()
    assert orch.owned_tasks.active(session.run.run_id) is None


@pytest.mark.asyncio
async def test_owned_gate_waits_without_jobs_and_duplicate_confirmation_wakes_same_owner(
    execution: tuple[Orchestrator, RunSession, Path, Path],
) -> None:
    orch, session, _, _ = execution
    for state in (NodeState.RUNNING, NodeState.WAITING_REVIEW, NodeState.APPROVED):
        await orch._transition(session, 'execution', state)
    arrived = asyncio.Event()
    async def wait_only() -> None:
        assert await orch._await_execution_confirmation(session, 'execution')
        arrived.set()
    assert orch._spawn_owned(session, 'approval', wait_only)
    owner = orch.owned_tasks.active(session.run.run_id)
    assert owner is not None
    await asyncio.sleep(0)
    view = get_execution_configuration(session.run.run_id, session.run.project)
    assert view['launch_ready'] and view['can_confirm']
    payload = ExecutionConfirmationPayload(project=session.run.project, token=view['token'])
    results = await asyncio.gather(*(confirm_execution_configuration(session.run.run_id, payload) for _ in range(2)))
    assert all(result['confirmed'] and result['ok'] for result in results)
    assert orch.owned_tasks.active(session.run.run_id) is owner
    await asyncio.wait_for(arrived.wait(), timeout=3)
    await owner
    assert len(list((session.run.root / 'execution/confirmations').glob('*.json'))) == 1
    assert session.graph.state('execution') == NodeState.APPROVED
    assert not (session.run.root / 'execution/local_commands').exists()


@pytest.mark.asyncio
async def test_waiting_gate_stop_retains_approved_configuration_without_starting_jobs(
    execution: tuple[Orchestrator, RunSession, Path, Path],
) -> None:
    orch, session, _, _ = execution
    for state in (NodeState.RUNNING, NodeState.WAITING_REVIEW, NodeState.APPROVED):
        await orch._transition(session, 'execution', state)
    async def wait_only() -> None:
        await orch._await_execution_confirmation(session, 'execution')
    assert orch._spawn_owned(session, 'approval', wait_only)
    await asyncio.sleep(0.1)
    result = await orch.stop_owned_run(session.run.run_id)
    assert result['ok'] and result['termination']['cleanup_complete']
    recovered = Orchestrator(run_store=orch.run_store, registry=AgentRegistry()).session(session.run.run_id)
    assert recovered.graph.state('execution') == NodeState.APPROVED
    assert not execution_preview(recovered.run, 'execution')['confirmed']
    assert not (session.run.root / 'execution/batch_summary.json').exists()


@pytest.mark.asyncio
async def test_cold_approval_returns_before_waiting_for_configuration(
    execution: tuple[Orchestrator, RunSession, Path, Path],
) -> None:
    orch, session, _, _ = execution
    for state in (NodeState.RUNNING, NodeState.WAITING_REVIEW):
        await orch._transition(session, 'execution', state)
    result = await asyncio.wait_for(orch.resume_after_artifact_approval(run_id=session.run.run_id, agent='execution'), timeout=1)
    assert result['ok'] and result['status'] == 'approval_signalled'
    await asyncio.sleep(0.1)
    assert session.graph.state('execution') == NodeState.APPROVED
    assert orch.owned_tasks.active(session.run.run_id) is not None
    assert not (session.run.root / 'execution/local_commands').exists()
    assert (await orch.stop_owned_run(session.run.run_id))['ok']


@pytest.mark.asyncio
async def test_explicit_auto_mode_records_policy_confirmation(execution: tuple[Orchestrator, RunSession, Path, Path]) -> None:
    orch, session, _, _ = execution
    session.request.auto_approve = True
    assert await orch._await_execution_confirmation(session, 'execution')
    receipt = next((session.run.root / 'execution/confirmations').glob('*.json'))
    assert json.loads(receipt.read_text())['actor'] == 'auto_approve'
    assert not (session.run.root / 'execution/local_commands').exists()


def test_real_git_code_change_invalidates_confirmation_but_checkpoint_commit_does_not(
    execution: tuple[Orchestrator, RunSession, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bridge.research_branch import research_branch_scope
    from app.harness.project_workspace import open_folder
    from app.harness.tools.git_branch import git
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = tmp_path / 'code'
    source.mkdir()
    git(source, 'init', '-b', 'baseline')
    (source / 'main.py').write_text('VALUE = 1\n')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c', 'commit.gpgsign=false', 'commit', '-m', 'Baseline')
    (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({'repo_path': str(source),
        'read_only': True, 'allowed_paths': ['main.py'], 'protected_paths': [], 'ignore_patterns': ['.git/']}))
    run = RunStore(tmp_path / 'git-runs').create(task='real-git-confirmation', project=project.name)
    with research_branch_scope(run, 'coding'):
        (source / 'main.py').write_text('VALUE = 2\n')
    artifacts = ArtifactStore(run)
    artifacts.approve(artifacts.write(text=dumps({'schema': 'code_spec.v1', 'project': run.project, 'agent': 'coding',
        'target_lang': 'python', 'baseline_compat': {'preserved': True},
        'files_changed': [{'path': 'main.py', 'type': 'modified'}]}, 'Human-authored code review.')))
    artifacts.approve(artifacts.write(text=dumps({'schema': 'run_log.v1', 'project': run.project, 'agent': 'execution',
        'run_id': run.run_id, 'status': 'interrupted', 'metrics': {'planned_experiments': 1}, 'fingerprint_hash': 'sha256:00',
        'planned_experiments': [{'name': 'baseline', 'config': {'seed': 2026}}]}, 'Human-authored execution plan.')))
    view = execution_preview(run, 'execution')
    assert view['defaults']['repository'] == str(source.resolve())
    save_confirmation(run, 'execution', view['token'])
    with research_branch_scope(run, 'execution'):
        require_confirmation(run, 'execution')
    assert execution_preview(run, 'execution')['confirmed']
    assert git(source, 'show', 'baseline:main.py') == 'VALUE = 1'
    (source / 'main.py').write_text('VALUE = 3\n')
    assert not execution_preview(run, 'execution')['confirmed']
