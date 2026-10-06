"""Actual CPU measurements exercise the pipeline and its artifact boundary."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
import yaml

from app.execution.batch_runner import BatchConfig, run_batch
from app.execution.simulation_runner import JobSpec, run_one
from app.settings import reset_settings_cache
from app.bridge.tensorboard_service import shutdown_tensorboard


@pytest.fixture(autouse=True)
async def close_execution_views() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await shutdown_tensorboard()


@pytest.fixture
def actual_regression_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    source = tmp_path / "measure_regression.py"
    source.write_text(
        "import json,os,pathlib,time\n"
        "request=json.loads(pathlib.Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
        "time.sleep(request['config'].get('delay',0))\n"
        "samples=json.loads(pathlib.Path(request['config']['data_path']).read_text())\n"
        "scale=request['config']['scale']\n"
        "predictions=[scale*x for x,y in samples]\n"
        "mse=sum((prediction-row[1])**2 for prediction,row in zip(predictions,samples))/len(samples)\n"
        "pathlib.Path('predictions.json').write_text(json.dumps({'samples':samples,'predictions':predictions}))\n"
        "result={'schema':'local_command_result.v1','status':'completed','invocation_id':request['invocation_id'],"
        "'run_id':request['run_id'],'experiment_id':request['experiment_id'],'metrics':{'mse':mse},'evidence_paths':['predictions.json']}\n"
        "if request['config'].get('wrong_id'): result['invocation_id']='older-invocation'\n"
        "if not request['config'].get('stdout_only'): pathlib.Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps(result))\n"
        "print(json.dumps({'metrics':{'mse':mse}}))\n",
    )
    argv = [sys.executable, str(source)]
    tools_path = tmp_path / "host-tools.yaml"
    from app.settings import repo_root
    tool_configuration = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
    for name in ('execution.simulation_runner', 'execution.batch_runner'):
        tool_configuration['tools'][name]['command_allowlist'] = [argv]
    tools_path.write_text(yaml.safe_dump(tool_configuration))
    execution_path = tmp_path / "host-execution.yaml"
    execution_path.write_text(yaml.safe_dump({"execution": {"backend": "local_command", "command_timeout_seconds": 5,
        "local_commands": [{"id": "regression", "argv": argv, "required_metrics": ["mse"]}]}}))
    monkeypatch.setenv("MARS_TOOLS_CONFIG_PATH", str(tools_path))
    monkeypatch.setenv("MARS_EXECUTION_CONFIG_PATH", str(execution_path))
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "local_command")
    reset_settings_cache()
    data_path = tmp_path / "samples.json"
    data_path.write_text(json.dumps([[1, 2], [2, 4], [3, 6]]))
    yield data_path
    reset_settings_cache()


@pytest.mark.asyncio
async def test_main_batch_runs_actual_configurations_and_binds_measurement_receipts(
    tmp_path: Path, actual_regression_command: Path,
) -> None:
    specs = [JobSpec(run_id="real-cpu", experiment_id=f"scale-{scale}", project="regression",
        run_root=tmp_path / "run", config={"scale": scale, "data_path": str(actual_regression_command)}) for scale in (1, 2)]
    outcome = await run_batch(specs, config=BatchConfig(max_concurrency=2, steps=1))
    assert not outcome.failures and len(outcome.results) == 2
    measured = {result.experiment_id: result.metrics["mse"] for result in outcome.results}
    assert measured == {"scale-1": pytest.approx(14 / 3), "scale-2": 0}
    for result in outcome.results:
        assert result.status == "completed" and not result.is_mock
        receipt_path = next((tmp_path / "run/execution/local_commands" / result.experiment_id).glob("*/execution_receipt.json"))
        receipt = json.loads(receipt_path.read_text())
        assert receipt["returncode"] == 0 and receipt["status"] == "completed"
        assert receipt["command_files"] and receipt["evidence"]
        assert result.fingerprint_hash == "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        evidence = receipt["evidence"][0]
        assert evidence["sha256"] == "sha256:" + hashlib.sha256(Path(evidence["path"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("fault", ["stdout_only", "wrong_id"])
@pytest.mark.asyncio
async def test_stdout_or_previous_invocation_cannot_become_success(
    tmp_path: Path, actual_regression_command: Path, fault: str,
) -> None:
    result = await run_one(JobSpec(run_id="invalid-receipt", experiment_id=fault, project="regression",
        run_root=tmp_path / "run", config={"data_path": str(actual_regression_command), "scale": 2, fault: True}))
    assert result.status == "failed" and result.metrics == {} and not result.is_mock
    receipt_path = next((tmp_path / "run/execution/local_commands" / fault).glob("*/execution_receipt.json"))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["returncode"] == 0 and receipt["status"] == "failed" and receipt["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("omit_result", [False, True])
async def test_bridge_executes_manual_approved_plan_and_preserves_seed(
    tmp_path: Path, actual_regression_command: Path, omit_result: bool,
) -> None:
    from app.bridge.agent_runner import _run_execution_batch
    from app.harness.schema.frontmatter_parser import dumps
    from app.storage.artifact_store import ArtifactStore
    from app.storage.run_store import RunStore

    run = RunStore(tmp_path / "runs").create(task="manual numerical plan", project="regression")
    plan = {"schema": "run_log.v1", "agent": "execution", "project": "regression", "run_id": run.run_id,
            "status": "interrupted", "metrics": {"planned_experiments": 1}, "fingerprint_hash": "sha256:00",
            "planned_experiments": [{"name": "actual-comparison", "config": {
                "scale": 2, "data_path": str(actual_regression_command), "seed": 0, "stdout_only": omit_result}}]}
    store = ArtifactStore(run)
    store.approve(store.write(text=dumps(plan, "Manually authored test input; no Agent output is substituted.")))
    from app.bridge.execution_confirmation import execution_preview, save_confirmation
    preview = execution_preview(run, 'execution')
    save_confirmation(run, 'execution', preview['token'])
    if omit_result:
        with pytest.raises(RuntimeError, match="actual execution batch failed"):
            await _run_execution_batch(run=run, node_key="execution")
    else:
        await _run_execution_batch(run=run, node_key="execution")
        metrics = json.loads((run.root / "execution/metrics.json").read_text())
        assert metrics[0]["metrics"]["mse"] == 0
    jobs = list((run.root / "execution/local_commands/actual-comparison").glob("*/job.json"))
    assert len(jobs) == 1 and json.loads(jobs[0].read_text())["seed"] == 0
    summary = json.loads((run.root / "execution/batch_summary.json").read_text())
    assert bool(summary["failures"]) is omit_result


@pytest.mark.asyncio
async def test_managed_resume_reuses_only_intact_completed_measurements(tmp_path: Path, actual_regression_command: Path) -> None:
    root = tmp_path / 'managed'
    spec = JobSpec(run_id='managed', experiment_id='candidate', project='regression', run_root=root,
                   seed=0, config={'scale': 2, 'data_path': str(actual_regression_command)})
    first = await run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1))
    second = await run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1))
    assert not first.failures and not second.failures
    assert first.results[0].fingerprint_hash == second.results[0].fingerprint_hash
    assert len(list((root / 'execution/local_commands/candidate').glob('*/job.json'))) == 1
    evidence = next((root / 'execution/local_commands/candidate').glob('*/predictions.json'))
    evidence.write_text('{}')
    rejected = await run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1))
    assert rejected.failures and not rejected.results
    assert len(list((root / 'execution/local_commands/candidate').glob('*/job.json'))) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_job_and_unknown_state_do_not_relaunch(tmp_path: Path, actual_regression_command: Path) -> None:
    import asyncio
    root = tmp_path / 'deduplicate'
    spec = JobSpec(run_id='deduplicate', experiment_id='candidate', project='regression', run_root=root,
                   config={'scale': 2, 'data_path': str(actual_regression_command)})
    outcomes = await asyncio.gather(*(run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1)) for _ in range(2)))
    assert any(not row.failures for row in outcomes)
    assert len(list((root / 'execution/local_commands/candidate').glob('*/job.json'))) == 1
    journal = next((root / 'execution/jobs').glob('*.json'))
    state = json.loads(journal.read_text())
    state['status'] = 'running'  # Author an uncertain historical state; no success is substituted.
    journal.write_text(json.dumps(state))
    blocked = await run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1))
    assert blocked.failures and not blocked.results
    assert len(list((root / 'execution/local_commands/candidate').glob('*/job.json'))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True])
async def test_deterministic_execution_through_orchestrator_waits_for_user_then_runs_real_jobs(
    tmp_path: Path, actual_regression_command: Path, interrupt: bool,
) -> None:
    import asyncio
    from app.agents.execution.agent import ExecutionAgent
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest
    from app.bridge.execution_confirmation import execution_preview, save_confirmation
    from app.harness.schema.frontmatter_parser import dumps
    from app.harness.runtime.state_machine import NodeState
    from app.storage.artifact_store import ArtifactStore
    from app.storage.run_store import RunStore
    registry = AgentRegistry()
    registry.register('execution', ExecutionAgent())
    orch = Orchestrator(run_store=RunStore(tmp_path / 'flow'), registry=registry)
    session = orch.create_session(RunRequest(task='actual numerical execution', project='regression',
        entrypoint='execution', standalone=True, auto_approve=False))
    rows = [{'name': f'comparison-{scale}', 'config': {'seed': 0, 'scale': scale,
        'data_path': str(actual_regression_command), 'delay': 30 if interrupt else 0}} for scale in range(5)]
    store = ArtifactStore(session.run)
    plan = {'schema': 'experiment_plan.v1', 'agent': 'experiment', 'project': 'regression',
        'variables': {'independent': ['scale'], 'dependent': ['mse']}, 'metrics': {'primary': 'mse'},
        'ablations': rows, 'estimated_runs': 5}
    code = {'schema': 'code_spec.v1', 'agent': 'coding', 'project': 'regression', 'target_lang': 'python',
        'baseline_compat': {'preserved': True}, 'files_changed': [],
        'execution_jobs': [{'name': row['name'], 'config': {'command_id': 'regression'}} for row in rows]}
    for metadata in (plan, code):
        store.approve(store.write(text=dumps(metadata, 'Human-authored scientific input, verified by schema.')))
    assert orch._spawn_owned(session, 'start', lambda: orch.run(session.run.run_id))
    owner = orch.owned_tasks.active(session.run.run_id)
    assert owner is not None
    for _ in range(100):
        if session.graph.state('execution') == NodeState.APPROVED or owner.done():
            break
        await asyncio.sleep(0.02)
    assert session.graph.state('execution') == NodeState.APPROVED
    assert not (session.run.root / 'execution/local_commands').exists()
    preview = execution_preview(session.run, 'execution')
    assert not preview['blockers'] and not preview['confirmed'] and len(preview['experiments']) == 5
    budget = session.run.root / 'resources/model_budget.v1.json'
    before = budget.read_bytes() if budget.exists() else b''
    save_confirmation(session.run, 'execution', preview['token'])
    if interrupt:
        from app.bridge.run_recovery import recovery_status, recover_run
        for _ in range(200):
            if list((session.run.root / 'execution/local_commands').glob('*/*/job.json')):
                break
            await asyncio.sleep(0.01)
        stopped = await orch.stop_owned_run(session.run.run_id)
        assert stopped['termination']['cleanup_complete']
        assert session.graph.state('execution') == NodeState.FAILED
        original_jobs = list((session.run.root / 'execution/local_commands').glob('*/*/job.json'))
        recovery = recovery_status(orch, session.run.run_id, project='regression')
        assert recovery['actions'], recovery
        assert recovery['actions'][0]['label'] == '重新核对并恢复仿真'
        result = await recover_run(orch, session.run.run_id, project='regression', action='retry',
                                   node='execution', token=recovery['token'])
        assert result['ok']
        restored = orch.owned_tasks.active(session.run.run_id)
        assert restored is not None
        for _ in range(100):
            if session.graph.state('execution') == NodeState.APPROVED or restored.done():
                break
            await asyncio.sleep(0.02)
        assert session.graph.state('execution') == NodeState.APPROVED
        next_preview = execution_preview(session.run, 'execution')
        assert not next_preview['confirmed'] and next_preview['token'] != preview['token']
        assert list((session.run.root / 'execution/local_commands').glob('*/*/job.json')) == original_jobs
        await orch.stop_owned_run(session.run.run_id)
        assert (budget.read_bytes() if budget.exists() else b'') == before
        return
    await asyncio.wait_for(owner, timeout=20)
    assert session.graph.state('execution') == NodeState.DONE
    summary = json.loads((session.run.root / 'execution/batch_summary.json').read_text())
    assert not summary['failures']
    results = json.loads((session.run.root / 'execution/metrics.json').read_text())
    assert len(results) == 5
    assert next(row for row in results if row['experiment_id'] == 'comparison-2')['metrics']['mse'] == 0
    assert (budget.read_bytes() if budget.exists() else b'') == before


@pytest.mark.asyncio
async def test_interrupt_kills_real_worker_and_requires_explicit_new_attempt(tmp_path: Path, actual_regression_command: Path) -> None:
    import asyncio
    import os
    root = tmp_path / 'interrupt'
    config = {'scale': 2, 'data_path': str(actual_regression_command), 'delay': 30, 'confirmation_token': 'first'}
    spec = JobSpec(run_id='interrupt', experiment_id='candidate', project='regression', run_root=root, config=config)
    owner = asyncio.create_task(run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1)))
    for _ in range(100):
        if list((root / 'execution/local_commands/candidate').glob('*/job.json')):
            break
        await asyncio.sleep(0.01)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    state = json.loads(next((root / 'execution/jobs').glob('*.json')).read_text())
    assert state['status'] == 'interrupted'
    receipt = json.loads(next((root / 'execution/local_commands/candidate').glob('*/execution_receipt.json')).read_text())
    assert receipt['status'] == 'cancelled'
    if receipt['pid'] is not None:
        with pytest.raises(ProcessLookupError):
            os.kill(receipt['pid'], 0)
    blocked = await run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1))
    assert blocked.failures and len(list((root / 'execution/local_commands/candidate').glob('*/job.json'))) == 1
    spec.config = {**config, 'delay': 0, 'confirmation_token': 'explicit-new-attempt'}
    recovered = await run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1))
    assert not recovered.failures and recovered.results[0].metrics['mse'] == 0
    assert len(list((root / 'execution/local_commands/candidate').glob('*/job.json'))) == 2
