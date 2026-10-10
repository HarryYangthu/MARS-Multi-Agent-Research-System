"""Real artifacts and host commands verify delivery-driven execution intake."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Any

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.execution.agent import ExecutionAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.execution_batch_plan import prepare_execution
from app.bridge.execution_confirmation import execution_preview, save_confirmation
from app.bridge.orchestrator import Orchestrator, RunRequest as SessionRequest
from app.execution.handoff_validation import execution_delivery_required, experiment_plan_required
from app.harness.schema.experiment_contract import (
    coding_job_errors, delivery_execution_errors, document_hash,
)
from app.harness.schema.frontmatter_parser import dumps
from app.settings import repo_root, reset_settings_cache
from app.storage.artifact_store import ArtifactStore, ArtifactValidationError
from app.storage.run_store import RunStore


def coding(project: str = 'regression') -> dict[str, Any]:
    return {'schema': 'code_spec.v1', 'agent': 'coding', 'project': project, 'target_lang': 'python',
        'baseline_compat': {'preserved': True}, 'files_changed': [], 'execution_jobs': [
            {'name': name, 'config': {'command_id': 'python-environment', 'seed': 2026, 'budget_steps': 2}}
            for name in ('baseline', 'candidate')]}


@pytest.mark.asyncio
async def test_direct_intake_uses_complete_coding_delivery_without_plan_or_model() -> None:
    text = dumps(coding(), 'Actual execution bindings; no experiment document exists.')
    request = RunRequest(project='regression', user_request='Run the delivered code',
        upstream_artifacts={'code_spec.approved.md': '[upstream artifact: coding/code_spec.approved.md]\n' + text},
        extra={'run_id': 'direct-intake'})
    agent = ExecutionAgent()
    result = await agent.run_loop(request, await agent.build_context(request))
    assert result.metadata['planned_experiments'] == coding()['execution_jobs']
    assert result.metadata['execution_source'] == 'coding_delivery'
    assert result.metadata['coding_spec_sha256'] == document_hash(text)
    assert result.metadata['execution_phase'] == 'planned' and not agent.requires_model
    assert not delivery_execution_errors('', text, result.metadata, project='regression')


@pytest.mark.parametrize('change', ['seed', 'unit', 'count', 'missing_jobs', 'empty_jobs', 'entrypoint', 'duplicate', 'path', 'project'])
def test_incomplete_delivery_has_actionable_errors(change: str) -> None:
    metadata = coding()
    config = metadata['execution_jobs'][0]['config']
    if change == 'seed':
        config.pop('seed')
    elif change == 'unit':
        config['budget_unit'] = 'epochs'
    elif change == 'count':
        config['budget_steps'] = 0
    elif change == 'missing_jobs':
        metadata.pop('execution_jobs')
    elif change == 'empty_jobs':
        metadata['execution_jobs'] = []
    elif change == 'entrypoint':
        config.pop('command_id')
    elif change == 'duplicate':
        metadata['execution_jobs'][1]['name'] = 'baseline'
    elif change == 'path':
        config['config_path'] = 'config.yaml (说明)'
    else:
        metadata['project'] = 'other'
    assert coding_job_errors(metadata, project='regression')


@pytest.mark.asyncio
async def test_required_design_cannot_be_deleted_to_enter_direct_intake() -> None:
    request = RunRequest(project='regression', user_request='Honor the research design',
        upstream_artifacts={'code': dumps(coding(), 'Delivered code.')},
        extra={'run_id': 'missing-required-design', 'experiment_plan_required': True})
    agent = ExecutionAgent()
    with pytest.raises(ValueError, match='实验设计交付缺失'):
        await agent.run_loop(request, await agent.build_context(request))


def test_dependency_graph_decides_requirements_without_fixed_task_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.project_workspace import open_folder
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True).name
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry())
    full = orch.create_session(SessionRequest(project=project, task='full', entrypoint='pipeline'))
    direct = orch.create_session(SessionRequest(project=project, task='direct', entrypoint='coding'))
    code_only = orch.create_session(SessionRequest(project=project, task='code-only', entrypoint='coding', standalone=True))
    assert experiment_plan_required(full.run, 'coding') and experiment_plan_required(full.run, 'execution')
    assert not experiment_plan_required(direct.run, 'coding') and not experiment_plan_required(direct.run, 'execution')
    assert execution_delivery_required(direct.run, 'coding')
    assert not execution_delivery_required(code_only.run, 'coding')
    with pytest.raises(ArtifactValidationError, match='实验设计交付缺失'):
        store = ArtifactStore(full.run)
        store.approve(store.write(text=dumps(coding(project), 'Missing required upstream design.')))
    metadata = coding(project)
    metadata.pop('execution_jobs')
    store = ArtifactStore(code_only.run)
    store.approve(store.write(text=dumps(metadata, 'A pure coding task needs no simulation.')))
    reset_settings_cache()


@pytest.mark.asyncio
async def test_real_approval_preview_and_confirmation_share_delivered_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = tmp_path / 'execution.yaml'
    configuration.write_text(yaml.safe_dump({'execution': {'backend': 'local_command', 'max_concurrency': 1,
        'batch_steps': 2, 'command_timeout_seconds': 5,
        'local_commands': [{'id': 'python-environment', 'argv': [sys.executable, '--version']}]}}))
    tools = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
    tools['tools']['execution.simulation_runner']['command_allowlist'] = [[sys.executable, '--version']]
    tool_path = tmp_path / 'tools.yaml'
    tool_path.write_text(yaml.safe_dump(tools))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(configuration))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    monkeypatch.setenv('MARS_TOOLS_CONFIG_PATH', str(tool_path))
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    try:
        from app.harness.project_workspace import open_folder
        project = open_folder(str(tmp_path / 'project'), create=True).name
        orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), registry=AgentRegistry())
        session = orch.create_session(SessionRequest(project=project, task='direct-delivery', entrypoint='coding'))
        store = ArtifactStore(session.run)
        metadata = coding(project)
        # All admission paths use real registered host configuration, without launching jobs.
        missing = deepcopy(metadata)
        missing.pop('execution_jobs')
        with pytest.raises(ArtifactValidationError, match='运行清单'):
            store.approve(store.write(text=dumps(missing, 'Incomplete coding delivery.')))
        bad = deepcopy(metadata)
        bad['execution_jobs'][0]['config']['command_id'] = 'not-registered'
        with pytest.raises(ArtifactValidationError, match='local_command'):
            store.approve(store.write(text=dumps(bad, 'Unknown host command.')))
        approved = store.approve(store.write(text=dumps(metadata, 'Verified host binding.')))
        before = execution_preview(session.run, 'execution')
        assert len(before['experiments']) == 2 and before['blockers']
        with pytest.raises(ValueError):
            save_confirmation(session.run, 'execution', before['token'])
        request = RunRequest(project=session.run.project, user_request='Run this delivery',
            upstream_artifacts={'code': approved.path.read_text()}, extra={'run_id': session.run.run_id})
        agent = ExecutionAgent()
        artifact = await agent.run_loop(request, await agent.build_context(request))
        changed = deepcopy(artifact.metadata)
        changed['planned_experiments'][0]['config']['seed'] = 42
        with pytest.raises(ArtifactValidationError, match='不一致'):
            store.approve(store.write(text=dumps(changed, 'Changed seed cannot be approved.')))
        store.approve(store.write(text=artifact.text))
        view = execution_preview(session.run, 'execution')
        assert not view['blockers'] and not view['confirmed']
        prepared = prepare_execution(session.run, 'execution')
        assert [job.experiment_id for job in prepared.specs] == ['baseline', 'candidate']
        assert prepared.plan_source == 'execution_run_log'
        receipt = save_confirmation(session.run, 'execution', view['token'])
        assert receipt['created'] and not save_confirmation(session.run, 'execution', view['token'])['created']
        store.approve(store.write(text=dumps(metadata, 'Updated coding delivery requires fresh execution intake.')))
        after = execution_preview(session.run, 'execution')
        assert after['token'] != view['token'] and not after['confirmed'] and after['blockers']
        assert any('coding_spec_sha256' in error for error in after['blockers'])
        assert not (session.run.root / 'execution/jobs').exists()
        assert not (session.run.root / 'experiment/experiment_plan.approved.md').exists()
    finally:
        reset_settings_cache()
