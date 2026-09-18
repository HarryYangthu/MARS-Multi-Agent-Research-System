"""The E2E driver checks real local configuration and caller-owned input files."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.run_runtime_e2e import E2EConfig, execute, file_record, preflight, request_payload
from scripts.run_runtime_e2e import validate_dataset_selection
from app.storage.data_source_store import sha256_file


def test_default_preflight_cannot_claim_execution_without_task_and_input_files(tmp_path: Path) -> None:
    report = preflight(E2EConfig(), base=tmp_path)
    assert report["status"] == "required_dependency_missing"
    missing = {check["name"] for check in report["missing"]}
    assert {"task_input", "baseline_file", "data_path", "data_description"} <= missing
    assert report["model_calls_executed"] == report["tool_calls_executed"] == report["experiments_executed"] == 0
    assert report["scientific_validated"] is False


def test_config_rejects_seed_artifact_and_invalid_approval_or_network_expansion() -> None:
    with pytest.raises(ValidationError):
        E2EConfig.model_validate({"seed_artifact": "prewritten success"})
    with pytest.raises(ValidationError):
        E2EConfig.model_validate({"approval_mode": "bypass"})
    with pytest.raises(ValidationError):
        E2EConfig.model_validate({"network_allowed_domains": ["*"]})


def test_payload_keeps_exact_task_baseline_and_human_approval(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.py"
    baseline.write_text("def model(value):\n    return value\n")
    config = E2EConfig(task="caller task", user_request="Keep this exact constraint: no new parameters.\n",
        baseline_file="baseline.py", data_description="Caller supplied two-column measured data.")
    payload = request_payload(config, base=tmp_path, source_id="actual-dataset-id")
    assert payload["user_request"] == config.user_request
    assert payload["idea_context"]["baseline_code"] == baseline.read_text()
    assert payload["data_source"] == {"id": "actual-dataset-id"}
    assert payload["auto_approve"] is False and "seed_artifact" not in payload
    assert payload["entrypoint"] == "pipeline" and payload["standalone"] is False
    assert file_record(baseline)["bytes"] == baseline.stat().st_size


def test_missing_preflight_does_not_even_connect_to_service(tmp_path: Path) -> None:
    config = E2EConfig(api_url="http://127.0.0.1:1")
    report = preflight(config, base=tmp_path)
    outcome = asyncio.run(execute(config, base=tmp_path, report=report, output=tmp_path / "report.json"))
    assert outcome is report and outcome["phase"] == "preflight"
    assert not (tmp_path / "report.json").exists()


def test_dataset_identity_matches_actual_storage_checksum_and_rejects_other_inputs(tmp_path: Path) -> None:
    dataset = tmp_path / "data.json"
    dataset.write_text('{"x": [1, 2], "y": [2, 4]}')
    profile = {"project": "regression", "checksum": sha256_file(dataset)}
    original = file_record(dataset)["sha256"]
    validate_dataset_selection(profile, project="regression", sha256=original)
    with pytest.raises(ValueError, match="does not match"):
        validate_dataset_selection(profile, project="another-project", sha256=original)
    dataset.write_text('{"x": [1, 2], "y": [2, 5]}')
    with pytest.raises(ValueError, match="does not match"):
        validate_dataset_selection(profile, project="regression", sha256=file_record(dataset)["sha256"])


def test_prepared_fixture_runs_real_code_checks_and_rejects_invalid_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.run_runtime_e2e import prepare_regression_fixture
    from app.settings import reset_settings_cache
    from app.bridge.diagnostics import load_diagnostics_config
    from app.harness.project_workspace import open_folder
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    reset_settings_cache()
    root = tmp_path / "fixture"
    try:
        prepare_regression_fixture(root)
        checker = tmp_path / "fixture-host/check_candidate.py"
        before = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)
        assert before.returncode == 0 and "no experiment executed" in before.stdout
        (root / "candidate.py").write_text("DEGREE = 6\nREGULARIZATION = 0.0\n")
        invalid = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)
        assert invalid.returncode != 0
        project = open_folder(str(root))
        diagnostics = load_diagnostics_config(project.name)
        assert (project.metadata_root / "diagnostics.yaml").is_file()
        assert diagnostics.max_iterations == diagnostics.default_budget == 1
        assert len(diagnostics.metric_rules) == 1
        rule = diagnostics.metric_rules[0]
        assert (rule.name, rule.direction, rule.aggregation, rule.target) == ("improvement", "gte", "min", 1e-12)
    finally:
        reset_settings_cache()
