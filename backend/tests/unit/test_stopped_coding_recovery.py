"""Admission after a real Git branch preparation; no Agent result substitutes."""
import json
from pathlib import Path

import pytest
import yaml

from app.agents.coding.agent import CodingAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.bridge.research_branch import research_branch_scope
from app.bridge.run_recovery import recovery_status
from app.bridge.task_runtime import bind_task
from app.harness.agent_loop.trace import LoopTrace
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.harness.tools.git_branch import git
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


def test_confirmed_coding_stop_offers_retry_but_unknown_write_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    try:
        project = open_folder(str(tmp_path / 'project'), create=True)
        root = tmp_path / 'source'; root.mkdir()
        (root / 'train.py').write_text('value = 1\n')
        git(root, 'init', '-b', 'baseline'); git(root, 'add', 'train.py')
        git(root, '-c', 'user.name=MARS', '-c', 'user.email=mars@localhost', '-c', 'commit.gpgsign=false',
            'commit', '-m', 'Authored admission fixture')
        (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({
            'repo_path': str(root), 'read_only': True, 'allowed_paths': []}))
        registry = AgentRegistry(); registry.register('coding', CodingAgent())
        orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), registry=registry, bus=InProcessEventBus())
        session = orch.create_session(RunRequest(task='stopped-coding', project=project.name,
                                                entrypoint='coding', standalone=True))
        with research_branch_scope(session.run, 'coding'):
            assert git(root, 'branch', '--show-current').startswith('mars/')
        task = bind_task(session.run, 'coding', goal='Admission before operations', upstream={}, output_schema='code_spec.v1')
        trace = LoopTrace(session.run.root / 'agent_traces/coding' / task.invocation_id, 'full')
        trace.emit('authored_admission_fixture', {})
        trace.snapshot({'status': 'interrupted', 'pending': None, 'counts': {}, 'usage': {},
                        'usage_complete': False, 'fingerprint': 'admission-only'})
        session.graph.restore_state('coding', NodeState.FAILED)
        session.termination = {'type': 'cancelled', 'scope': 'owned_async_tasks',
                               'cleanup_complete': True, 'interrupted_nodes': ['coding']}
        view = recovery_status(orch, session.run.run_id, project=project.name)
        assert [item['action'] for item in view['actions']] == ['retry']
        path = trace.root / 'checkpoint.json'
        state = json.loads(path.read_text()); state['pending'] = 'tool'; path.write_text(json.dumps(state))
        assert recovery_status(orch, session.run.run_id, project=project.name)['status'] == 'blocked'
    finally:
        reset_settings_cache()
