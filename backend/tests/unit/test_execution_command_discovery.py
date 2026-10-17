"""Project discovery and admission against real host YAML; no execution doubles."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.coding.agent import CodingAgent
from app.harness.tools.execution.handoff import execution_delivery_context, execution_delivery_blocker, coding_handoff_errors
from app.harness.tools.execution.local_command import LocalCommandJob, _registered_command
from app.settings import reset_settings_cache


@pytest.mark.parametrize('scope', [['old-project'], [], 'new-project', [True]])
def test_unusable_commands_are_not_advertised_and_stop_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: Any,
) -> None:
    config = tmp_path / 'host-execution.yaml'
    config.write_text(yaml.safe_dump({'execution': {'local_commands': [
        {'id': 'old-training', 'argv': ['python', '{code_root}/train.py'], 'project_ids': scope},
    ]}}), encoding='utf-8')
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(config))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    try:
        assert execution_delivery_context(project='new-project')['local_commands'] == []
        assert '当前项目没有匹配' in execution_delivery_blocker('new-project')
        with pytest.raises(ValueError, match='registered command'):
            _registered_command(LocalCommandJob('run', 'job', 'new-project', tmp_path, command_id='old-training'))
        with pytest.raises(ValueError, match='当前项目没有匹配'):
            asyncio.run(CodingAgent().build_context(RunRequest(project='new-project',
                user_request='Implement the requested model', extra={'execution_delivery_required': True})))
        errors = coding_handoff_errors('', {'schema': 'code_spec.v1', 'project': 'new-project', 'execution_jobs': [{'name': 'training', 'config': {
            'entrypoint': 'train.py', 'config_path': 'configs/train.yaml', 'seed': 42, 'budget_steps': 10,
        }}]}, project='new-project')
        assert errors and all('old-training' not in error for error in errors)
        assert any('无，请先配置真实执行命令' in error for error in errors)
    finally:
        reset_settings_cache()


def test_explicit_and_unrestricted_registrations_use_same_scope_as_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{'id': 'old', 'project_ids': ['old-project']},
            {'id': 'new', 'project_ids': ['new-project']}, {'id': 'shared'}]
    config = tmp_path / 'host-execution.yaml'
    config.write_text(yaml.safe_dump({'execution': {'local_commands': rows}}), encoding='utf-8')
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(config))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    try:
        selected = execution_delivery_context(project='new-project')['local_commands']
        assert [row['id'] for row in selected] == ['new', 'shared']
        assert execution_delivery_blocker('new-project') == ''
        for row in selected:
            assert _registered_command(LocalCommandJob('run', 'job', 'new-project', tmp_path,
                                                       command_id=row['id'])) == row
        assert execution_delivery_context(project='old-project')['local_commands'] == [rows[0], rows[2]]
    finally:
        reset_settings_cache()


def test_pipeline_recovery_stops_before_repeating_an_unconfigured_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest as WorkflowRequest
    from app.bridge.run_recovery import recovery_status
    from app.harness.runtime.state_machine import NodeState
    from app.storage.run_store import RunStore
    config = tmp_path / 'host-execution.yaml'
    config.write_text(yaml.safe_dump({'execution': {'local_commands': [
        {'id': 'other-project-only', 'project_ids': ['another-project']},
    ]}}), encoding='utf-8')
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(config))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    try:
        registry = AgentRegistry()
        registry.register('coding', CodingAgent())
        orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), registry=registry)
        session = orch.create_session(WorkflowRequest(task='registration-admission', project='pimc', entrypoint='coding'))
        session.graph.restore_state('coding', NodeState.FAILED)
        orch._persist_state(session, status='failed')
        state_path = session.run.root / 'run_state.json'
        before = state_path.read_bytes()
        result = recovery_status(orch, session.run.run_id, project='pimc')
        assert result['status'] == 'blocked' and not result['actions']
        assert '当前项目没有匹配' in result['message']
        assert state_path.read_bytes() == before and orch.owned_tasks.active(session.run.run_id) is None
    finally:
        reset_settings_cache()
