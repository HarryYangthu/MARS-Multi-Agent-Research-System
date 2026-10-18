"""Real Git, checker processes and measurements verify preflight reuse boundaries."""
from __future__ import annotations

from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path
from typing import Any
import uuid

import pytest
import yaml

from app.agents.base import RunRequest
from app.bridge.coding_reuse import prepare_reused_artifact
from app.bridge.execution_batch_plan import prepare_execution
from app.bridge.execution_confirmation import execution_preview, save_confirmation
from app.bridge.research_branch import research_branch_scope
from app.harness.tools.execution.command_preflight import preflight_state, run_command_preflight
from app.harness.tools.execution.local_command import LocalCommandJob, run_local_command
from app.harness.tools.execution.preflight_reuse import input_records_unchanged, preflight_fingerprint
from app.harness.tools.git_branch import git
from app.harness.schema.experiment_contract import document_hash
from app.harness.tools.project_repo import load_project_repo
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RunStore
from tests.unit.test_coding_reuse_evidence import _environment, _request, real_project


def _instrument(real: tuple[RunStore, str, Path]) -> None:
    _, project, _ = real
    source = load_project_repo(project).root
    runner = source / 'existing_runner.py'
    runner.write_text(runner.read_text().replace("if os.environ.get('MARS_VALIDATE_ONLY') == '1':",
        "if os.environ.get('MARS_VALIDATE_ONLY') == '1':\n"
        "    with (Path(request['project_workspace']) / 'checker_processes.jsonl').open('a') as audit:\n"
        "        audit.write(json.dumps({'pid': os.getpid(), 'invocation_id': request['invocation_id']})+'\\n')"))
    git(source, 'add', 'existing_runner.py')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c', 'commit.gpgsign=false',
        'commit', '-m', 'Record actual checker processes')


def _job(run: RunHandle, data: Path, **changes: Any) -> LocalCommandJob:
    config = {**_request(max_iters=7, seed=2031), 'command_id': 'ordinary_statistics',
              'data_path': str(data), 'attempt': 1, 'label': 'existing_statistics', **changes}
    return LocalCommandJob(run.run_id, 'existing_statistics', run.project, run.root,
        config=config, seed=config['seed'], steps=config['max_iters'], command_id=config['command_id'])


def _checker_count(data: Path) -> int:
    path = data.parent.parent / 'checker_processes.jsonl'
    return len(path.read_text().splitlines()) if path.exists() else 0


@pytest.mark.asyncio
async def test_current_preparation_confirmation_and_real_execution_use_one_checker_process(
        real_project: tuple[RunStore, str, Path]) -> None:
    _instrument(real_project)
    store, project, data = real_project
    run = store.create(task='one_real_check', project=project, entrypoint='coding',
        data_source={'id': 'current_values', 'stored_path': str(data)})
    request = RunRequest(project=project, user_request='Run existing for 7 steps seed 2031',
        extra={'invocation_id': uuid.uuid4().hex, 'attempt': 1,
               'existing_execution_job': _request(max_iters=7, seed=2031)})
    with research_branch_scope(run, 'coding'):
        artifact = await prepare_reused_artifact(run, request)
        assert artifact is not None
        assert artifact.metadata['execution_jobs'][0]['config']['attempt'] == 1
        artifacts = ArtifactStore(run)
        artifacts.approve(artifacts.write(text=artifact.text))
        # Deliberate human-authored fixture approvals, not a fabricated model
        # review. The real production managed review remains independent.
        artifacts.approve(artifacts.write_metadata(metadata={
            'schema': 'run_log.v1', 'project': project, 'agent': 'execution', 'run_id': run.run_id,
            'status': 'interrupted', 'metrics': {'planned_experiments': 1}, 'fingerprint_hash': 'sha256:00',
            'coding_spec_sha256': document_hash(artifact.text),
            'planned_experiments': artifact.metadata['execution_jobs']}, body='Approved actual statistics job.'))
        original = {path: path.read_bytes() for path in (run.root / 'execution/preflight').glob('*/*.json')}
        assert _checker_count(data) == 1
        view = execution_preview(run, 'execution')
        saved = save_confirmation(run, 'execution', view['token'])
        assert saved['confirmed'] and _checker_count(data) == 1
        spec = prepare_execution(run, 'execution').specs[0]
        actual = await run_local_command(LocalCommandJob(run.run_id, spec.experiment_id, project, run.root,
            config={**spec.config, 'confirmation_token': view['token']}, seed=spec.seed,
            steps=7, command_id=spec.config['command_id']))
        assert actual.status == 'completed'
        assert actual.metrics == {'mean': 4.5, 'variance': 7.25, 'count': 4.0}
        assert _checker_count(data) == 1
        assert len(list((run.root / 'execution/preflight').glob('*/validation_receipt.json'))) == 1
        derived = list((run.root / 'execution/preflight/derivations').glob('*.json'))
        assert len(derived) == 2
        assert all(json.loads(path.read_text())['checker_started'] is False for path in derived)
        assert all(path.read_bytes() == content for path, content in original.items())


