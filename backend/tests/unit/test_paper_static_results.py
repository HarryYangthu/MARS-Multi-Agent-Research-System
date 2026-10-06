"""Negative admission contracts plus an explicitly selected real saved run.

The optional integration case consumes real training receipts, never a provider,
training-command double, or an invented successful execution.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.bridge.paper_static_results import collect_paper_jobs, metric_unit
from app.bridge.results_service import ResultReader, collect_run_results, load_results_policy
from app.storage.run_store import RunStore


def test_paper_units_are_backend_measurement_units_not_ambiguous_plan_prose() -> None:
    assert metric_unit("RES") == "dB"
    assert metric_unit("APE") == "dB"  # Gain, not angular error.
    assert metric_unit("optimizer_steps") == "optimizer updates"
    assert metric_unit("loss") is None


@pytest.mark.parametrize("path_kind", ["outside", "symlink", "wrong_experiment"])
def test_untrusted_paper_references_cannot_establish_verified_measurements(tmp_path: Path, path_kind: str) -> None:
    run = RunStore(tmp_path / "runs").create(task="negative-paper-admission", project="authored-contract")
    jobs = run.root / "execution/jobs"
    jobs.mkdir()
    target = run.root / "execution/paper_static/another/attempt/execution_receipt.json"
    target.parent.mkdir(parents=True)
    if path_kind == "outside":
        target = tmp_path / "execution/paper_static/another/attempt/execution_receipt.json"
        target.parent.mkdir(parents=True)
    if path_kind == "symlink":
        outside = tmp_path / "authored-invalid.json"
        outside.write_text('{}')
        target.symlink_to(outside)
    else:
        target.write_text('{}')
    (jobs / "invalid.json").write_text(json.dumps({"experiment_id": "one", "result": {},
        "evidence": [{"path": str(target), "sha256": "sha256:invalid"}]}))
    experiments, metrics, curves = collect_paper_jobs(ResultReader(run, load_results_policy()))
    assert len(experiments) == 1 and experiments[0]["verification"] == "invalid"
    assert metrics == [] and curves == []


def test_selected_real_paper_results_are_read_only_and_summary_bound() -> None:
    selected = os.environ.get("MARS_RECORDED_PAPER_RUN")
    if not selected:
        pytest.skip("Requires explicitly selected real paper-training receipts")
    root = Path(selected).resolve(strict=True)
    run = RunStore(root.parent).get(root.name)
    assert run is not None
    files = [path for path in root.rglob("*") if path.is_file() and path.name in {
        "execution_receipt.json", "summary.json", "run_state.json", "run_state.sqlite3", "research_report.approved.md"}]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    value = collect_run_results(run)
    jobs = list((root / "execution/jobs").glob("*.json"))
    assert value["evidence"]["verified_jobs"] == len(jobs) > 0
    assert all(row["verification"] == "verified_local_receipt" for row in value["metrics"])
    for path in jobs:
        job = json.loads(path.read_text())
        observed = {row["name"]: row["value"] for row in value["metrics"] if row["experiment_id"] == job["experiment_id"]}
        assert observed == job["result"]["metrics"]
        assert next(row for row in value["curves"] if row["experiment_id"] == job["experiment_id"])["points"] == job["result"]["loss_curve"]
    assert value["reproduction"]["independent_rerun_verified"] is False
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files} == before
    assert str(root) not in json.dumps(value)
