"""Real filesystem checks and numerical subprocesses exercise command admission."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest
import yaml

from app.harness.tools.execution.command_preflight import bounded_error, preflight_state, run_command_preflight
from app.harness.tools.execution.local_command import LocalCommandJob, _command, command_request, run_local_command
from app.settings import repo_root, reset_settings_cache


@pytest.fixture
def genuine_checker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[LocalCommandJob, Path, Path]]:
    registry = tmp_path / 'registry.json'
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(registry))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    from app.harness.project_workspace import open_folder
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = tmp_path / 'check_and_measure.py'
    source.write_text(
        "import hashlib,json,os,pathlib\n"
        "r=json.loads(pathlib.Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
        "path=pathlib.Path(r['config']['data_path'])\n"
        "samples=json.loads(path.read_text())\n"
        "assert os.environ['MARS_DATA_PATH']==str(path)\n"
        "assert path.is_relative_to(pathlib.Path(os.environ['MARS_PROJECT_ROOT']))\n"
        "assert samples and r['steps']>0\n"
        "input_files=[{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]\n"
        "extra=pathlib.Path(os.environ['MARS_PROJECT_ROOT'],'checkpoint.json')\n"
        "if extra.exists():\n"
        " json.loads(extra.read_text())\n"
        " input_files.append({'path':str(extra),'sha256':hashlib.sha256(extra.read_bytes()).hexdigest()})\n"
        "if os.environ.get('MARS_VALIDATE_ONLY')=='1':\n"
        " result={'schema':'local_command_validation.v1','status':'ready','invocation_id':r['invocation_id'],"
        "'run_id':r['run_id'],'experiment_id':r['experiment_id'],'checks':[{'name':'actual_data_and_budget',"
        "'ready':True,'details':{'samples':len(samples),'steps':r['steps'],'data_path':str(path),'input_files':input_files}}]}\n"
        "else:\n"
        " scale=sum(x*y for x,y in samples)/sum(x*x for x,y in samples)\n"
        " mse=sum((scale*x-y)**2 for x,y in samples)/len(samples)\n"
        " pathlib.Path('measured.json').write_text(json.dumps({'scale':scale,'mse':mse,'samples':samples}))\n"
        " pathlib.Path(os.environ['MARS_PROJECT_ROOT'],'actual_execution.txt').write_text(str(mse))\n"
        " result={'schema':'local_command_result.v1','status':'completed','invocation_id':r['invocation_id'],"
        "'run_id':r['run_id'],'experiment_id':r['experiment_id'],'metrics':{'mse':mse},'evidence_paths':['measured.json']}\n"
        "pathlib.Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps(result))\n",
        encoding='utf-8')
    data = project.root / 'samples.json'
    data.write_text('[[1,2],[2,4],[3,6]]')
    config = tmp_path / 'host-execution.yaml'
    command: dict[str, Any] = {'id': 'actual', 'argv': [sys.executable, str(source)], 'required_metrics': ['mse'],
        'project_ids': [project.name], 'entrypoints': ['tools/check_and_measure.py'], 'config_paths': ['configs/*.yaml'],
        'consumes_data_path': True,
        'validation': {'environment': {'MARS_VALIDATE_ONLY': '1'}, 'timeout_seconds': 2, 'max_output_bytes': 65536}}
    config.write_text(yaml.safe_dump({'execution': {'backend': 'local_command',
        'command_timeout_seconds': 3, 'local_commands': [command]}}))
    tool_config = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
    for name in ('execution.simulation_runner', 'execution.batch_runner'):
        tool_config['tools'][name]['command_allowlist'] = [[sys.executable, str(source)]]
    tools = tmp_path / 'host-tools.yaml'
    tools.write_text(yaml.safe_dump(tool_config))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(config))
    monkeypatch.setenv('MARS_TOOLS_CONFIG_PATH', str(tools))
    reset_settings_cache()
    job = LocalCommandJob(run_id='genuine', experiment_id='candidate', project=project.name,
        run_root=project.root / 'runs/genuine', command_id='actual', seed=2026, steps=50,
        config={'command_id': 'actual', 'entrypoint': 'tools/check_and_measure.py',
                'config_path': 'configs/actual.yaml', 'data_path': str(data), 'budget_steps': 3, 'seed': 2026})
    try:
        yield job, config, data
    finally:
        reset_settings_cache()


def test_explicit_registration_checks_project_entrypoint_and_config(genuine_checker: tuple[LocalCommandJob, Path, Path]) -> None:
    job, _, _ = genuine_checker
    assert _command(job, 'execution.simulation_runner')[0][0] == sys.executable
    for invalid in (replace(job, command_id='', config={}), replace(job, project='other'),
            replace(job, config={**job.config, 'entrypoint': 'tools/other.py'}),
            replace(job, config={**job.config, 'config_path': '../outside.yaml'}),
            replace(job, config={**job.config, 'config_path': 'C:\\outside.yaml'})):
        with pytest.raises(ValueError):
            _command(invalid, 'execution.simulation_runner')


def test_actual_preflight_checks_data_budget_without_executing_measurement(genuine_checker: tuple[LocalCommandJob, Path, Path]) -> None:
    job, _, data = genuine_checker
    state = run_command_preflight(job, 'approved-token')
    assert state['status'] == 'passed'
    assert preflight_state(job, 'approved-token')['receipt_sha256'] == state['receipt_sha256']
    assert preflight_state(job, 'changed-token')['status'] == 'required'
    receipt = json.loads(Path(state['receipt_path']).read_text())
    request = json.loads(Path(state['receipt_path']).with_name('job.json').read_text())
    assert receipt['returncode'] == 0
    assert request['steps'] == 3 and request['seed'] == 2026
    assert request['config']['data_path'] == str(data)
    assert request['data_binding']['path'] == str(data)
    assert receipt['checks'][0]['details']['samples'] == 3
    assert receipt['input_files'][0]['path'] == str(data)
    assert not (data.parent / 'actual_execution.txt').exists()
    assert not (job.run_root / 'execution/local_commands').exists()
    data.write_text('not valid JSON')
    failed = run_command_preflight(job, 'approved-token')
    assert failed['status'] == 'failed' and 'JSONDecodeError' in failed['error']
    assert preflight_state(job, 'approved-token')['status'] == 'failed'


@pytest.mark.asyncio
async def test_direct_execution_cannot_bypass_configured_checker(genuine_checker: tuple[LocalCommandJob, Path, Path]) -> None:
    job, _, data = genuine_checker
    data.write_text('broken input')
    failed = await run_local_command(job)
    assert failed.status == 'failed' and 'JSONDecodeError' in failed.error
    assert not (job.run_root / 'execution/local_commands').exists()
    assert not (data.parent / 'actual_execution.txt').exists()
    data.write_text('[[1,2],[2,4],[3,6]]')
    completed = await run_local_command(job)
    assert completed.status == 'completed' and completed.metrics == {'mse': 0}
    assert (data.parent / 'actual_execution.txt').read_text() == '0.0'
    assert any(row['kind'] == 'execution_log' for row in completed.artifacts)


def test_declared_data_binding_rejects_external_file(genuine_checker: tuple[LocalCommandJob, Path, Path], tmp_path: Path) -> None:
    job, _, _ = genuine_checker
    external = tmp_path / 'external.json'
    external.write_text('[[1,2]]')
    with pytest.raises(ValueError, match='outside the host-bound'):
        command_request(replace(job, config={**job.config, 'data_path': str(external)}), 'preview', job.run_root)


def test_legacy_registration_is_explicitly_unverified(genuine_checker: tuple[LocalCommandJob, Path, Path]) -> None:
    job, config, _ = genuine_checker
    raw = yaml.safe_load(config.read_text())
    raw['execution']['local_commands'][0].pop('validation')
    config.write_text(yaml.safe_dump(raw))
    assert run_command_preflight(job, 'legacy-token') == {'status': 'unverified', 'configured': False}
    assert not (job.run_root / 'execution/preflight').exists()


def test_preflight_request_and_result_hashes_cannot_be_replaced(genuine_checker: tuple[LocalCommandJob, Path, Path]) -> None:
    job, _, _ = genuine_checker
    state = run_command_preflight(job, 'hash-token')
    path = Path(state['receipt_path']).with_name('result.json')
    original = path.read_bytes()
    path.write_text('{}')
    with pytest.raises(ValueError, match='request or result changed'):
        preflight_state(job, 'hash-token')
    path.write_bytes(original)
    assert preflight_state(job, 'hash-token')['status'] == 'passed'
    request = path.with_name('job.json')
    request.write_text('{}')
    with pytest.raises(ValueError, match='request or result changed'):
        preflight_state(job, 'hash-token')


def _approved_run(job: LocalCommandJob) -> Any:
    from app.storage.run_store import RunHandle, RUN_SUBDIRS
    from app.storage.artifact_store import ArtifactStore
    from app.harness.schema.frontmatter_parser import dumps
    run = RunHandle(run_id=job.run_id, root=job.run_root, project=job.project,
        task='Actual command environment check', entrypoint='execution', created_at='2026-10-13T00:00:00+00:00')
    for name in RUN_SUBDIRS:
        run.subdir(name).mkdir(parents=True, exist_ok=True)
    metadata = {'schema': 'run_log.v1', 'project': run.project, 'agent': 'execution',
        'run_id': run.run_id, 'status': 'interrupted', 'metrics': {'planned_experiments': 1},
        'fingerprint_hash': 'sha256:00', 'planned_experiments': [{'name': job.experiment_id, 'config': job.config}]}
    artifacts = ArtifactStore(run)
    artifacts.approve(artifacts.write(text=dumps(metadata, 'Human-authored input, not a result.')))
    return run


def test_confirmation_requires_real_preflight_and_is_invalidated_by_data_change(
    genuine_checker: tuple[LocalCommandJob, Path, Path],
) -> None:
    from app.bridge.execution_confirmation import execution_preview, save_confirmation, require_confirmation
    job, _, data = genuine_checker
    run = _approved_run(job)
    before = execution_preview(run, 'execution')
    assert not before['blockers'] and not before['confirmed']
    assert before['validation'][0]['status'] == 'required'
    assert not (job.run_root / 'execution/preflight').exists()
    data.write_text('invalid input')
    invalid = execution_preview(run, 'execution')
    with pytest.raises(ValueError, match='JSONDecodeError'):
        save_confirmation(run, 'execution', invalid['token'])
    assert not (run.root / 'execution/confirmations' / (invalid['token'] + '.json')).exists()
    failed = execution_preview(run, 'execution')
    assert failed['validation'][0]['status'] == 'failed' and not failed['confirmed']
    data.write_text('[[1,2],[2,4],[3,6]]')
    current = execution_preview(run, 'execution')
    saved = save_confirmation(run, 'execution', current['token'])
    assert saved['confirmed'] and saved['validation'][0]['status'] == 'passed'
    assert require_confirmation(run, 'execution')['confirmed']
    assert not (data.parent / 'actual_execution.txt').exists()
    data.write_text('[[1,3]]')
    changed = execution_preview(run, 'execution')
    assert changed['token'] != current['token'] and not changed['confirmed']
    with pytest.raises(ValueError, match='尚未确认'):
        require_confirmation(run, 'execution')


@pytest.mark.asyncio
@pytest.mark.parametrize('previous_validation', [False, True])
async def test_explicit_retry_uses_renewed_confirmation_for_real_batch_admission(
    genuine_checker: tuple[LocalCommandJob, Path, Path], previous_validation: bool,
) -> None:
    from app.bridge.agent_runner import _run_execution_batch
    from app.bridge.execution_confirmation import execution_preview, renew_execution_confirmation, save_confirmation
    from app.bridge.tensorboard_service import shutdown_tensorboard
    job, _, data = genuine_checker
    run = _approved_run(job)
    initial = execution_preview(run, 'execution')
    original_receipt = run.root / 'execution/confirmations' / (initial['token'] + '.json')
    if previous_validation:
        save_confirmation(run, 'execution', initial['token'])
    original_bytes = original_receipt.read_bytes() if previous_validation else None
    renew_execution_confirmation(run, 'execution')
    retry = execution_preview(run, 'execution')
    assert retry['inputs_token'] == initial['inputs_token']
    assert retry['token'] != retry['inputs_token'] and not retry['confirmed']
    assert save_confirmation(run, 'execution', retry['token'])['validation'][0]['status'] == 'passed'
    assert not (data.parent / 'actual_execution.txt').exists()
    try:
        await _run_execution_batch(run=run, node_key='execution')
    finally:
        await shutdown_tensorboard()
    summary = json.loads((run.root / 'execution/batch_summary.json').read_text())
    assert not summary['failures'] and summary['experiments'] == [job.experiment_id]
    request_path = next((run.root / 'execution/local_commands' / job.experiment_id).glob('*/job.json'))
    request = json.loads(request_path.read_text())
    assert request['config']['confirmation_token'] == retry['token']
    assert request['steps'] == 3 and request['seed'] == 2026
    assert (data.parent / 'actual_execution.txt').read_text() == '0.0'
    if previous_validation:
        assert original_receipt.read_bytes() == original_bytes
    else:
        assert not original_receipt.exists()


@pytest.mark.asyncio
async def test_renewed_decision_reuses_only_same_host_bound_inputs_and_intact_measurements(
    genuine_checker: tuple[LocalCommandJob, Path, Path],
) -> None:
    from app.bridge.agent_runner import _run_execution_batch
    from app.bridge.execution_confirmation import execution_preview, renew_execution_confirmation, save_confirmation
    from app.bridge.tensorboard_service import shutdown_tensorboard
    job, _, data = genuine_checker
    run = _approved_run(job)
    first = execution_preview(run, 'execution')
    save_confirmation(run, 'execution', first['token'])
    try:
        await _run_execution_batch(run=run, node_key='execution')
        original = json.loads((run.root / 'execution/metrics.json').read_text())[0]
        renew_execution_confirmation(run, 'execution')
        same = execution_preview(run, 'execution')
        assert same['inputs_token'] == first['inputs_token'] and same['token'] != first['token']
        save_confirmation(run, 'execution', same['token'])
        await _run_execution_batch(run=run, node_key='execution')
        reused = json.loads((run.root / 'execution/metrics.json').read_text())[0]
        assert reused['fingerprint_hash'] == original['fingerprint_hash']
        directory = run.root / 'execution/local_commands' / job.experiment_id
        assert len(list(directory.glob('*/job.json'))) == 1
        data.write_text('[[1,3],[2,6],[3,9]]')
        changed = execution_preview(run, 'execution')
        assert changed['inputs_token'] != same['inputs_token'] and not changed['confirmed']
        save_confirmation(run, 'execution', changed['token'])
        await _run_execution_batch(run=run, node_key='execution')
        measured = json.loads((run.root / 'execution/metrics.json').read_text())[0]
        assert measured['fingerprint_hash'] != original['fingerprint_hash']
        requests = list(directory.glob('*/job.json'))
        assert len(requests) == 2
        measurements = [json.loads(path.with_name('measured.json').read_text()) for path in requests]
        assert sorted(row['scale'] for row in measurements) == [2.0, 3.0]
    finally:
        await shutdown_tensorboard()


@pytest.mark.asyncio
async def test_checked_hidden_checkpoint_change_requires_new_confirmation_and_cannot_be_silently_revalidated(
    genuine_checker: tuple[LocalCommandJob, Path, Path],
) -> None:
    from app.bridge.execution_confirmation import execution_preview, save_confirmation, require_confirmation
    from app.bridge.execution_batch_plan import prepare_execution
    job, _, data = genuine_checker
    checkpoint = data.parent / 'checkpoint.json'
    checkpoint.write_text('[1,2,3]')
    run = _approved_run(job)
    before = execution_preview(run, 'execution')
    assert save_confirmation(run, 'execution', before['token'])['confirmed']
    saved_job = prepare_execution(run, 'execution').specs[0]
    admitted = replace(job, config={**saved_job.config, 'confirmation_token': before['token']})
    checkpoint.write_text('[4,5,6]')
    changed = execution_preview(run, 'execution')
    assert changed['token'] != before['token'] and changed['validated_input_changes']
    assert not changed['confirmed']
    with pytest.raises(ValueError, match='missing or stale'):
        await run_local_command(admitted)
    assert not (data.parent / 'actual_execution.txt').exists()
    with pytest.raises(ValueError, match='变化'):
        save_confirmation(run, 'execution', before['token'])
    assert save_confirmation(run, 'execution', changed['token'])['confirmed']
    assert require_confirmation(run, 'execution')['confirmed']


@pytest.mark.asyncio
async def test_actual_admission_hash_check_catches_same_stat_content_change(
    genuine_checker: tuple[LocalCommandJob, Path, Path],
) -> None:
    from app.bridge.execution_confirmation import execution_preview, save_confirmation
    from app.bridge.execution_batch_plan import prepare_execution
    job, _, data = genuine_checker
    checkpoint = data.parent / 'checkpoint.json'
    checkpoint.write_text('[1,2,3]')
    run = _approved_run(job)
    view = execution_preview(run, 'execution')
    save_confirmation(run, 'execution', view['token'])
    stat = checkpoint.stat()
    checkpoint.write_text('[4,5,6]')
    os.utime(checkpoint, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    # Polling is intentionally stat-based. Execution independently hashes the
    # real inputs and never approves a change hidden behind unchanged metadata.
    assert execution_preview(run, 'execution')['confirmed']
    config = prepare_execution(run, 'execution').specs[0].config
    with pytest.raises(ValueError, match='input hashes changed after confirmation'):
        await run_local_command(replace(job, config={**config, 'confirmation_token': view['token']}))
    assert not (data.parent / 'actual_execution.txt').exists()


def test_real_checker_timeout_does_not_launch_measurement(genuine_checker: tuple[LocalCommandJob, Path, Path]) -> None:
    job, config, data = genuine_checker
    raw = yaml.safe_load(config.read_text())
    source = Path(raw['execution']['local_commands'][0]['argv'][1])
    source.write_text('import time;time.sleep(2)\n' + source.read_text())
    raw['execution']['local_commands'][0]['validation']['timeout_seconds'] = 0.05
    config.write_text(yaml.safe_dump(raw))
    state = run_command_preflight(job, 'timeout-token')
    assert state['status'] == 'failed' and 'timed out' in state['error']
    assert not (data.parent / 'actual_execution.txt').exists()


def test_error_details_are_bounded_and_redact_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RESEARCH_API_KEY', 'human-authored-sensitive-test-value')
    result = bounded_error('token=literal-secret Bearer token-value\n'
                           'https://user:password@example.test/ human-authored-sensitive-test-value\n'
                           + 'x' * 5000 + '\nValueError: incompatible config', max_chars=200)
    assert len(result) <= 200 and result.endswith('ValueError: incompatible config')
    result = bounded_error('token=literal-secret Bearer token-value human-authored-sensitive-test-value')
    assert 'literal-secret' not in result and 'token-value' not in result
    assert 'human-authored-sensitive-test-value' not in result


def test_authentication_and_session_headers_are_fully_redacted() -> None:
    result = bounded_error('Authorization: Basic dXNlcjpwYXNzd29yZA==\n'
                           'Proxy-Authorization: Digest username="example", response="secret-response"\n'
                           'Cookie: sessionid=private-session-value; csrftoken=another-secret\n'
                           'Set-Cookie: sid=private-session-value; HttpOnly\n'
                           'ValueError: data path is unavailable')
    for secret in ('dXNlcjpwYXNzd29yZA', 'secret-response', 'private-session-value', 'another-secret'):
        assert secret not in result
    assert result.endswith('ValueError: data path is unavailable')