@pytest.mark.parametrize('change', ['seed', 'budget', 'unit', 'extra_config', 'attempt', 'data',
                                  'restored_mtime', 'driver', 'unlisted_source', 'registration', 'environment'])
def test_changed_complete_identity_or_actual_input_runs_a_new_checker(
        real_project: tuple[RunStore, str, Path], change: str) -> None:
    _instrument(real_project)
    store, project, data = real_project
    run = store.create(task='changed_actual_inputs', project=project, entrypoint='coding')
    with research_branch_scope(run, 'coding'):
        job = _job(run, data)
        first = run_command_preflight(job, 'coding')
        assert first['status'] == 'passed' and _checker_count(data) == 1
        context: dict[str, str] = {}
        if change in {'seed', 'budget', 'unit', 'extra_config', 'attempt'}:
            changes: dict[str, dict[str, Any]] = {'seed': {'seed': 2032}, 'budget': {'max_iters': 8},
                'unit': {'budget_unit': 'epochs'}, 'extra_config': {'scale': 2}, 'attempt': {'attempt': 2}}
            job = _job(run, data, **changes[change])
        elif change in {'data', 'restored_mtime'}:
            before = data.stat()
            data.write_text(json.dumps({'values': [2, 4, 7, 9]}))
            if change == 'restored_mtime':
                os.utime(data, ns=(before.st_atime_ns, before.st_mtime_ns))
            assert not input_records_unchanged(first['input_files'])
        elif change in {'driver', 'unlisted_source'}:
            from app.harness.tools.git_branch import current_git_branch
            branch = current_git_branch(project, run.run_id)
            assert branch is not None
            runner = branch.root / ('existing_runner.py' if change == 'driver' else 'unlisted_module.py')
            runner.write_text((runner.read_text() if runner.exists() else '') + '\n# Actual source changed\n')
        elif change == 'registration':
            path = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])
            registration = yaml.safe_load(path.read_text())
            registration['execution']['local_commands'][0]['label'] = 'Different host registration'
            path.write_text(yaml.safe_dump(registration))
        elif change == 'environment':
            context['MARS_CHECKER_RUNTIME_OPTION'] = 'different-real-child-environment'
        with _environment(context):
            second = run_command_preflight(job, 'confirmation')
        assert second['status'] == 'passed' and not second.get('reused', False)
        assert _checker_count(data) == 2


def test_two_actual_task_scopes_each_run_their_own_checker(
        real_project: tuple[RunStore, str, Path]) -> None:
    _instrument(real_project)
    store, project, data = real_project
    receipts: list[str] = []
    for index in range(2):
        run = store.create(task=f'independent_{index}', project=project, entrypoint='coding')
        with research_branch_scope(run, 'coding'):
            checked = run_command_preflight(_job(run, data), 'coding')
            assert checked['status'] == 'passed' and not checked.get('reused', False)
            receipts.append(checked['receipt_path'])
        assert _checker_count(data) == index + 1
    assert receipts[0] != receipts[1]


