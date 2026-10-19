"""Actual registered checker processes validate delivery before artifact review."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.coding.agent import CodingAgent
from app.harness.project_workspace import open_folder
from app.harness.schema.frontmatter_parser import dumps
from app.harness.tools.execution.handoff import coding_handoff_errors, coding_handoff_preflight_errors
from app.harness.tools.git_branch import GitBranch, bind_git_branch, git
from app.settings import repo_root, reset_settings_cache


@dataclass(frozen=True)
class Delivery:
    project: str
    root: Path
    run_root: Path
    source: Path
    branch: GitBranch
    policy: Path

    @property
    def candidate(self) -> Path:
        return self.branch.root / 'configs/comparison/candidate.yaml'

    def metadata(self) -> dict[str, Any]:
        return {'schema': 'code_spec.v1', 'agent': 'coding', 'project': self.project,
            'target_lang': 'python', 'baseline_compat': {'preserved': True}, 'files_changed': [],
            'execution_jobs': [{'name': name, 'config': {
                'command_id': 'actual-loader', 'entrypoint': 'checker.py', 'seed': 2026,
                'budget_unit': 'epochs', 'max_iters': 2, 'config_path': path}}
                for name, path in [('baseline', 'configs/baseline.yaml'),
                                   ('candidate', 'configs/comparison/candidate.yaml')]]}


@pytest.fixture
def delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Delivery]:
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    monkeypatch.setenv('COLUMNS', '80')
    monkeypatch.setenv('LINES', '24')
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = tmp_path / 'source'
    (source / 'configs/comparison').mkdir(parents=True)
    (source / 'configs/base.yaml').write_text(yaml.safe_dump({
        'train_param': {'batch_size': 8}, 'optimizer_param': {'lr': 0.0004}}))
    (source / 'configs/baseline.yaml').write_text('model: established\n')
    # The real loader intentionally follows sibling-base semantics. This child
    # config does not inherit its parent's base, reproducing the missing key.
    (source / 'configs/comparison/candidate.yaml').write_text('model: changed\n')
    (source / 'checker.py').write_text(
        "import hashlib,json,os,pathlib,time,yaml\n"
        "request=json.loads(pathlib.Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
        "project=pathlib.Path(os.environ['MARS_PROJECT_ROOT'])\n"
        "with (project/'checker_calls.jsonl').open('a') as f: f.write(json.dumps({'pid':os.getpid(),'name':request['experiment_id']})+'\\n')\n"
        "assert os.environ.get('MARS_VALIDATE_ONLY')=='1', 'No training is permitted by this checker'\n"
        "code=pathlib.Path(os.environ['MARS_CODE_ROOT'])\n"
        "path=code/request['config']['config_path']\n"
        "base=path.with_name('base.yaml')\n"
        "inputs=[path,code/'checker.py']\n"
        "config=yaml.safe_load(base.read_text()) if base.is_file() else {}\n"
        "if base.is_file(): inputs.append(base)\n"
        "config.update(yaml.safe_load(path.read_text()))\n"
        "time.sleep(config.get('checker_sleep',0))\n"
        "batch=config['train_param']['batch_size']\n"
        "rate=config['optimizer_param']['lr']\n"
        "data=pathlib.Path(request['config']['data_path'])\n"
        "assert json.loads(data.read_text()), 'Actual dataset is empty'\n"
        "assert request['seed']==request['config']['seed']\n"
        "assert request['config']['budget_unit']=='epochs' and request['steps']==request['config']['max_iters']==2\n"
        "inputs.append(data)\n"
        "details={'training_started':False,'batch_size':batch,'learning_rate':rate,'seed':request['seed'],"
        "'budget_unit':request['config']['budget_unit'],'budget_value':request['steps'],"
        "'input_files':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs]}\n"
        "result={'schema':'local_command_validation.v1','status':'ready','invocation_id':request['invocation_id'],"
        "'run_id':request['run_id'],'experiment_id':request['experiment_id'],"
        "'checks':[{'name':'actual_native_loader','ready':True,'details':details}]}\n"
        "pathlib.Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps(result))\n")
    git(source, 'init', '-b', 'baseline')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c', 'commit.gpgsign=false',
        'commit', '-m', 'Actual loader and scientific input fixture')
    commit = git(source, 'rev-parse', 'HEAD')
    code = project.root / 'code/isolated-study'
    git(project.root, 'clone', '--no-local', str(source), str(code))
    git(code, 'switch', '-c', 'study')
    branch = GitBranch(project.name, 'delivery', str(code), 'study', 'baseline', commit,
                       source_repo_path=str(source), initial_commit=commit)
    run_root = project.root / 'runs/delivery'
    (run_root / 'input').mkdir(parents=True)
    data = project.root / 'actual-data.json'
    data.write_text('[1,2,3]')
    (run_root / 'input/selected_data_source.json').write_text(json.dumps({
        'project': project.name, 'id': 'frozen-data', 'stored_path': str(data),
        'fs_mhz': 245.76, 'channel_count': 16}))
    policy = tmp_path / 'execution.yaml'
    argv = [sys.executable, str(code / 'checker.py')]
    policy.write_text(yaml.safe_dump({'execution': {'backend': 'local_command',
        'command_timeout_seconds': 10, 'local_commands': [{'id': 'actual-loader', 'argv': argv,
            'project_ids': [project.name], 'entrypoints': ['checker.py'], 'config_paths': ['configs/*.yaml'],
            'consumes_data_path': True, 'validation': {'environment': {'MARS_VALIDATE_ONLY': '1'},
            'timeout_seconds': 2, 'max_output_bytes': 65536}}]}}))
    tool_config = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
    tool_config['tools']['execution.simulation_runner']['command_allowlist'] = [argv]
    tools = tmp_path / 'tools.yaml'
    tools.write_text(yaml.safe_dump(tool_config))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(policy))
    monkeypatch.setenv('MARS_TOOLS_CONFIG_PATH', str(tools))
    reset_settings_cache()
    try:
        yield Delivery(project.name, project.root, run_root, source, branch, policy)
    finally:
        reset_settings_cache()


async def check(delivery: Delivery, metadata: dict[str, Any] | None = None) -> list[str]:
    with bind_git_branch(delivery.branch):
        return await coding_handoff_preflight_errors('', metadata or delivery.metadata(),
            project=delivery.project, run_id='delivery', run_root=delivery.run_root)


def summaries(delivery: Delivery) -> list[dict[str, Any]]:
    paths = sorted((delivery.run_root / 'coding/delivery_preflight').glob('*.json'),
                   key=lambda path: path.stat().st_mtime_ns)
    return [json.loads(path.read_text()) for path in paths]


def fix_candidate(delivery: Delivery) -> None:
    delivery.candidate.write_text(yaml.safe_dump({'model': 'changed',
        'train_param': {'batch_size': 8}, 'optimizer_param': {'lr': 0.0002}}))


def assert_unapproved_no_training(delivery: Delivery) -> None:
    assert not (delivery.run_root / 'execution/local_commands').exists()
    assert not (delivery.run_root / 'execution/jobs').exists()
    assert not list(delivery.run_root.rglob('*.approved.md'))
    assert not list(delivery.run_root.rglob('*confirmation*.json'))
    assert not (delivery.run_root / 'state.json').exists()


@pytest.mark.asyncio
async def test_real_loader_failure_returns_exact_stderr_and_original_receipt_before_review(delivery: Delivery) -> None:
    original = delivery.metadata()
    with bind_git_branch(delivery.branch):
        assert coding_handoff_errors('', original, project=delivery.project) == []
    errors = await check(delivery, original)
    assert len(errors) == 1 and '/execution_jobs/1/config' in errors[0]
    assert "KeyError: 'train_param'" in errors[0] and 'validation_receipt.json' in errors[0]
    summary = summaries(delivery)[0]
    assert summary['status'] == 'failed' and not summary['training_started'] and not summary['approved']
    assert summary['jobs'][0]['status'] == 'passed'
    failed = summary['jobs'][1]
    receipt_path = delivery.run_root / failed['receipt_ref']
    assert failed['receipt_sha256'] == 'sha256:' + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    receipt = json.loads(receipt_path.read_text())
    assert receipt['returncode'] == 1 and receipt['pid'] > 0
    assert "KeyError: 'train_param'" in Path(receipt['stderr_path']).read_text()
    assert original == delivery.metadata()  # Host validation never patches metadata or source defaults.
    assert git(delivery.source, 'status', '--porcelain') == ''
    assert_unapproved_no_training(delivery)


@pytest.mark.asyncio
async def test_agent_validator_feeds_real_failure_back_then_accepts_actual_fix(delivery: Delivery) -> None:
    request = RunRequest(project=delivery.project, user_request='Compare these two actual configurations',
        extra={'run_id': 'delivery', 'run_root': str(delivery.run_root), 'attempt': 1,
               'execution_delivery_required': True})
    text = dumps(delivery.metadata(), 'Delivery submitted for validation, not an approved experiment.')
    with bind_git_branch(delivery.branch):
        agent = CodingAgent()
        errors = await agent.validate_candidate(request, text, [])
        assert any("KeyError: 'train_param'" in error for error in errors)
        fix_candidate(delivery)
        assert await agent.validate_candidate(request, text, []) == []
    assert [row['status'] for row in summaries(delivery)] == ['failed', 'passed']
    assert_unapproved_no_training(delivery)


@pytest.mark.asyncio
async def test_fixed_inputs_budget_seed_and_current_dataset_reach_real_checker(delivery: Delivery) -> None:
    fix_candidate(delivery)
    metadata = delivery.metadata()
    before = deepcopy(metadata)
    assert await check(delivery, metadata) == []
    for row in summaries(delivery)[0]['jobs']:
        path = delivery.run_root / row['receipt_ref']
        receipt = json.loads(path.read_text())
        request = json.loads(path.with_name('job.json').read_text())
        details = receipt['checks'][0]['details']
        assert details['training_started'] is False
        assert (details['budget_unit'], details['budget_value'], details['seed']) == ('epochs', 2, 2026)
        assert request['config']['data_source_id'] == 'frozen-data'
        assert request['data_binding']['path'] == str(delivery.root / 'actual-data.json')
        assert request['code_workspace'] == str(delivery.branch.root)
        assert request['config']['channel_count'] == 16 and request['config']['fs_mhz'] == 245.76
    assert metadata == before
    assert_unapproved_no_training(delivery)


@pytest.mark.asyncio
async def test_actual_source_change_invalidates_prior_pass_and_preserves_receipts(delivery: Delivery) -> None:
    fix_candidate(delivery)
    # A clean real Git checkout can reuse a genuine checker receipt. Altering
    # scientific input must invalidate it even with the same delivery metadata.
    git(delivery.branch.root, 'add', '.')
    git(delivery.branch.root, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Self contained candidate')
    assert await check(delivery) == []
    first = summaries(delivery)[0]
    originals = {row['receipt_ref']: (delivery.run_root / row['receipt_ref']).read_bytes() for row in first['jobs']}
    delivery.candidate.write_text('model: incomplete-again\n')
    errors = await check(delivery)
    assert any("KeyError: 'train_param'" in error for error in errors)
    latest = summaries(delivery)[-1]
    assert latest['jobs'][1]['receipt_ref'] != first['jobs'][1]['receipt_ref']
    assert not latest['jobs'][1]['reused']
    assert all((delivery.run_root / name).read_bytes() == value for name, value in originals.items())
    assert git(delivery.source, 'status', '--porcelain') == ''
    assert_unapproved_no_training(delivery)


@pytest.mark.asyncio
async def test_actual_dataset_change_invalidates_prior_pass(delivery: Delivery) -> None:
    fix_candidate(delivery)
    assert await check(delivery) == []
    (delivery.root / 'actual-data.json').write_text('[]')
    errors = await check(delivery)
    assert len(errors) == 2 and all('Actual dataset is empty' in error for error in errors)
    assert summaries(delivery)[-1]['status'] == 'failed'
    assert_unapproved_no_training(delivery)


@pytest.mark.asyncio
async def test_no_registered_checker_retains_static_admission_without_claiming_pass(delivery: Delivery) -> None:
    policy = yaml.safe_load(delivery.policy.read_text())
    del policy['execution']['local_commands'][0]['validation']
    delivery.policy.write_text(yaml.safe_dump(policy))
    # A command without no-training capability does not acquire a new dataset
    # loading requirement merely because Coding's static validator ran.
    (delivery.run_root / 'input/selected_data_source.json').write_text('not JSON')
    reset_settings_cache()
    assert await check(delivery) == []
    summary = summaries(delivery)[0]
    assert summary['status'] == 'unverified' and all(not row['configured'] for row in summary['jobs'])
    assert not (delivery.root / 'checker_calls.jsonl').exists()
    with bind_git_branch(delivery.branch):
        assert await coding_handoff_preflight_errors('', delivery.metadata(), project=delivery.project,
            run_id='', run_root=Path('.')) == []
    assert len(summaries(delivery)) == 1 and not (delivery.run_root / 'execution/preflight').exists()


@pytest.mark.asyncio
async def test_registered_checker_rejects_missing_real_task_context_without_spawning(delivery: Delivery) -> None:
    with bind_git_branch(delivery.branch):
        errors = await coding_handoff_preflight_errors('', delivery.metadata(), project=delivery.project,
            run_id='', run_root=Path('.'))
    assert len(errors) == 1 and 'run_id、run_root 和 attempt' in errors[0]
    assert not (delivery.root / 'checker_calls.jsonl').exists()
    assert not (delivery.run_root / 'execution/preflight').exists()


@pytest.mark.asyncio
async def test_cancellation_waits_for_registered_timeout_and_leaves_no_checker_process(delivery: Delivery) -> None:
    # The checker timeout is the registered policy, not a new Coding time limit.
    path = delivery.branch.root / 'configs/baseline.yaml'
    path.write_text('model: established\nchecker_sleep: 30\n')
    with bind_git_branch(delivery.branch):
        task = asyncio.create_task(coding_handoff_preflight_errors('', delivery.metadata(), project=delivery.project,
            run_id='delivery', run_root=delivery.run_root))
        deadline = time.monotonic() + 5
        calls = delivery.root / 'checker_calls.jsonl'
        while not calls.exists():
            assert time.monotonic() < deadline, 'Actual checker did not start'
            await asyncio.sleep(0.02)
        started = time.monotonic()
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), 'Cancellation released the task capability while its checker was still running'
        task.cancel()  # Repeated user stop must still wait for bounded cleanup.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=4)
        assert time.monotonic() - started < 4
    pid = json.loads(calls.read_text().splitlines()[0])['pid']
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    receipts = list((delivery.run_root / 'execution/preflight').glob('*/validation_receipt.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt['status'] == 'failed' and receipt['returncode'] != 0 and 'timed out' in receipt['error']
    assert not list((delivery.run_root / 'coding/delivery_preflight').glob('*.json'))
    assert_unapproved_no_training(delivery)
