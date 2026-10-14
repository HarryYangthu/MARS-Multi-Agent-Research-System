"""Real failed processes preserve their cause through the batch and stage."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time

import pytest
import yaml

from app.execution.batch_runner import BatchConfig, BatchOutcome, batch_failure_message, run_batch
from app.execution.results import SimulationResult
from app.execution.simulation_runner import JobSpec
from app.settings import repo_root, reset_settings_cache


@pytest.fixture
def real_failing_command(tmp_path: Path) -> Iterator[Path]:
    source = tmp_path / "read_measurement.py"
    source.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "request = json.loads(Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
        "measurement = Path(request['output_dir']) / 'unavailable_measurement.json'\n"
        "measurement.read_text()\n",
        encoding="utf-8",
    )
    argv = [sys.executable, str(source)]
    tools = yaml.safe_load((repo_root() / "configs/tools.yaml").read_text())
    for name in ("execution.simulation_runner", "execution.batch_runner"):
        tools["tools"][name]["command_allowlist"] = [argv]
    tools_path = tmp_path / "tools.yaml"
    tools_path.write_text(yaml.safe_dump(tools), encoding="utf-8")
    execution_path = tmp_path / "execution.yaml"
    execution_path.write_text(yaml.safe_dump({"execution": {
        "backend": "local_command", "max_concurrency": 1, "batch_steps": 1,
        "command_timeout_seconds": 5,
        "local_commands": [{"id": "read-measurement", "argv": argv, "required_metrics": ["mse"]}],
    }}), encoding="utf-8")
    configured = {
        "MARS_EXECUTION_BACKEND": "local_command", "MARS_EXECUTION_CONFIG_PATH": str(execution_path),
        "MARS_TOOLS_CONFIG_PATH": str(tools_path),
    }
    saved = {name: os.environ.get(name) for name in configured}
    os.environ.update(configured)
    reset_settings_cache()
    try:
        yield source
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        reset_settings_cache()


@pytest.fixture(autouse=True)
async def close_execution_display() -> AsyncIterator[None]:
    yield
    from app.bridge.tensorboard_service import shutdown_tensorboard
    await shutdown_tensorboard()


def test_legacy_result_records_remain_readable_and_failure_fields_roundtrip() -> None:
    saved = {"run_id": "past", "experiment_id": "job", "duration_seconds": 1.0,
             "status": "failed", "metrics": {}, "fingerprint_hash": "sha256:receipt"}
    legacy = SimulationResult(**json.loads(json.dumps(saved)))
    assert legacy.error == "" and legacy.evidence_refs == []
    legacy.error = "FileNotFoundError: unavailable_measurement.json"
    legacy.evidence_refs = ["execution/attempt/stderr.log"]
    restored = SimulationResult(**asdict(legacy))
    assert restored.error == legacy.error and restored.evidence_refs == legacy.evidence_refs
    assert restored.metrics == {} and restored.status == "failed"


def test_batch_failure_message_keeps_first_cause_and_other_failure_count() -> None:
    outcome = BatchOutcome(failures=[("first", "FileNotFoundError: missing input"),
                                    ("second", "RuntimeError: second input")])
    message = batch_failure_message(outcome, 2)
    assert "first: FileNotFoundError: missing input" in message
    assert "1 additional failed jobs" in message
    assert "execution/batch_summary.json" in message
    assert "only 0 of 2 jobs produced results" in batch_failure_message(BatchOutcome(), 2)


@pytest.mark.asyncio
async def test_real_failed_process_cause_and_log_evidence_reach_batch(
    tmp_path: Path, real_failing_command: Path,
) -> None:
    outcome = await run_batch([JobSpec(run_id="real-failure", experiment_id="input-read",
        project="failure-propagation", run_root=tmp_path / "run",
        config={"command_id": "read-measurement", "budget_steps": 1})],
        config=BatchConfig(max_concurrency=1, steps=1))
    assert real_failing_command.is_file()
    assert len(outcome.results) == 1 and len(outcome.failures) == 1
    result = outcome.results[0]
    assert result.status == "failed" and result.metrics == {} and not result.is_mock
    assert "FileNotFoundError" in result.error and "unavailable_measurement.json" in result.error
    assert outcome.failures[0] == (result.experiment_id, result.error)
    references = [Path(ref) for ref in result.evidence_refs]
    assert all(path.is_file() for path in references)
    assert {path.name for path in references} >= {"execution_receipt.json", "stderr.log"}
    receipt = json.loads(next(path for path in references if path.name == "execution_receipt.json").read_text())
    assert receipt["returncode"] == 1 and receipt["status"] == "failed"
    assert "FileNotFoundError" in receipt["error"]


@pytest.mark.asyncio
async def test_real_preflight_failure_preserves_its_receipt_without_launching_execution(
    tmp_path: Path, real_failing_command: Path,
) -> None:
    config_path = tmp_path / "execution.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["execution"]["local_commands"][0]["validation"] = {
        "environment": {"MARS_VALIDATE_ONLY": "1"}, "timeout_seconds": 3,
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    outcome = await run_batch([JobSpec(run_id="real-validation-failure", experiment_id="input-read",
        project="failure-propagation", run_root=tmp_path / "run",
        config={"command_id": "read-measurement", "budget_steps": 1})],
        config=BatchConfig(max_concurrency=1, steps=1))
    assert real_failing_command.is_file()
    assert len(outcome.results) == 1 and len(outcome.failures) == 1
    result = outcome.results[0]
    assert result.status == "failed" and result.metrics == {}
    assert "FileNotFoundError" in result.error
    assert not (tmp_path / "run/execution/local_commands").exists()
    assert len(result.evidence_refs) == 1
    receipt_path = Path(result.evidence_refs[0])
    assert receipt_path.name == "validation_receipt.json" and receipt_path.is_file()
    assert "FileNotFoundError" in json.loads(receipt_path.read_text())["error"]


def test_paper_early_failed_result_keeps_cause_without_inventing_log_evidence(tmp_path: Path) -> None:
    from app.execution.paper_static_adapter import _failed_result
    spec = JobSpec(run_id="missing-paper-input", experiment_id="candidate", project="failure-propagation",
                   run_root=tmp_path / "run")
    result = _failed_result(spec, time.monotonic(), "paper_static data_path does not exist: missing.pth")
    assert result.status == "failed"
    assert result.error == "paper_static data_path does not exist: missing.pth"
    assert result.evidence_refs == [] and not (tmp_path / "run").exists()


@pytest.mark.asyncio
async def test_real_paper_process_failure_keeps_stderr_and_receipt(
    tmp_path: Path, real_failing_command: Path,
) -> None:
    from app.execution.paper_static_adapter import run_paper_static_simulation
    source = tmp_path / "paper-code"
    source.mkdir()
    (source / "train_static.py").write_text(
        "import argparse\nfrom pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--max-steps', type=int)\n"
        "parser.parse_known_args()\n"
        "Path('unavailable_measurement.json').read_text()\n",
        encoding="utf-8",
    )
    (source / "scenario.yaml").write_text("Epoch: 1\nEtotal: 1\n", encoding="utf-8")
    data = tmp_path / "existing-input.json"
    data.write_text("[1, 2, 3]", encoding="utf-8")
    config_path = tmp_path / "execution.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["execution"].update(backend="paper_static", paper_static={
        "repo_path": str(source), "python": sys.executable, "data_path": str(data), "timeout_seconds": 5,
    })
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    os.environ["MARS_EXECUTION_BACKEND"] = "paper_static"
    saved_python = os.environ.get("MARS_PAPER_STATIC_PYTHON")
    os.environ["MARS_PAPER_STATIC_PYTHON"] = sys.executable
    reset_settings_cache()
    try:
        result = await run_paper_static_simulation(JobSpec(run_id="real-paper-failure", experiment_id="input-read",
            project="failure-propagation", run_root=tmp_path / "run", seed=2026,
            config={"entrypoint": "train_static.py", "config_path": "scenario.yaml", "budget_steps": 1}))
    finally:
        if saved_python is None:
            os.environ.pop("MARS_PAPER_STATIC_PYTHON", None)
        else:
            os.environ["MARS_PAPER_STATIC_PYTHON"] = saved_python
        reset_settings_cache()
    assert real_failing_command.is_file()
    assert result.status == "failed" and not result.is_mock
    assert "FileNotFoundError" in result.error and "unavailable_measurement.json" in result.error
    references = [Path(ref) for ref in result.evidence_refs]
    assert all(path.is_file() for path in references)
    assert {path.suffix for path in references} == {".json", ".log"}
    receipt = json.loads(next(path for path in references if path.name == "execution_receipt.json").read_text())
    assert receipt["returncode"] == 1 and receipt["status"] == "failed"
    assert "FileNotFoundError" in receipt["error"]


@pytest.mark.asyncio
async def test_real_stage_error_and_saved_summary_preserve_actual_failed_input(
    tmp_path: Path, real_failing_command: Path,
) -> None:
    from app.bridge.agent_runner import _run_execution_batch
    from app.bridge.execution_confirmation import execution_preview, save_confirmation
    from app.harness.schema.frontmatter_parser import dumps
    from app.storage.artifact_store import ArtifactStore
    from app.storage.run_store import RunStore

    run = RunStore(tmp_path / "runs").create(task="real failed input read", project="failure-propagation")
    plan = {"schema": "run_log.v1", "agent": "execution", "project": run.project,
        "run_id": run.run_id, "status": "interrupted", "metrics": {"planned_experiments": 1},
        "fingerprint_hash": "sha256:00", "planned_experiments": [{"name": "input-read",
        "config": {"command_id": "read-measurement", "seed": 2026, "budget_steps": 1}}]}
    store = ArtifactStore(run)
    store.approve(store.write(text=dumps(plan, "Human-authored execution input; no measurements asserted.")))
    preview = execution_preview(run, "execution")
    assert not preview["blockers"] and real_failing_command.is_file()
    save_confirmation(run, "execution", preview["token"])
    with pytest.raises(RuntimeError, match="actual execution batch failed") as caught:
        await _run_execution_batch(run=run, node_key="execution")
    assert "FileNotFoundError" in str(caught.value)
    assert "unavailable_measurement.json" in str(caught.value)
    summary = json.loads((run.root / "execution/batch_summary.json").read_text())
    assert "FileNotFoundError" in summary["failures"][0][1]
    references = summary["failure_evidence"]["input-read"]
    assert references and all(Path(ref).is_file() for ref in references)
    assert any(Path(ref).name == "stderr.log" for ref in references)
