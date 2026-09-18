"""Actual CPU measurements exercise the pipeline and its artifact boundary."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from collections.abc import Iterator

import pytest
import yaml

from app.execution.batch_runner import BatchConfig, run_batch
from app.execution.simulation_runner import JobSpec, run_one
from app.settings import reset_settings_cache


@pytest.fixture
def actual_regression_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    source = tmp_path / "measure_regression.py"
    source.write_text(
        "import json,os,pathlib\n"
        "request=json.loads(pathlib.Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
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
    tools_path.write_text(yaml.safe_dump({"tools": {
        name: {"command_allowlist": [argv]} for name in ("execution.simulation_runner", "execution.batch_runner")
    }}))
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