@pytest.mark.parametrize('target', ['receipt', 'request', 'result', 'derivation'])
def test_tampered_genuine_evidence_never_becomes_a_reusable_success(
        real_project: tuple[RunStore, str, Path], target: str) -> None:
    _instrument(real_project)
    store, project, data = real_project
    run = store.create(task='tamper_actual_evidence', project=project, entrypoint='coding')
    with research_branch_scope(run, 'coding'):
        job = _job(run, data)
        first = run_command_preflight(job, 'coding')
        second = run_command_preflight(job, 'confirmation')
        assert second.get('reused') is True and _checker_count(data) == 1
        path = (Path(second['derivation_path']) if target == 'derivation' else
                Path(first['receipt_path']).with_name({'receipt': 'validation_receipt.json',
                    'request': 'job.json', 'result': 'result.json'}[target]))
        path.write_text(path.read_text() + '\n')
        with pytest.raises(ValueError, match='changed'):
            preflight_state(job, 'confirmation')
        assert _checker_count(data) == 1


def test_legacy_receipt_has_no_reuse_identity_and_is_not_upgraded(
        real_project: tuple[RunStore, str, Path]) -> None:
    _instrument(real_project)
    store, project, data = real_project
    run = store.create(task='old_receipt', project=project, entrypoint='coding')
    with research_branch_scope(run, 'coding'):
        job = _job(run, data)
        old_job = replace(job, config={key: value for key, value in job.config.items() if key != 'attempt'})
        first = run_command_preflight(old_job, 'old')
        original = Path(first['receipt_path']).read_bytes()
        assert preflight_fingerprint(old_job) is None
        assert 'reuse_fingerprint' not in json.loads(original)
        second = run_command_preflight(job, 'new')
        assert second['status'] == 'passed' and not second.get('reused', False)
        assert _checker_count(data) == 2 and Path(first['receipt_path']).read_bytes() == original


def test_environment_change_after_a_derivation_is_stale_and_rechecked_not_mistaken_for_tampering(
        real_project: tuple[RunStore, str, Path]) -> None:
    _instrument(real_project)
    store, project, data = real_project
    run = store.create(task='changed_derived_environment', project=project, entrypoint='coding')
    with research_branch_scope(run, 'coding'):
        job = _job(run, data)
        assert run_command_preflight(job, 'coding')['status'] == 'passed'
        derived = run_command_preflight(job, 'confirmation')
        original = Path(derived['derivation_path']).read_bytes()
        assert derived.get('reused') is True and _checker_count(data) == 1
        with _environment({'MARS_CHECKER_RUNTIME_OPTION': 'changed-after-confirmation'}):
            assert preflight_state(job, 'confirmation')['status'] == 'stale'
            fresh = run_command_preflight(job, 'new-confirmation')
            assert fresh['status'] == 'passed' and not fresh.get('reused', False)
        assert _checker_count(data) == 2 and Path(derived['derivation_path']).read_bytes() == original


def test_legacy_signatures_use_the_conservative_content_check_even_with_restored_mtime(tmp_path: Path) -> None:
    from app.harness.tools.execution.command_preflight import _input_signature
    path = tmp_path / 'actual_values.json'
    path.write_text('[1,2,3]')
    stat = path.stat()
    row = {key: value for key, value in _input_signature(path).items() if key != 'ctime_ns'}
    row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert input_records_unchanged([row])
    path.write_text('[4,5,6]')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert not input_records_unchanged([row])


@pytest.mark.parametrize('directory', ['reuse_index', 'derivations'])
def test_reuse_metadata_cannot_be_written_through_a_symlink(
        real_project: tuple[RunStore, str, Path], tmp_path: Path, directory: str) -> None:
    _instrument(real_project)
    store, project, data = real_project
    run = store.create(task='no_metadata_escape', project=project, entrypoint='coding')
    with research_branch_scope(run, 'coding'):
        job = _job(run, data)
        if directory == 'derivations':
            assert run_command_preflight(job, 'coding')['status'] == 'passed'
        outside = tmp_path / 'outside_metadata'
        outside.mkdir()
        target = run.root / 'execution/preflight' / directory
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match='symlink'):
            run_command_preflight(job, 'confirmation')
        assert not list(outside.iterdir())
        assert _checker_count(data) == (1 if directory == 'derivations' else 0)
