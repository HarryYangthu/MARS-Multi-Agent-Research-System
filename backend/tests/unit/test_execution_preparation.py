"""Real background checks, durable progress and exact confirmation ownership."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI, HTTPException

from app.api import dependencies
from app.api.runs import (ExecutionConfirmationPayload, ExecutionPreparationPayload,
    confirm_execution_configuration, get_execution_preparation, prepare_execution_configuration, router)
from app.bridge.agent_registry import AgentRegistry
from app.bridge.execution_confirmation import confirmation_policy
from app.bridge.execution_preparation import ExecutionPreparations
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps
from app.settings import repo_root, reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    before = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    reset_settings_cache()
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


@pytest.fixture
async def actual_batch(tmp_path: Path) -> AsyncIterator[tuple[Orchestrator, RunSession, Path]]:
    config, tools, review = (tmp_path / name for name in ('execution.yaml', 'tools.yaml', 'review.yaml'))
    with _environment({'MARS_FOLDER_PROJECTS_REGISTRY': str(tmp_path / 'registry.json'),
        'MARS_EXECUTION_BACKEND': 'local_command', 'MARS_EXECUTION_CONFIG_PATH': str(config),
        'MARS_TOOLS_CONFIG_PATH': str(tools), 'MARS_EXECUTION_REVIEW_CONFIG_PATH': str(review),
        'COLUMNS': '80', 'LINES': '24'}):
        from app.harness.project_workspace import open_folder
        project = open_folder(str(tmp_path / 'project'), create=True)
        checker = tmp_path / 'actual_check_and_measure.py'
        checker.write_text(
            "import hashlib,json,os,pathlib,time\n"
            "r=json.loads(pathlib.Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
            "pathlib.Path(r['output_dir'],'checker-started.json').write_text(json.dumps({'pid':os.getpid()}))\n"
            "p=pathlib.Path(r['config']['data_path']); data=json.loads(p.read_text())\n"
            "assert str(p)==os.environ['MARS_DATA_PATH'] and r['seed']==2026 and r['steps']==2\n"
            "if os.environ.get('MARS_VALIDATE_ONLY')=='1':\n"
            " time.sleep(r['config'].get('check_delay',0))\n"
            " assert not r['config'].get('invalid',False), 'actual invalid configuration'\n"
            " result={'schema':'local_command_validation.v1','status':'ready','invocation_id':r['invocation_id'],"
            "'run_id':r['run_id'],'experiment_id':r['experiment_id'],'checks':[{'name':'actual_data',"
            "'ready':True,'details':{'input_files':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}}]}\n"
            "else:\n"
            " mean=sum(data)/len(data)\n"
            " pathlib.Path('actual-mean.json').write_text(json.dumps({'mean':mean,'samples':data}))\n"
            " result={'schema':'local_command_result.v1','status':'completed','invocation_id':r['invocation_id'],"
            "'run_id':r['run_id'],'experiment_id':r['experiment_id'],'metrics':{'mean':mean},'evidence_paths':['actual-mean.json']}\n"
            "pathlib.Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps(result))\n", encoding='utf-8')
        command = {'id': 'actual', 'argv': [sys.executable, str(checker)], 'project_ids': [project.name],
            'consumes_data_path': True, 'required_metrics': ['mean'],
            'validation': {'environment': {'MARS_VALIDATE_ONLY': '1'}, 'timeout_seconds': 3, 'max_output_bytes': 65536}}
        config.write_text(yaml.safe_dump({'execution': {'backend': 'local_command', 'max_concurrency': 1,
            'batch_steps': 2, 'command_timeout_seconds': 5, 'local_commands': [command]}}))
        raw_tools = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
        for name in ('execution.simulation_runner', 'execution.batch_runner'):
            raw_tools['tools'][name]['command_allowlist'] = [[sys.executable, str(checker)]]
        tools.write_text(yaml.safe_dump(raw_tools))
        review.write_text(yaml.safe_dump({'poll_interval_seconds': 0.02, 'max_source_bytes': 2000000,
                                          'preflight_total_timeout_seconds': 0.3}))
        reset_settings_cache()
        store = RunStore(tmp_path / 'runs')
        orch = Orchestrator(run_store=store, registry=AgentRegistry())
        session = orch.create_session(RunRequest(task='actual_batch', project=project.name,
            entrypoint='execution', standalone=True, auto_approve=False, extra={'stop_after': 'execution'}))
        data = project.root / 'samples.json'
        data.write_text('[1,2,6]')
        _plan(session, data, count=2)
        for state in (NodeState.RUNNING, NodeState.WAITING_REVIEW, NodeState.APPROVED):
            await orch._transition(session, 'execution', state)
        previous = dependencies._run_store, dependencies._orchestrator
        dependencies._run_store, dependencies._orchestrator = store, orch
        try:
            yield orch, session, data
        finally:
            await orch.shutdown_owned_runs()
            dependencies._run_store, dependencies._orchestrator = previous
            from app.bridge.tensorboard_service import shutdown_tensorboard
            await shutdown_tensorboard()


def _plan(session: RunSession, data: Path, *, count: int, delay: float = 0, invalid: int = -1) -> None:
    rows = [{'name': f'group_{index}', 'config': {'command_id': 'actual', 'seed': 2026,
        'budget_steps': 2, 'data_path': str(data), 'check_delay': delay, 'invalid': index == invalid}}
        for index in range(count)]
    store = ArtifactStore(session.run)
    store.approve(store.write(text=dumps({'schema': 'run_log.v1', 'project': session.run.project,
        'agent': 'execution', 'run_id': session.run.run_id, 'status': 'interrupted',
        'metrics': {'planned_experiments': count}, 'fingerprint_hash': 'sha256:00',
        'planned_experiments': rows}, 'Human-authored exact numerical batch.')))


async def _finished(orch: Orchestrator, session: RunSession) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = get_execution_preparation(session.run.run_id, session.run.project)
        if state['status'] != 'preparing':
            return state
        await asyncio.sleep(0.02)
    raise AssertionError('Actual temporary batch did not finish')


@pytest.mark.asyncio
async def test_twenty_real_checks_exceed_legacy_http_budget_but_finish_independently(
    actual_batch: tuple[Orchestrator, RunSession, Path],
) -> None:
    orch, session, data = actual_batch
    _plan(session, data, count=20, delay=0.2)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://actual') as client:
        initial = await client.get(f'/api/runs/{session.run.run_id}/execution-configuration/preparation',
                                   params={'project': session.run.project})
        assert initial.json()['status'] == 'unprepared'
        started = time.monotonic()
        response = await client.post(f'/api/runs/{session.run.run_id}/execution-configuration/prepare',
                                    json={'project': session.run.project})
        assert response.status_code == 202 and time.monotonic() - started < 2
        operation = response.json()['operation_id']
        # Closing the actual ASGI client does not cancel the independently owned task.
    joined = await prepare_execution_configuration(session.run.run_id, ExecutionPreparationPayload(project=session.run.project))
    assert joined['operation_id'] == operation
    assert session.graph.state('execution') == NodeState.APPROVED
    state = await _finished(orch, session)
    assert state['status'] == 'ready', state
    assert state['completed'] == state['total'] == 20
    receipts = list((session.run.root / 'execution/preflight').glob('*/validation_receipt.json'))
    assert len(receipts) == 20
    rows = [json.loads(path.read_text()) for path in receipts]
    assert {row['experiment_id'] for row in rows} == {f'group_{index}' for index in range(20)}
    assert all(row['status'] == 'passed' and row['returncode'] == 0 and row['pid'] > 0 for row in rows)
    assert sum(row['duration_seconds'] for row in rows) > confirmation_policy()['preflight_total_timeout_seconds']
    before = {path: path.read_bytes() for path in session.run.root.rglob('*') if path.is_file()}
    for _ in range(3):
        assert get_execution_preparation(session.run.run_id, session.run.project)['status'] == 'ready'
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (session.run.root / 'execution/confirmations').exists()
    assert not (session.run.root / 'execution/local_commands').exists()
    assert ExecutionPreparations().status(session.run, 'execution')['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_failed_group_is_real_and_exact_successes_are_retained_on_retry(
    actual_batch: tuple[Orchestrator, RunSession, Path],
) -> None:
    orch, session, data = actual_batch
    _plan(session, data, count=5, invalid=3)
    await orch.execution_preparations.start(session.run, 'execution')
    first = await _finished(orch, session)
    assert first['status'] == 'failed'
    assert [row['status'] for row in first['validation']] == ['passed', 'passed', 'passed', 'failed', 'required']
    original = {path: path.read_bytes() for path in (session.run.root / 'execution/preflight').glob('*/validation_receipt.json')}
    _plan(session, data, count=5)
    await orch.execution_preparations.start(session.run, 'execution')
    second = await _finished(orch, session)
    assert second['status'] == 'ready', second
    assert len(list((session.run.root / 'execution/preflight').glob('*/validation_receipt.json'))) == 6
    assert all(path.read_bytes() == content for path, content in original.items())
    data.write_text('[2,3,7]')
    assert get_execution_preparation(session.run.run_id, session.run.project)['status'] == 'stale'
    with pytest.raises(HTTPException):
        await confirm_execution_configuration(session.run.run_id,
            ExecutionConfirmationPayload(project=session.run.project, token=second['token']))
    assert not (session.run.root / 'execution/local_commands').exists()


@pytest.mark.asyncio
async def test_actual_confirmation_duplicate_and_saved_receipt_resume_same_numerical_batch(
    actual_batch: tuple[Orchestrator, RunSession, Path],
) -> None:
    orch, session, _ = actual_batch
    await orch.execution_preparations.wait(session.run, 'execution')
    state = get_execution_preparation(session.run.run_id, session.run.project)
    assert state['status'] == 'ready', state
    # A real durable confirmation written before coordinator scheduling models
    # the crash window without a fake resume function or a successful provider.
    from app.bridge.execution_confirmation import save_prepared_confirmation
    save_prepared_confirmation(session.run, 'execution', state['token'], state['configuration'])
    receipt = session.run.root / 'execution/confirmations' / (state['token'] + '.json')
    original = receipt.read_bytes()
    payload = ExecutionConfirmationPayload(project=session.run.project, token=state['token'])
    results = await asyncio.gather(*(confirm_execution_configuration(session.run.run_id, payload) for _ in range(3)))
    assert all(row['status'] == 'confirming' for row in results)
    deadline = time.monotonic() + 30
    while session.graph.state('execution') != NodeState.DONE and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert session.graph.state('execution') == NodeState.DONE
    final = get_execution_preparation(session.run.run_id, session.run.project)
    assert final['confirmed'] and final['launch_state'] == 'done'
    assert len(final['configuration']['jobs']) == 2
    assert all(row['status'] == 'completed' and row['metrics']['mean'] == 3 for row in final['configuration']['jobs'])
    assert len(list((session.run.root / 'execution/local_commands').glob('*/*/execution_receipt.json'))) == 2
    assert receipt.read_bytes() == original
    assert all(key != 'writing' or value == NodeState.PENDING for key, value in session.graph.all_states().items())
    recovered = Orchestrator(run_store=orch.run_store, registry=AgentRegistry())
    previous = dependencies._orchestrator
    dependencies._orchestrator = recovered
    try:
        historical = get_execution_preparation(session.run.run_id, session.run.project)
        assert historical['confirmed'] and historical['launch_state'] == 'done'
        assert len(historical['configuration']['jobs']) == 2
        (session.run.root / 'execution/preparations/current.json').unlink()
        without_preparation = get_execution_preparation(session.run.run_id, session.run.project)
        assert without_preparation['confirmed'] and without_preparation['launch_state'] == 'done'
        assert len(without_preparation['configuration']['jobs']) == 2
        assert all(key in without_preparation['configuration'] for key in (
            'experiments', 'source_configs', 'blockers', 'warnings', 'defaults', 'budget'))
    finally:
        dependencies._orchestrator = previous


@pytest.mark.asyncio
async def test_preparation_shutdown_records_interruption_without_confirming_or_training(
    actual_batch: tuple[Orchestrator, RunSession, Path],
) -> None:
    orch, session, data = actual_batch
    _plan(session, data, count=5, delay=2)
    await orch.execution_preparations.start(session.run, 'execution')
    deadline = time.monotonic() + 10
    while not list((session.run.root / 'execution/preflight').glob('*/checker-started.json')) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    await orch.execution_preparations.shutdown()
    state = get_execution_preparation(session.run.run_id, session.run.project)
    assert state['status'] == 'interrupted'
    assert not (session.run.root / 'execution/confirmations').exists()
    assert not (session.run.root / 'execution/local_commands').exists()
    receipts = [json.loads(path.read_text()) for path in (session.run.root / 'execution/preflight').glob('*/validation_receipt.json')]
    assert len(receipts) == 1 and receipts[0]['status'] == 'failed'
    assert type(receipts[0]['returncode']) is int and receipts[0]['returncode'] != 0


@pytest.mark.asyncio
async def test_early_confirmation_and_cross_task_tokens_are_rejected_without_waiting(
    actual_batch: tuple[Orchestrator, RunSession, Path],
) -> None:
    orch, session, data = actual_batch
    _plan(session, data, count=3, delay=0.2)
    response = await prepare_execution_configuration(session.run.run_id, ExecutionPreparationPayload(project=session.run.project))
    started = time.monotonic()
    with pytest.raises(HTTPException):
        await confirm_execution_configuration(session.run.run_id,
            ExecutionConfirmationPayload(project=session.run.project, token='0' * 64))
    assert time.monotonic() - started < 2
    with pytest.raises(HTTPException):
        get_execution_preparation(session.run.run_id, 'other-project')
    with pytest.raises(HTTPException):
        get_execution_preparation(session.run.run_id, session.run.project, 'not-this-operation')
    assert response['status'] == 'preparing'
    assert not (session.run.root / 'execution/confirmations').exists()
    assert not (session.run.root / 'execution/local_commands').exists()
    await orch.execution_preparations.cancel(session.run.run_id)


@pytest.mark.asyncio
async def test_host_configuration_symlink_alias_is_tracked_without_changing_its_policy(
    actual_batch: tuple[Orchestrator, RunSession, Path], tmp_path: Path,
) -> None:
    if os.name == 'nt':
        pytest.skip('Creating unprivileged symbolic links is not portable on Windows')
    orch, session, _ = actual_batch
    original = os.environ['MARS_EXECUTION_CONFIG_PATH']
    alias = tmp_path / 'execution-alias.yaml'
    alias.symlink_to(original)
    os.environ['MARS_EXECUTION_CONFIG_PATH'] = str(alias)
    reset_settings_cache()
    try:
        await orch.execution_preparations.wait(session.run, 'execution')
        assert get_execution_preparation(session.run.run_id, session.run.project)['status'] == 'ready'
        alternate = tmp_path / 'other-execution.yaml'
        alternate.write_bytes(Path(original).read_bytes())
        alias.unlink()
        alias.symlink_to(alternate)
        assert get_execution_preparation(session.run.run_id, session.run.project)['status'] == 'stale'
    finally:
        os.environ['MARS_EXECUTION_CONFIG_PATH'] = original
        reset_settings_cache()


@pytest.mark.asyncio
async def test_closing_coordinator_does_not_consume_a_saved_confirmation_to_start_jobs(
    actual_batch: tuple[Orchestrator, RunSession, Path],
) -> None:
    orch, session, _ = actual_batch
    gate = asyncio.create_task(orch._await_execution_confirmation(session, 'execution'))
    await orch.execution_preparations.wait(session.run, 'execution')
    state = get_execution_preparation(session.run.run_id, session.run.project)
    assert state['status'] == 'ready'
    orch.owned_tasks.closing = True
    from app.bridge.execution_confirmation import save_prepared_confirmation
    save_prepared_confirmation(session.run, 'execution', state['token'], state['configuration'])
    assert not await asyncio.wait_for(gate, timeout=5)
    assert session.graph.state('execution') == NodeState.APPROVED
    assert not (session.run.root / 'execution/local_commands').exists()
    assert not (session.run.root / 'execution/jobs').exists()


@pytest.mark.asyncio
async def test_receipt_metadata_only_change_stays_ready_but_same_length_tampering_is_stale(
    actual_batch: tuple[Orchestrator, RunSession, Path],
) -> None:
    from app.bridge.execution_preparation import _signature
    orch, session, _ = actual_batch
    await orch.execution_preparations.wait(session.run, 'execution')
    ready = get_execution_preparation(session.run.run_id, session.run.project)
    assert ready['status'] == 'ready'
    path = Path(ready['validation'][0]['receipt_path'])
    original = path.read_bytes()
    before = _signature(path)
    mode = path.stat().st_mode
    try:
        path.chmod(mode ^ 0o100)
        changed = _signature(path)
        if changed['ctime_ns'] == before['ctime_ns']:
            pytest.skip('This filesystem does not expose chmod metadata changes in st_ctime_ns')
        assert changed['ctime_ns'] != before['ctime_ns']
        assert {key: value for key, value in changed.items() if key != 'ctime_ns'} == {
            key: value for key, value in before.items() if key != 'ctime_ns'}
        assert get_execution_preparation(session.run.run_id, session.run.project)['status'] == 'ready'
        assert path.read_bytes() == original
        assert len(list((session.run.root / 'execution/preflight').glob('*/validation_receipt.json'))) == 2
        assert not (session.run.root / 'execution/confirmations').exists()
        assert not (session.run.root / 'execution/local_commands').exists()
        altered = original.replace(b'"status": "passed"', b'"status": "failed"', 1)
        assert altered != original and len(altered) == len(original)
        timestamps = path.stat()
        path.write_bytes(altered)
        os.utime(path, ns=(timestamps.st_atime_ns, timestamps.st_mtime_ns))
        assert _signature(path)['mtime_ns'] == changed['mtime_ns']
        assert get_execution_preparation(session.run.run_id, session.run.project)['status'] == 'stale'
    finally:
        path.chmod(mode)


@pytest.mark.parametrize('kind', ['receipt', 'derivation', 'data', 'unbound', 'outside', 'budget', 'budget_total'])
def test_receipt_metadata_guard_is_hash_bound_scoped_and_byte_bounded(tmp_path: Path, kind: str) -> None:
    from app.bridge.execution_preparation import _environment as guard_environment, _signature, _unchanged
    root = tmp_path / 'run'
    directory = root / 'execution/preflight/actual-files'
    directory.mkdir(parents=True)
    policy = tmp_path / 'review.yaml'
    policy.write_text(yaml.safe_dump({'poll_interval_seconds': 1, 'max_source_bytes': 4,
                                     'preflight_total_timeout_seconds': 60}))
    with _environment({'MARS_EXECUTION_REVIEW_CONFIG_PATH': str(policy)}):
        path = (tmp_path / 'outside.json') if kind == 'outside' else directory / 'evidence.json'
        path.write_bytes(b'abcde' if kind == 'budget' else b'abc' if kind == 'budget_total' else b'abcd')
        field = 'derivation' if kind == 'derivation' else 'receipt'
        binding: dict[str, Any] = {field + '_path': str(path),
                                  field + '_sha256': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}
        if kind == 'unbound':
            binding.pop(field + '_sha256')
        if kind == 'data':
            binding['input_files'] = [{'path': str(path)}]
        record: dict[str, Any] = {'environment_sha256': guard_environment(), 'guards': [_signature(path)],
                  'configuration': {'validation': [binding]}}
        extra: Path | None = None
        if kind == 'budget_total':
            extra = directory / 'second-evidence.json'
            extra.write_bytes(b'def')
            record['guards'].append(_signature(extra))
            record['configuration']['validation'].append({'receipt_path': str(extra),
                'receipt_sha256': hashlib.sha256(extra.read_bytes()).hexdigest()})
        assert _unchanged(record, root)
        mode = path.stat().st_mode
        try:
            path.chmod(mode ^ 0o100)
            if extra is not None:
                extra.chmod(extra.stat().st_mode ^ 0o100)
            if _signature(path)['ctime_ns'] == record['guards'][0]['ctime_ns']:
                pytest.skip('This filesystem does not expose chmod metadata changes in st_ctime_ns')
            assert _signature(path)['ctime_ns'] != record['guards'][0]['ctime_ns']
            assert _unchanged(record, root) is (kind in {'receipt', 'derivation'})
        finally:
            path.chmod(mode)
