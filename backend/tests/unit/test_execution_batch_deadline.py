"""Deadline policy and real local-process cancellation; no execution substitutes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest
import yaml

from app.bridge.execution_batch_deadline import batch_deadline_seconds
from app.execution.job_journal import _inputs, _path, rearm_interrupted_jobs
from app.execution.simulation_runner import JobSpec
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import repo_root, reset_settings_cache


def view(limits: list[float], concurrency: int = 1) -> dict[str, Any]:
    return {'confirmed': True, 'blockers': [], 'defaults': {'timeout_seconds': 900, 'max_concurrency': concurrency},
            'experiments': [{'effective': {'timeout_seconds': limit}} for limit in limits]}


def test_six_sequential_jobs_fit_the_outer_deadline() -> None:
    assert batch_deadline_seconds(view([900] * 6), 600) == 6000
    assert batch_deadline_seconds(view([900] * 6, 3), 600) == 6000
    assert batch_deadline_seconds(view([10, 90, 900]), 60) == 1060
    approved = view([900] * 6)
    approved['experiments'] = [{}] * 6
    assert batch_deadline_seconds(approved, 600) == 6000


@pytest.mark.parametrize('invalid', [0, -1, float('inf'), float('nan'), True, '900', None])
def test_invalid_timeouts_fail_closed(invalid: Any) -> None:
    with pytest.raises(ValueError):
        batch_deadline_seconds(view([invalid]), 600)
    with pytest.raises(ValueError):
        batch_deadline_seconds(view([900]), invalid)


def test_unconfirmed_or_empty_batch_has_no_deadline() -> None:
    for approved in (view([]), {**view([900]), 'confirmed': False}, {**view([900]), 'blockers': ['changed input']}):
        with pytest.raises(ValueError):
            batch_deadline_seconds(approved, 600)


def test_deadline_fork_retains_gates_permissions_and_global_defaults() -> None:
    parent = get_registry()
    original = parent.spec('execution.batch_runner')
    assert original is not None
    private = parent.with_timeout(original.name, 6000)
    changed = private.spec(original.name)
    assert changed is not None and changed.policy.timeout_seconds == 6000
    assert parent.spec(original.name) == original
    assert private._gates == parent._gates
    assert changed.policy.allowed_agents == original.policy.allowed_agents
    assert changed.input_schema == original.input_schema and changed.output_schema == original.output_schema
    with pytest.raises(ValueError):
        parent.with_timeout(original.name, float('inf'))


@pytest.mark.asyncio
async def test_real_process_timeout_has_a_cause_and_acknowledged_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_path = tmp_path / 'pid.txt'
    script = tmp_path / 'long_process.py'
    script.write_text(f'import os, time\nfrom pathlib import Path\nPath({str(pid_path)!r}).write_text(str(os.getpid()))\ntime.sleep(10)\n')
    argv = [sys.executable, str(script)]
    execution_path = tmp_path / 'execution.yaml'
    execution_path.write_text(yaml.safe_dump({'execution': {'command_timeout_seconds': 15,
        'local_commands': [{'id': 'real-delay', 'argv': argv}]}}))
    config = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
    config['tools']['execution.simulation_runner']['command_allowlist'] = [argv]
    config_path = tmp_path / 'tools.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(execution_path))
    monkeypatch.setenv('MARS_TOOLS_CONFIG_PATH', str(config_path))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    try:
        registry = get_registry().with_timeout('execution.simulation_runner', 0.5)
        result = await registry.dispatch('execution.simulation_runner',
            {'backend': 'local_command', 'command_id': 'real-delay'},
            ToolContext(run_id='real-timeout', project='regression', agent='bridge', extra={'run_root': str(tmp_path)}))
        assert result.status == 'timeout' and not result.ok
        assert '0.5s' in str(result.error)
        assert result.metadata['timeout_scope'] == 'tool_dispatch'
        pid = int(pid_path.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        receipt = json.loads(next((tmp_path / 'execution/local_commands').glob('*/*/execution_receipt.json')).read_text())
        assert receipt['status'] == 'cancelled' and receipt['returncode'] != 0
    finally:
        reset_settings_cache()


def test_explicit_retry_archives_interruption_and_refuses_changed_or_unknown_inputs(tmp_path: Path) -> None:
    spec = JobSpec(run_id='retry', experiment_id='control', project='regression', run_root=tmp_path,
                   config={'attempt': 1, 'confirmation_token': 'fixed'})
    path = _path(spec)
    path.parent.mkdir(parents=True)
    record = {'status': 'interrupted', 'cleanup_complete': True, 'inputs': _inputs(spec, 20)}
    path.write_text(json.dumps(record))
    path.with_suffix('.claim').touch()
    rearm_interrupted_jobs([spec], steps=20)
    assert not path.exists() and not path.with_suffix('.claim').exists()
    archived = next((path.parent / 'retry_history').glob('*/*/*.json'))
    assert json.loads(archived.read_text()) == record
    for update in ({'status': 'running'}, {'status': 'failed'}, {'cleanup_complete': False}, {'inputs': 'changed'}):
        path.write_text(json.dumps({**record, **update}))
        with pytest.raises(ValueError):
            rearm_interrupted_jobs([spec], steps=20)
        assert path.exists()
