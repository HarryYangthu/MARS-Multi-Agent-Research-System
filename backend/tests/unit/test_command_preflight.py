"""Real filesystem checks and numerical subprocesses exercise command admission."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pytest
import yaml

from app.harness.tools.execution.command_preflight import (
    _verified_inputs, bounded_error, known_input_changes, preflight_state, run_command_preflight,
)
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
        " assert r['config']['budget_unit']=='steps' and r['config']['max_iters']==r['steps']\n"
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
    assert request['config']['budget_unit'] == 'steps' and request['config']['max_iters'] == 3
    assert 'budget_unit' not in job.config and 'max_iters' not in job.config
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


@pytest.mark.parametrize(('declared', 'expected_unit', 'expected_count'), [
    ({'budget_steps': 7}, 'steps', 7),
    ({'budget_unit': 'steps', 'max_iters': 7}, 'steps', 7),
    ({'budget_steps': 7, 'budget_unit': 'steps', 'max_iters': 7}, 'steps', 7),
    ({'budget_unit': 'epochs', 'max_iters': 2}, 'epochs', 2),
    ({}, None, 50),
])
def test_command_request_preserves_budget_unit_and_original_configuration(
    genuine_checker: tuple[LocalCommandJob, Path, Path], declared: dict[str, Any],
    expected_unit: str | None, expected_count: int,
) -> None:
    job, _, _ = genuine_checker
    config = {key: value for key, value in job.config.items()
              if key not in {'budget_steps', 'budget_unit', 'max_iters'}}
    config.update(declared)
    original = dict(config)
    request = command_request(replace(job, config=config), 'actual-budget', job.run_root / 'request')
    assert request['steps'] == expected_count
    assert config == original
    if expected_unit is None:
        assert 'budget_unit' not in request['config'] and 'max_iters' not in request['config']
    else:
        assert request['config']['budget_unit'] == expected_unit
        assert request['config']['max_iters'] == expected_count
    assert ('budget_steps' in request['config']) == ('budget_steps' in declared)


@pytest.mark.parametrize('declared', [
    {'budget_steps': 7, 'max_iters': 8},
    {'budget_steps': 7, 'budget_unit': 'epochs'},
    {'budget_steps': True},
    {'max_iters': 7},
])
def test_command_request_rejects_conflicting_or_ambiguous_budgets(
    genuine_checker: tuple[LocalCommandJob, Path, Path], declared: dict[str, Any],
) -> None:
    job, _, _ = genuine_checker
    config = {key: value for key, value in job.config.items()
              if key not in {'budget_steps', 'budget_unit', 'max_iters'}}
    config.update(declared)
    with pytest.raises(ValueError):
        command_request(replace(job, config=config), 'invalid-budget', job.run_root / 'request')


def test_multiple_checked_inputs_revalidate_real_git_binding_between_operations(
    genuine_checker: tuple[LocalCommandJob, Path, Path],
) -> None:
    from app.harness.tools.git_branch import GitBranch, GitWorkspaceError, bind_git_branch, git
    job, _, data = genuine_checker
    code = data.parent / 'code'
    code.mkdir(exist_ok=True)
    git(code, 'init', '-b', 'baseline')
    (code / 'main.py').write_text('VALUE = 1\n')
    git(code, 'add', 'main.py')
    git(code, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c', 'commit.gpgsign=false',
        'commit', '-m', 'Genuine preflight baseline')
    baseline = git(code, 'rev-parse', 'HEAD')
    git(code, 'switch', '-c', 'study')
    branch = GitBranch(job.project, job.run_id, str(code), 'study', 'baseline', baseline)
    checkpoint = data.parent / 'checkpoint.json'
    checkpoint.write_text('[1,2,3]')
    with bind_git_branch(branch):
        state = run_command_preflight(job, 'multi-input-token')
        assert state['status'] == 'passed'
        result = json.loads(Path(state['receipt_path']).with_name('result.json').read_text())
        records = _verified_inputs(job, result, deadline=time.monotonic() + 2, max_bytes=4096)
        assert {row['path'] for row in records} == {str(data), str(checkpoint)}
        assert known_input_changes(job, 'multi-input-token') == []
        assert preflight_state(job, 'multi-input-token')['status'] == 'passed'
        # A successful earlier operation never grants a later operation access
        # after an external branch switch. These are actual Git invocations.
        git(code, 'switch', 'baseline')
        with pytest.raises(GitWorkspaceError, match='切换到其他分支'):
            _verified_inputs(job, result, deadline=time.monotonic() + 2, max_bytes=4096)
        with pytest.raises(GitWorkspaceError, match='切换到其他分支'):
            known_input_changes(job, 'multi-input-token')
        with pytest.raises(GitWorkspaceError, match='切换到其他分支'):
            preflight_state(job, 'multi-input-token')
    assert not (data.parent / 'actual_execution.txt').exists()


def test_each_input_still_checks_hash_scope_symlinks_and_control_paths(
    genuine_checker: tuple[LocalCommandJob, Path, Path], tmp_path: Path,
) -> None:
    job, _, data = genuine_checker
    other = data.parent / 'other.json'
    other.write_text('[4,5,6]')

    def inputs(*paths: Path) -> dict[str, Any]:
        return {'checks': [{'details': {'input_files': [
            {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths
        ]}}]}

    saved = inputs(data, other)
    assert len(_verified_inputs(job, saved, deadline=time.monotonic() + 2, max_bytes=4096)) == 2
    other.write_text('[7,8,9]')
    with pytest.raises(ValueError, match='hash does not match'):
        _verified_inputs(job, saved, deadline=time.monotonic() + 2, max_bytes=4096)
    link = data.parent / 'symbolic.json'
    link.symlink_to(data)
    forbidden = [tmp_path / 'outside.json', data.parent / '.env.private',
                 data.parent / '.git' / 'config', data.parent / '.ssh' / 'identity',
                 data.parent / 'private.pem']
    for path in forbidden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('human-authored boundary fixture')
    for path in [link, *forbidden]:
        with pytest.raises(ValueError, match='absolute regular file|control/credential files'):
            _verified_inputs(job, inputs(data, path), deadline=time.monotonic() + 2, max_bytes=4096)


def _actual_legacy_zero_exit(
    genuine_checker: tuple[LocalCommandJob, Path, Path], tmp_path: Path,
) -> tuple[LocalCommandJob, Path, Path]:
    """Execute archived host code and genuine computation under its old schema."""
    from app.harness.runtime.git_runtime import GitRuntimeError, run_git
    job, config, data = genuine_checker
    registered = yaml.safe_load(config.read_text())['execution']['local_commands'][0]
    source = Path(registered['argv'][1])
    text = source.read_text()
    text = text.replace(" result={'schema':'local_command_result.v1'", " count=path.parent/'measurement_count.txt'\n"
        " count.write_text(str(int(count.read_text())+1) if count.exists() else '1')\n"
        " result={'schema':'local_command_result.v1'")
    text = text.replace("'metrics':{'mse':mse},'evidence_paths'",
        "'metrics':{'mse':mse},'warnings':['Measurement uses three actual fixture samples'],'evidence_paths'")
    source.write_text(text)
    job = replace(job, config={**job.config, 'attempt': 1, 'confirmation_token': 'original-decision',
                              'confirmation_inputs_token': 'original-inputs'})
    assert run_command_preflight(job, 'original-decision')['status'] == 'passed'
    legacy = tmp_path / 'legacy_local_command.py'
    try:
        archived_source = run_git(['show', '55b2c087:backend/app/harness/tools/execution/local_command.py'],
                                   cwd=repo_root()).stdout
    except GitRuntimeError:
        pytest.skip('Genuine archived 55b2c087 host code is unavailable in this checkout')
    legacy.write_text(archived_source)
    declaration = tmp_path / 'legacy_host_job.json'
    declaration.write_text(json.dumps({'run_id': job.run_id, 'experiment_id': job.experiment_id,
        'project': job.project, 'run_root': str(job.run_root), 'config': job.config,
        'seed': job.seed, 'steps': job.steps, 'command_id': job.command_id}))
    launcher = (
        "import asyncio,importlib.util,json,pathlib,sys\n"
        "s=importlib.util.spec_from_file_location('actual_legacy_command',sys.argv[1])\n"
        "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n"
        "j=json.loads(pathlib.Path(sys.argv[2]).read_text());j['run_root']=pathlib.Path(j['run_root'])\n"
        "r=asyncio.run(m.run_local_command(m.LocalCommandJob(**j)))\n"
        "assert r.status=='failed',r.status\n"
    )
    completed = subprocess.run([sys.executable, '-c', launcher, str(legacy), str(declaration)],
                               cwd=repo_root(), capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    receipt = next((job.run_root / 'execution/local_commands' / job.experiment_id).glob('*/execution_receipt.json'))
    raw = json.loads(receipt.read_text())
    assert raw['returncode'] == 0 and raw['status'] == 'failed'
    assert 'warnings' in raw['error']
    assert json.loads(receipt.with_name('result.json').read_text())['status'] == 'completed'
    assert (data.parent / 'measurement_count.txt').read_text() == '1'
    return job, receipt, source


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', [False, True])
async def test_actual_result_warnings_extension_and_concise_schema_failure(
    genuine_checker: tuple[LocalCommandJob, Path, Path], invalid: bool,
) -> None:
    job, configuration, _ = genuine_checker
    source = Path(yaml.safe_load(configuration.read_text())['execution']['local_commands'][0]['argv'][1])
    source.write_text(source.read_text().replace("'metrics':{'mse':mse},'evidence_paths'",
        "'metrics':{'mse':mse},'warnings':r['config']['warnings'],'evidence_paths'"))
    actual = await run_local_command(replace(job, config={**job.config,
        'warnings': [7] if invalid else ['Measured zero residual on three actual fixture samples']}))
    if invalid:
        assert actual.status == 'failed' and 'schema validation failed at warnings.0' in actual.error
        assert "7 is not of type 'string'" in actual.error
        assert 'On instance' not in actual.error and 'Failed validating' not in actual.error
    else:
        assert actual.status == 'completed' and actual.metrics == {'mse': 0}


@pytest.mark.asyncio
async def test_zero_exit_legacy_schema_failure_recovers_without_repeating_real_measurement(
    genuine_checker: tuple[LocalCommandJob, Path, Path], tmp_path: Path,
) -> None:
    from app.execution.job_journal import _evidence, run_managed_job
    from app.execution.simulation_runner import JobSpec
    from app.harness.reporting.execution_evidence import execution_job_evidence
    job, old_receipt, _ = _actual_legacy_zero_exit(genuine_checker, tmp_path)
    old_files = {path: path.read_bytes() for path in old_receipt.parent.iterdir() if path.is_file()}
    retry = replace(job, config={**job.config, 'attempt': 2, 'confirmation_token': 'renewed-decision',
                                'confirmation_inputs_token': 'renewed-inputs'})
    assert run_command_preflight(retry, 'renewed-decision')['status'] == 'passed'
    spec = JobSpec(run_id=job.run_id, experiment_id=job.experiment_id, project=job.project,
                   run_root=job.run_root, config=retry.config, seed=job.seed)
    recovered = await run_managed_job(spec, steps=retry.steps)
    assert recovered.status == 'completed' and recovered.metrics == {'mse': 0}
    assert (genuine_checker[2].parent / 'measurement_count.txt').read_text() == '1'
    for path, original in old_files.items():
        assert path.read_bytes() == original
    receipt_path = next(path for path in old_receipt.parent.parent.glob('*/execution_receipt.json')
                        if 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() == recovered.fingerprint_hash)
    assert receipt_path != old_receipt and receipt_path.name == 'execution_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'completed' and receipt['returncode'] == 0
    assert receipt['recovery']['training_rerun'] is False
    assert receipt['recovery']['source_receipt_path'] == str(old_receipt)
    evidence = _evidence(spec, recovered)
    assert str(old_receipt) in {row['path'] for row in evidence}
    assert str(old_receipt.with_name('result.json')) in {row['path'] for row in evidence}
    handoff = execution_job_evidence(job.run_root, job.run_id, job.project, admit_materials=True)
    assert 'evidence_error' not in handoff
    assert 'measurement_recovered_without_retraining' in handoff
    assert 'original host contract failure; retained audit, not a new measurement' in handoff


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', ['request', 'result', 'command', 'same_stat_input', 'missing_evidence'])
async def test_same_job_legacy_zero_exit_tampering_cannot_trigger_recovery_or_retraining(
    genuine_checker: tuple[LocalCommandJob, Path, Path], tmp_path: Path, tamper: str,
) -> None:
    job, receipt, source = _actual_legacy_zero_exit(genuine_checker, tmp_path)
    if tamper in {'request', 'result'}:
        path = receipt.with_name('job.json' if tamper == 'request' else 'result.json')
        path.write_text(path.read_text() + '\n')
    elif tamper == 'command':
        source.write_text(source.read_text() + '\n# Different actual command bytes\n')
    elif tamper == 'same_stat_input':
        data = genuine_checker[2]
        stat = data.stat()
        data.write_text('[[1,3],[2,6],[3,9]]')
        os.utime(data, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        receipt.with_name('measured.json').unlink()
    retry = replace(job, config={**job.config, 'attempt': 2, 'confirmation_token': 'renewed-decision'})
    assert run_command_preflight(retry, 'renewed-decision')['status'] == 'passed'
    with pytest.raises(ValueError):
        await run_local_command(retry)
    assert (genuine_checker[2].parent / 'measurement_count.txt').read_text() == '1'
    assert len(list(receipt.parent.parent.glob('*/execution_receipt.json'))) == 1


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
