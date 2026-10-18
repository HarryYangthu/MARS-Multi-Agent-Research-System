"""Real filesystem/Git/process receipts; no model or service substitutes."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, ExitStack
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any
import uuid

import pytest
import yaml

from app.bridge.coding_reuse_evidence import (
    ReuseUnavailable, _approved_recipe, _completed_receipt, _match_inputs,
    _regular, _requested, _sha, _verified_preflight, inspect_reusable_execution,
)
from app.bridge.research_branch import research_branch_scope
from app.harness.project_workspace import open_folder
from app.harness.runtime.git_runtime import resolve_git
from app.harness.tools.execution.local_command import LocalCommandJob, run_local_command
from app.harness.tools.git_branch import git, receipt_path
from app.settings import reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RunStore


_RUNNER = '''import hashlib, json, os
from pathlib import Path
request = json.loads(Path(os.environ['MARS_JOB_REQUEST']).read_text())
code = Path(request['code_workspace'])
data = Path(request['config']['data_path'])
files = [code / 'existing_runner.py', code / 'configs/example.yaml', data]
if os.environ.get('MARS_VALIDATE_ONLY') == '1':
    result = {'schema': 'local_command_validation.v1', 'status': 'ready',
        'checks': [{'name': 'actual_input_readiness', 'ready': True, 'details': {
            'training_started': False, 'budget_unit': request['config']['budget_unit'],
            'budget_value': request['steps'], 'seed': request['seed'], 'input_files': [{'path': str(p),
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}}]}
else:
    values = [value * float(request['config'].get('scale', 1)) for value in json.loads(data.read_text())['values']]
    config = (code / 'configs/example.yaml').read_text()
    assert config.strip() == 'existing_model: ordinary_linear'
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    measurement = {'mean': mean, 'variance': variance, 'count': len(values)}
    evidence = Path(request['output_dir']) / 'observed_statistics.json'
    evidence.write_text(json.dumps(measurement))
    result = {'schema': 'local_command_result.v1', 'status': 'completed',
        'metrics': measurement, 'evidence_paths': [evidence.name]}
for key in ('invocation_id', 'run_id', 'experiment_id'):
    result[key] = request[key]
Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps(result))
'''


def _request(**overrides: Any) -> dict[str, Any]:
    return {'entrypoint': 'existing_runner.py', 'config_path': 'configs/example.yaml',
            'seed': 2026, 'budget_unit': 'steps', 'max_iters': 20, **overrides}


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    original = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    reset_settings_cache()
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


@pytest.fixture
def real_project(tmp_path: Path) -> Iterator[tuple[RunStore, str, Path]]:
    with ExitStack() as stack:
        stack.enter_context(_environment({
            'MARS_GIT_EXECUTABLE': resolve_git().executable,
            'MARS_FOLDER_PROJECTS_REGISTRY': str(tmp_path / 'registry.json'),
            'MARS_EXECUTION_BACKEND': 'local_command'}))
        yield from _create_real_project(tmp_path, stack)


def _create_real_project(tmp_path: Path, stack: ExitStack) -> Iterator[tuple[RunStore, str, Path]]:
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = project.root / 'code/ordinary_linear'; source.mkdir(parents=True)
    (source / 'configs').mkdir()
    (source / 'existing_runner.py').write_text(_RUNNER, encoding='utf-8')
    (source / 'configs/example.yaml').write_text('existing_model: ordinary_linear\n')
    git(source, 'init', '-b', 'main')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Actual statistics command')
    (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({
        'repo_path': str(source), 'repo_mode': 'local_path', 'read_only': True,
        'allowed_paths': ['existing_runner.py', 'configs/*.yaml'], 'ignore_patterns': ['.git/', '.env*']}))
    data = project.root / 'data/values.json'; data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text(json.dumps({'values': [1, 3, 6, 8]}))
    argv = ['python', '-c', "import os, runpy; from pathlib import Path; "
            "runpy.run_path(str(Path(os.environ['MARS_CODE_ROOT']) / 'existing_runner.py'), run_name='__main__')"]
    execution = tmp_path / 'execution.yaml'
    execution.write_text(yaml.safe_dump({'execution': {
        'backend': 'local_command', 'command_timeout_seconds': 10,
        'local_commands': [{'id': 'ordinary_statistics', 'argv': argv,
            'entrypoints': ['existing_runner.py'], 'config_paths': ['configs/example.yaml'],
            'project_ids': [project.name], 'consumes_data_path': True,
            'required_metrics': ['mean', 'variance'],
            'validation': {'environment': {'MARS_VALIDATE_ONLY': '1'}, 'timeout_seconds': 10}}]}}))
    tools = tmp_path / 'tools.yaml'
    tools.write_text(yaml.safe_dump({'tools': {'execution.simulation_runner': {
        'enabled': True, 'process_backend': 'local_process', 'require_isolation': False,
        'command_allowlist': [['python']]}}}))
    stack.enter_context(_environment({'MARS_EXECUTION_CONFIG_PATH': str(execution),
        'MARS_TOOLS_CONFIG_PATH': str(tools),
        'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')}))
    yield RunStore(tmp_path / 'run_registry', project_storage=True), project.name, data


async def _completed_source(store: RunStore, project: str, data: Path,
                            overrides: dict[str, Any] | None = None) -> RunHandle:
    source = store.create(task='actual_existing_statistics', project=project, entrypoint='coding',
                          user_request='Measure existing data without changing code')
    with research_branch_scope(source, 'coding'):
        config = {**_request(), 'command_id': 'ordinary_statistics', 'data_path': str(data), **(overrides or {})}
        ref = ArtifactStore(source).write_metadata(metadata={
            'schema': 'code_spec.v1', 'agent': 'coding', 'project': project,
            'target_lang': 'python', 'baseline_compat': {'preserved': True},
            'files_changed': [], 'new_dependencies': [],
            'test_coverage': {'baseline_smoke_test': 'skipped', 'unit_tests_added': 0},
            'execution_jobs': [{'name': 'existing_statistics', 'config': config}]},
            body='Host-authored execution recipe; actual statistics are measured by the process.')
        ArtifactStore(source).approve(ref)
        result = await run_local_command(LocalCommandJob(source.run_id, 'existing_statistics', project, source.root,
            config=config, seed=2026, steps=20, command_id='ordinary_statistics'))
        assert result.status == 'completed'
        scale = float(config.get('scale', 1))
        assert result.metrics == {'mean': 4.5 * scale, 'variance': 7.25 * scale ** 2, 'count': 4.0}
    return source


@pytest.mark.parametrize('overrides', [
    {'entrypoint': '../outside.py'}, {'config_path': 'C:\\private\\config.yaml'},
    {'seed': True}, {'max_iters': 0}, {'budget_unit': 'batches'},
    {'command_id': 'model_selected_command'}, {'source_run_id': '../another'},
])
def test_request_requires_explicit_unambiguous_host_consumable_fields(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _requested(_request(**overrides))


def test_regular_file_rejects_parent_symlinks_and_outside(tmp_path: Path) -> None:
    bound = tmp_path / 'bound'; bound.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'secret.txt').write_text('private')
    (bound / 'alias').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReuseUnavailable):
        _regular(bound / 'alias/secret.txt', bound)
    with pytest.raises(ReuseUnavailable):
        _regular(outside / 'secret.txt', bound)


def test_inputs_use_content_hashes_and_relocate_only_the_bound_roots(tmp_path: Path) -> None:
    old_project, new_project = tmp_path / 'old', tmp_path / 'new'
    old_code, new_code = old_project / 'code/model', new_project / 'code/model'
    old_code.mkdir(parents=True); new_code.mkdir(parents=True)
    (old_code / 'model.py').write_text('coefficient = 2\n')
    (new_code / 'model.py').write_text('coefficient = 2\n')
    records = [{'path': str(old_code / 'model.py'), 'sha256': _sha(old_code / 'model.py')}]
    matched = _match_inputs(old_code, new_code, old_project, new_project, records)
    assert matched[0]['path'] == str(new_code / 'model.py')
    (new_code / 'model.py').write_text('coefficient = 3\n')
    with pytest.raises(ReuseUnavailable, match='changed'):
        _match_inputs(old_code, new_code, old_project, new_project, records)


@pytest.mark.asyncio
async def test_real_completed_recipe_prepares_different_budget_without_caching_success(
        real_project: tuple[RunStore, str, Path]) -> None:
    store, project, data = real_project
    source = await _completed_source(store, project, data)
    current = store.create(task='new_requested_measurement', project=project, entrypoint='coding',
                           data_source={'stored_path': str(data)})
    before = {path: path.read_bytes() for path in source.root.rglob('*.json')}
    with research_branch_scope(current, 'coding'):
        evidence = inspect_reusable_execution(current, _request(seed=2027, max_iters=35, source_run_id=source.run_id))
        assert evidence is not None
        assert evidence['source_run_id'] == source.run_id
        assert evidence['target_lang'] == 'python'
        config = evidence['execution_jobs'][0]['config']
        assert config['max_iters'] == 35 and config['seed'] == 2027
        assert config['command_id'] == 'ordinary_statistics'
        assert 'confirmation_token' not in config
        assert evidence['environment']['fresh_preflight_required'] is True
        assert evidence['environment']['packages_verified'] is False
        assert evidence['prior_check_provenance']['current_test_status'] == 'not_rerun'
        assert len(evidence['input_files']) == 3
        assert not list((current.root / 'execution').rglob('execution_receipt.json'))
        assert not (current.root / 'coding/code_spec.approved.md').exists()
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source_version', 'approved', 'result', 'measurement', 'config', 'dataset', 'argv'])
async def test_real_recipe_falls_back_on_changed_source_evidence(
        real_project: tuple[RunStore, str, Path], change: str) -> None:
    store, project, data = real_project
    source = await _completed_source(store, project, data)
    if change in {'source_version', 'approved'}:
        filename = 'code_spec.v1.md' if change == 'source_version' else 'code_spec.approved.md'
        path = source.root / 'coding' / filename
        path.write_text(path.read_text() + '\nChanged after approval.\n')
    elif change == 'result':
        path = next((source.root / 'execution/local_commands').glob('*/*/result.json'))
        raw = json.loads(path.read_text()); raw['metrics']['mean'] = 999
        path.write_text(json.dumps(raw))
    elif change == 'measurement':
        next((source.root / 'execution/local_commands').glob('*/*/observed_statistics.json')).write_text('{}')
    elif change == 'dataset':
        data.write_text(json.dumps({'values': [100, 200]}))
    elif change == 'config':
        branch = json.loads(receipt_path(source.root).read_text())
        (Path(branch['repo_path']) / 'configs/example.yaml').write_text('different: model\n')
    else:
        configured = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])
        policy = yaml.safe_load(configured.read_text())
        policy['execution']['local_commands'][0]['argv'].append('--unexpected')
        configured.write_text(yaml.safe_dump(policy))
    current = store.create(task='do_not_reuse_changed_evidence', project=project, entrypoint='coding')
    with research_branch_scope(current, 'coding'):
        assert inspect_reusable_execution(current, _request(source_run_id=source.run_id)) is None
    event = json.loads((current.root / 'events/agent_events.jsonl').read_text().splitlines()[-1])
    assert event['event'] == 'coding.execution_reuse_unavailable'
    assert 'private' not in event['reason']
    assert not list((current.root / 'execution').rglob('execution_receipt.json'))


@pytest.mark.asyncio
async def test_wrong_model_and_dirty_current_checkout_use_normal_coding(
        real_project: tuple[RunStore, str, Path]) -> None:
    store, project, data = real_project
    source = await _completed_source(store, project, data)
    current = store.create(task='normal_coding_fallback', project=project, entrypoint='coding')
    with research_branch_scope(current, 'coding'):
        assert inspect_reusable_execution(current, _request(config_path='configs/other.yaml')) is None
        branch = json.loads(receipt_path(current.root).read_text())
        (Path(branch['repo_path']) / 'existing_runner.py').write_text('raise RuntimeError("current edit")\n')
        assert inspect_reusable_execution(current, _request(source_run_id=source.run_id)) is None


@pytest.mark.asyncio
async def test_completed_label_without_real_receipt_and_wrong_project_cannot_establish_recipe(
        real_project: tuple[RunStore, str, Path]) -> None:
    store, project, _ = real_project
    source = store.create(task='metadata_only', project=project, entrypoint='coding')
    (source.root / 'run_state.json').write_text(json.dumps({'status': 'completed'}))
    current = store.create(task='verify_real_receipts', project=project, entrypoint='coding')
    with research_branch_scope(current, 'coding'):
        assert inspect_reusable_execution(current, _request(source_run_id=source.run_id)) is None


@pytest.mark.asyncio
async def test_real_no_code_tuned_recipe_does_not_add_unrequested_method_overrides(
        real_project: tuple[RunStore, str, Path]) -> None:
    store, project, data = real_project
    # The actual process evaluates scaled values, so this is a real different
    # method supplied through configuration, despite files_changed being empty.
    source = await _completed_source(store, project, data, overrides={'scale': 2})
    current = store.create(task='exact_yaml_request', project=project, entrypoint='coding',
                           data_source={'stored_path': str(data)})
    with research_branch_scope(current, 'coding'):
        assert inspect_reusable_execution(current, _request(source_run_id=source.run_id)) is None
    events = [json.loads(row) for row in (current.root / 'events/agent_events.jsonl').read_text().splitlines()]
    assert any(row.get('reason') == 'source_recipe_contains_unrequested_overrides' for row in events)
    assert not list((current.root / 'execution').rglob('execution_receipt.json'))


@pytest.mark.asyncio
async def test_real_host_preparation_runs_current_validation_without_model_training_or_approval(
        real_project: tuple[RunStore, str, Path]) -> None:
    from app.agents.base import RunRequest
    from app.bridge.coding_reuse import prepare_reused_artifact
    from app.harness.schema.validator import validate_document

    store, project, data = real_project
    source = await _completed_source(store, project, data)
    source_bytes = {path: path.read_bytes() for path in source.root.rglob('*') if path.is_file()}
    selection = {'id': 'current_values', 'stored_path': str(data), 'fs_mhz': 100, 'channel_count': 1}
    current = store.create(task='actual_host_preparation', project=project, entrypoint='coding',
        user_request='Measure the existing model with explicit current inputs', data_source=selection)
    invocation = uuid.uuid4().hex
    requested = _request(seed=2027, max_iters=35, source_run_id=source.run_id)
    request = RunRequest(project=project, user_request='Reuse existing implementation',
        extra={'invocation_id': invocation, 'existing_execution_job': requested})
    with research_branch_scope(current, 'coding'):
        artifact = await prepare_reused_artifact(current, request)
        assert artifact is not None
        validation = validate_document(artifact.text, expected_schema='code_spec.v1')
        assert validation.valid
        assert validation.metadata['project'] == project
        assert validation.metadata['files_changed'] == []
        assert validation.metadata['test_coverage']['baseline_smoke_test'] == 'skipped'
        config = validation.metadata['execution_jobs'][0]['config']
        assert config['seed'] == 2027 and config['max_iters'] == 35
        assert config['config_path'] == 'configs/example.yaml'
        reuse = json.loads((current.root / 'coding/reuse' / (invocation + '.json')).read_text())
        assert reuse['model_calls'] == 0 and reuse['training_started'] is False
        assert reuse['approved'] is False and reuse['full_test_suite_rerun'] is False
        assert reuse['requested'] == requested
        checked_path = current.root / reuse['current_preflight']['ref']
        assert _sha(checked_path) == reuse['current_preflight']['sha256']
        checked = json.loads(checked_path.read_text())
        assert checked['run_id'] == current.run_id and checked['status'] == 'passed'
        assert checked['returncode'] == 0 and checked['pid'] > 0
        assert checked['checks'][0]['details']['training_started'] is False
        fresh_job = json.loads(checked_path.with_name('job.json').read_text())
        assert fresh_job['run_id'] == current.run_id and fresh_job['seed'] == 2027
        assert fresh_job['steps'] == 35 and fresh_job['config']['data_source_id'] == 'current_values'
        assert fresh_job['config']['fs_mhz'] == 100 and fresh_job['config']['channel_count'] == 1
        assert fresh_job['invocation_id'] != reuse['source']['source_receipt_path'].split('/')[-2]
        assert 'metrics' not in artifact.metadata and '4.5' not in artifact.body
        assert not list((current.root / 'execution').rglob('execution_receipt.json'))
        assert not list((current.root / 'execution').rglob('observed_statistics.json'))
        assert not (current.root / 'coding/code_spec.approved.md').exists()
        assert not (current.root / 'agent_traces').exists()
    assert all(path.read_bytes() == content for path, content in source_bytes.items())


@pytest.mark.asyncio
async def test_cancelled_host_check_drains_the_real_worker_before_releasing_ownership() -> None:
    from app.bridge.coding_reuse import _bounded_check

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    exited = threading.Event()
    gate = threading.Event()

    def real_worker() -> int:
        loop.call_soon_threadsafe(started.set)
        try:
            if not gate.wait(timeout=10):
                raise TimeoutError('test worker gate was not released')
            return 13
        finally:
            exited.set()

    pending = asyncio.create_task(_bounded_check(real_worker))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        pending.cancel()
        # Yield event-loop turns rather than guessing a wall-clock sleep. The
        # thread stays at the explicit gate while cancellation reaches the host.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert pending.cancelling() == 1 and not pending.done() and not exited.is_set()
        pending.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert pending.cancelling() == 2 and not pending.done() and not exited.is_set()
    finally:
        gate.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, timeout=5)
    assert exited.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize('unit,count', [('steps', 35), ('epochs', 3)])
async def test_first_existing_model_uses_current_registration_and_real_preflight_without_history(
        real_project: tuple[RunStore, str, Path], unit: str, count: int) -> None:
    from app.agents.base import RunRequest
    from app.bridge.coding_reuse import prepare_reused_artifact
    from app.harness.schema.validator import validate_document

    store, project, data = real_project
    # A fresh project's explicitly selected data need not match a historical
    # training run. No historical run or approved code_spec exists in this case.
    chosen = data.with_name('new_selected_values.json')
    chosen.write_text(json.dumps({'values': [2, 4, 8, 10]}))
    current = store.create(task='first_existing_model', project=project, entrypoint='coding',
        data_source={'id': 'new_values', 'stored_path': str(chosen), 'fs_mhz': 100, 'channel_count': 1})
    assert [run.run_id for run in store.list()] == [current.run_id]
    invocation = uuid.uuid4().hex
    requested = _request(seed=2031, max_iters=count, budget_unit=unit)
    request = RunRequest(project=project, user_request='Run the already existing configuration',
        extra={'invocation_id': invocation, 'existing_execution_job': requested})
    with research_branch_scope(current, 'coding'):
        branch = json.loads(receipt_path(current.root).read_text())
        before = git(Path(branch['repo_path']), 'rev-parse', 'HEAD')
        artifact = await prepare_reused_artifact(current, request)
        assert artifact is not None
        validation = validate_document(artifact.text, expected_schema='code_spec.v1')
        assert validation.valid and validation.metadata['files_changed'] == []
        config = validation.metadata['execution_jobs'][0]['config']
        assert config == {**requested, 'command_id': 'ordinary_statistics', 'data_path': str(chosen),
                          'data_source_id': 'new_values', 'fs_mhz': 100, 'channel_count': 1,
                          'attempt': 1, 'label': validation.metadata['execution_jobs'][0]['name']}
        reuse = json.loads((current.root / 'coding/reuse' / (invocation + '.json')).read_text())
        assert reuse['mode'] == 'host_validated_registered_execution'
        assert reuse['model_calls'] == 0 and reuse['training_started'] is False and reuse['approved'] is False
        assert reuse['source']['schema'] == 'coding.registered_execution_evidence.v1'
        assert 'source_run_id' not in reuse['source']
        assert not any(key in reuse['source'] for key in ('metrics', 'source_receipt_path', 'source_code_spec_ref'))
        receipt = json.loads((current.root / reuse['current_preflight']['ref']).read_text())
        details = receipt['checks'][0]['details']
        assert receipt['status'] == 'passed' and receipt['pid'] > 0
        assert details['training_started'] is False and details['budget_unit'] == unit
        assert details['budget_value'] == count and details['seed'] == 2031
        assert any(row['path'] == str(chosen) for row in receipt['input_files'])
        assert not any(row['path'] == str(data) for row in receipt['input_files'])
        assert not list((current.root / 'execution').rglob('execution_receipt.json'))
        assert not list((current.root / 'execution').rglob('observed_statistics.json'))
        assert not (current.root / 'coding/code_spec.approved.md').exists()
        assert git(Path(branch['repo_path']), 'rev-parse', 'HEAD') == before
        assert git(Path(branch['repo_path']), 'status', '--porcelain') == ''
        events = [json.loads(line) for line in (current.root / 'events/agent_events.jsonl').read_text().splitlines()]
        assert not any(event.get('event') == 'coding.execution_reuse_unavailable' for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    'ambiguous', 'scope', 'validation', 'unit', 'extra_override', 'dirty', 'symlink',
])
async def test_untrusted_or_ambiguous_current_capability_falls_back_without_running(
        real_project: tuple[RunStore, str, Path], change: str) -> None:
    from app.agents.base import RunRequest
    from app.bridge.coding_reuse import prepare_reused_artifact

    store, project, data = real_project
    requested = _request()
    profile = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])
    policy = yaml.safe_load(profile.read_text())
    command = policy['execution']['local_commands'][0]
    if change == 'ambiguous':
        policy['execution']['local_commands'].append({**command, 'id': 'second_statistics'})
    elif change == 'scope':
        command.pop('config_paths')
    elif change == 'validation':
        command.pop('validation')
    elif change == 'unit':
        command['budget_units'] = ['epochs']
    elif change == 'extra_override':
        requested['learning_rate'] = 0.2
    profile.write_text(yaml.safe_dump(policy))
    current = store.create(task='decline_current_capability', project=project, entrypoint='coding',
        data_source={'stored_path': str(data)})
    request = RunRequest(project=project, user_request='Run existing model',
        extra={'invocation_id': uuid.uuid4().hex, 'existing_execution_job': requested})
    with research_branch_scope(current, 'coding'):
        branch = json.loads(receipt_path(current.root).read_text())
        config_path = Path(branch['repo_path']) / 'configs/example.yaml'
        if change == 'dirty':
            config_path.write_text('existing_model: changed\n')
        elif change == 'symlink':
            config_path.unlink()
            config_path.symlink_to(data)
            git(Path(branch['repo_path']), 'add', '.')
            git(Path(branch['repo_path']), '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                '-c', 'commit.gpgsign=false', 'commit', '-m', 'Actual symbolic config')
        assert await prepare_reused_artifact(current, request) is None
        assert not list((current.root / 'execution').rglob('validation_receipt.json'))
        assert not list((current.root / 'coding/reuse').glob('*.json'))


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['seed', 'budget_unit', 'training_started', 'budget_value'])
async def test_actual_checker_must_explicitly_verify_requested_seed_budget_and_no_training(
        real_project: tuple[RunStore, str, Path], missing: str) -> None:
    from app.agents.base import RunRequest
    from app.bridge.coding_reuse import prepare_reused_artifact
    from app.harness.project_workspace import folder_project

    store, project, data = real_project
    folder = folder_project(project)
    assert folder is not None
    source = folder.root / 'code/ordinary_linear'
    runner = source / 'existing_runner.py'
    # An actual checker with incomplete consumption evidence still runs as a
    # genuine subprocess, but it is not enough to skip ordinary Coding.
    removal = {'seed': "'seed': request['seed'], ",
               'budget_unit': "'budget_unit': request['config']['budget_unit'],\n            ",
               'training_started': "'training_started': False, ",
               'budget_value': "'budget_value': request['steps'], "}[missing]
    assert removal in runner.read_text()
    runner.write_text(runner.read_text().replace(removal, ''))
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Actual incomplete validation contract')
    current = store.create(task='incomplete_validation_contract', project=project, entrypoint='coding',
        data_source={'stored_path': str(data)})
    request = RunRequest(project=project, user_request='Run existing model',
        extra={'invocation_id': uuid.uuid4().hex, 'existing_execution_job': _request()})
    with research_branch_scope(current, 'coding'):
        assert await prepare_reused_artifact(current, request) is None
        receipts = list((current.root / 'execution/preflight').glob('*/validation_receipt.json'))
        assert len(receipts) == 1
        receipt = json.loads(receipts[0].read_text())
        assert receipt['pid'] > 0 and receipt['returncode'] == 0
        assert not list((current.root / 'execution').rglob('execution_receipt.json'))
        assert not list((current.root / 'coding/reuse').glob('*.json'))


@pytest.mark.asyncio
async def test_first_prepared_jobs_execute_on_two_isolated_tasks_with_current_data_and_budget(
        real_project: tuple[RunStore, str, Path]) -> None:
    from app.agents.base import RunRequest
    from app.bridge.coding_reuse import prepare_reused_artifact

    store, project, data = real_project
    workspaces: set[str] = set()
    outputs: set[str] = set()
    for index, (unit, count, seed) in enumerate((('steps', 7, 2027), ('epochs', 2, 2031))):
        selected = data.with_name(f'selected_{index}.json')
        values = [index + 1, index + 3, index + 6, index + 8]
        selected.write_text(json.dumps({'values': values}))
        run = store.create(task=f'current_measurement_{index}', project=project, entrypoint='coding',
            data_source={'id': f'current_{index}', 'stored_path': str(selected)})
        request = RunRequest(project=project, user_request='Execute existing configuration',
            extra={'invocation_id': uuid.uuid4().hex,
                   'existing_execution_job': _request(seed=seed, max_iters=count, budget_unit=unit)})
        with research_branch_scope(run, 'coding'):
            artifact = await prepare_reused_artifact(run, request)
            assert artifact is not None
            row = artifact.metadata['execution_jobs'][0]
            ref = ArtifactStore(run).write(text=artifact.text)
            ArtifactStore(run).approve(ref)
            result = await run_local_command(LocalCommandJob(run.run_id, row['name'], project, run.root,
                config=row['config'], seed=seed, steps=count, command_id=row['config']['command_id']))
            assert result.status == 'completed'
            assert result.metrics['mean'] == sum(values) / len(values)
            path = next((run.root / 'execution/local_commands').glob('*/*/execution_receipt.json'))
            receipt = json.loads(path.read_text())
            job = json.loads(path.with_name('job.json').read_text())
            assert receipt['status'] == 'completed' and receipt['returncode'] == 0
            assert job['seed'] == seed and job['steps'] == count
            assert job['config']['budget_unit'] == unit and job['config']['max_iters'] == count
            assert job['config']['data_path'] == str(selected) and job['config']['data_source_id'] == f'current_{index}'
            workspaces.add(job['code_workspace'])
            outputs.add(job['output_dir'])
    assert len(workspaces) == len(outputs) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['config', 'registration', 'unit_echo'])
async def test_real_preflight_cannot_accept_changed_inputs_or_wrong_budget_echo(
        real_project: tuple[RunStore, str, Path], change: str) -> None:
    from app.agents.base import RunRequest
    from app.bridge.coding_reuse import prepare_reused_artifact
    from app.harness.project_workspace import folder_project

    store, project, data = real_project
    folder = folder_project(project)
    assert folder is not None
    source = folder.root / 'code/ordinary_linear'
    runner = source / 'existing_runner.py'
    text = runner.read_text()
    if change == 'config':
        text = text.replace("    result = {'schema': 'local_command_validation.v1'",
            "    (code / 'configs/example.yaml').write_text('existing_model: changed_during_validation\\n')\n"
            "    result = {'schema': 'local_command_validation.v1'")
    elif change == 'registration':
        text = text.replace("    result = {'schema': 'local_command_validation.v1'",
            "    profile = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])\n"
            "    profile.write_text(profile.read_text().replace('ordinary_statistics', 'changed_statistics'))\n"
            "    result = {'schema': 'local_command_validation.v1'")
    else:
        text = text.replace("'budget_unit': request['config']['budget_unit']", "'budget_unit': 'epochs'")
    runner.write_text(text)
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Actual concurrent validation mutation')
    run = store.create(task='reject_changed_preflight', project=project, entrypoint='coding',
        data_source={'stored_path': str(data)})
    request = RunRequest(project=project, user_request='Execute current model',
        extra={'invocation_id': uuid.uuid4().hex, 'existing_execution_job': _request()})
    with research_branch_scope(run, 'coding'):
        assert await prepare_reused_artifact(run, request) is None
        receipt_path_value = next((run.root / 'execution/preflight').glob('*/validation_receipt.json'))
        receipt = json.loads(receipt_path_value.read_text())
        assert receipt['status'] == 'passed' and receipt['pid'] > 0
        assert not list((run.root / 'coding/reuse').glob('*.json'))
        assert not list((run.root / 'execution/local_commands').rglob('execution_receipt.json'))


def test_actual_archived_receipts_are_verified_and_changed_inputs_decline_reuse(
        record_property: Callable[[str, object], None]) -> None:
    configured = os.environ.get('MARS_FAST_REUSE_SOURCE_ROOT')
    if not configured:
        pytest.skip('requires an explicitly selected actual completed research run')
    root = Path(configured).resolve()
    metadata = json.loads((root / 'run_meta.json').read_text())
    source = RunHandle(metadata['run_id'], root, metadata['project'], metadata.get('task', ''),
                       metadata.get('entrypoint', 'coding'), metadata.get('created_at', ''))
    from app.harness.schema.frontmatter_parser import parse
    old = parse((root / 'coding/code_spec.approved.md').read_text()).metadata
    config = old['execution_jobs'][0]['config']
    requested = {key: config[key] for key in ('entrypoint', 'config_path', 'seed', 'budget_unit', 'max_iters')}
    before = _sha(root / 'coding/code_spec.approved.md')
    _, recipe, approved = _approved_recipe(source, requested)
    receipt_path_value, receipt, request = _completed_receipt(source, recipe)
    preflight_path, preflight = _verified_preflight(source, recipe, request)
    assert receipt['returncode'] == 0 and preflight['status'] == 'passed'
    assert receipt_path_value.is_file() and preflight_path.is_file()
    changes = [{'path': row['path'], 'expected_sha256': row['sha256'].removeprefix('sha256:'),
                'current_sha256': _sha(Path(row['path'])) if Path(row['path']).is_file() else 'missing'}
               for row in preflight['input_files']
               if not Path(row['path']).is_file() or _sha(Path(row['path'])) != row['sha256'].removeprefix('sha256:')]
    record_property('actual_archive_input_changes', json.dumps(changes, sort_keys=True))
    code_root = Path(request['code_workspace']).resolve()
    project_root = Path(request['project_workspace']).resolve()
    if changes:
        with pytest.raises((ReuseUnavailable, OSError)):
            _match_inputs(code_root, code_root, project_root, project_root, preflight['input_files'])
    else:
        matched = _match_inputs(code_root, code_root, project_root, project_root, preflight['input_files'])
        assert len(matched) == len(preflight['input_files'])
    assert _sha(approved) == before
