"""Pure contracts and actual temporary files; no provider or execution substitutes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
import yaml

from app.api.research_contracts import router
from app.bridge.research_contract_service import (
    ProjectPreflightError, contract_sha256, default_research_budget, freeze_research_task,
    load_project_contract, parse_project_contract, preflight_project, source_write_allowed,
)
from app.harness.runtime.research_contract import ProjectContract, ResearchBudget


def project_input(root: Path, *, domain: str = "regression") -> dict[str, Any]:
    code = root / "code"
    (code / "src").mkdir(parents=True)
    (code / "baseline.py").write_text("def baseline(x):\n    return x\n", encoding="utf-8")
    (code / "train.py").write_text("raise SystemExit('preflight must not execute this file')\n", encoding="utf-8")
    (code / "src" / "candidate.py").write_text("def candidate(x):\n    return x\n", encoding="utf-8")
    data = root / "samples.json"
    data.write_text("[1, 2, 3]\n", encoding="utf-8")
    return {"project_id": domain, "display_name": domain, "paths": {"code": str(code), "data": [str(data)],
        "knowledge": [], "output": str(root / "results")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
            "arguments": ["train.py"], "entrypoint_files": ["train.py"]} for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "RES" if domain == "pimc" else "MSE", "unit": "dB" if domain == "pimc" else "unitless",
            "direction": "minimize", "target": 0.0, "tolerance": 0.0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["src"], "protected_paths": ["src/reference"],
        "execution": {"kind": "local", "device": "cpu"}}


def test_default_budget_matches_frozen_plan() -> None:
    budget = default_research_budget()
    assert (budget.search_candidates, budget.deep_read_papers, budget.concurrent_readers) == (20, 5, 2)
    assert (budget.proposal_candidates, budget.implemented_candidates, budget.debate_rounds, budget.automatic_iterations) == (2, 1, 1, 2)
    assert (budget.model_requests, budget.tool_executions, budget.research_activity_seconds) == (60, 120, 5400)
    assert (budget.input_tokens, budget.billed_output_tokens, budget.model_cost_cny) == (1_000_000, 128_000, 20)
    assert (budget.request_input_tokens, budget.request_output_tokens, budget.coding_output_tokens) == (48_000, 8192, 16_384)
    assert (budget.training_job_seconds, budget.concurrent_training_jobs, budget.max_gpus,
            budget.training_process_seconds, budget.gpu_seconds) == (600, 1, 1, 3600, 3600)
    assert (budget.operation_retries, budget.repeated_error_limit) == (2, 2)


@pytest.mark.parametrize("invalid", [None, -1, float("inf"), float("nan"), True, "1"])
@pytest.mark.parametrize("field", list(ResearchBudget.model_fields))
def test_every_budget_requires_an_explicit_finite_valid_number(field: str, invalid: Any) -> None:
    raw = default_research_budget().model_dump()
    raw[field] = invalid
    with pytest.raises(ValidationError):
        ResearchBudget.model_validate(raw)


@pytest.mark.parametrize("field", list(ResearchBudget.model_fields))
def test_zero_only_disables_optional_iteration_retry_and_gpu_allowances(field: str) -> None:
    raw = default_research_budget().model_dump()
    raw[field] = 0
    if field in {"automatic_iterations", "operation_retries", "max_gpus"}:
        assert getattr(ResearchBudget.model_validate(raw), field) == 0
    else:
        with pytest.raises(ValidationError):
            ResearchBudget.model_validate(raw)


def test_missing_budget_and_per_action_overflow_are_rejected() -> None:
    raw = default_research_budget().model_dump()
    del raw["model_requests"]
    with pytest.raises(ValidationError):
        ResearchBudget.model_validate(raw)
    raw = default_research_budget().model_dump()
    raw["input_tokens"] = 1
    with pytest.raises(ValidationError, match="per-action"):
        ResearchBudget.model_validate(raw)


@pytest.mark.parametrize("domain", ["pimc", "regression"])
def test_non_git_domain_contracts_share_read_only_preflight_and_freeze(tmp_path: Path, domain: str) -> None:
    raw = project_input(tmp_path, domain=domain)
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    project = load_project_contract(config)
    assert not (Path(project.paths.code) / ".git").exists()
    report = preflight_project(project)
    assert report.ready and not report.issues
    assert {item.path for item in report.input_fingerprints} == {"train.py", "baseline.py"}
    assert not Path(project.paths.output).exists()
    frozen = freeze_research_task(project, goal="Compare the declared candidate with its baseline", mode="manual", budget=default_research_budget())
    json_project = parse_project_contract(json.loads(json.dumps(raw)))
    assert freeze_research_task(json_project, goal=frozen.task.goal, mode="manual", budget=frozen.task.budget) == frozen
    assert frozen.task.project_sha256 == contract_sha256(project)
    original_hash = frozen.task_sha256
    (Path(project.paths.code) / "baseline.py").write_text("def baseline(x):\n    return x + 1\n", encoding="utf-8")
    changed = freeze_research_task(project, goal=frozen.task.goal, mode="manual", budget=frozen.task.budget)
    assert changed.task_sha256 != original_hash
    assert frozen.task_sha256 == original_hash


@pytest.mark.parametrize("scope", ["../outside", "src/../outside", "/absolute", "C:/secret", "src\\outside", "src/*", "src//file", "."])
def test_nonportable_or_escaping_scopes_are_rejected(tmp_path: Path, scope: str) -> None:
    raw = project_input(tmp_path)
    raw["allowed_paths"] = [scope]
    with pytest.raises(ValidationError):
        ProjectContract.model_validate(raw)


def test_baseline_protection_takes_precedence_and_symlinks_are_blocked(tmp_path: Path) -> None:
    raw = project_input(tmp_path)
    raw["allowed_paths"] = ["src", "baseline.py"]
    project = parse_project_contract(raw)
    assert source_write_allowed(project, "src/candidate.py")
    assert not source_write_allowed(project, "src/reference/weights.py")
    assert not source_write_allowed(project, "baseline.py")
    assert not source_write_allowed(project, "train.py")
    (tmp_path / "code" / "src" / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        source_write_allowed(project, "src/escape/samples.json")
    raw["allowed_paths"] = ["src/escape"]
    assert not preflight_project(parse_project_contract(raw)).ready


def test_missing_resources_aggregate_and_block_freeze_without_running_commands(tmp_path: Path) -> None:
    raw = project_input(tmp_path)
    (tmp_path / "code" / "train.py").unlink()
    (tmp_path / "samples.json").unlink()
    raw["commands"][0]["executable"] = str(tmp_path / "absent-python")
    project = parse_project_contract(raw)
    report = preflight_project(project)
    assert not report.ready
    assert {item.code for item in report.issues} >= {"missing_input", "missing_executable", "invalid_source_file"}
    with pytest.raises(ProjectPreflightError) as error:
        freeze_research_task(project, goal="test", mode="bounded_auto", budget=default_research_budget())
    assert error.value.report == report
    assert not Path(project.paths.output).exists()


def test_ssh_and_gpu_are_explicitly_blocked_until_environment_verification(tmp_path: Path) -> None:
    raw = project_input(tmp_path)
    raw["execution"] = {"kind": "ssh", "device": "gpu", "connection_ref": "saved-connection"}
    report = preflight_project(parse_project_contract(raw))
    assert not report.ready
    assert report.issues[-1].code == "remote_preflight_pending"
    raw["execution"] = {"kind": "local", "device": "gpu"}
    assert preflight_project(parse_project_contract(raw)).issues[-1].code == "gpu_preflight_pending"


def test_api_and_direct_service_freeze_identical_contracts(tmp_path: Path) -> None:
    raw = project_input(tmp_path)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        defaults = client.get("/api/research-contracts/defaults")
        assert defaults.status_code == 200
        payload = {"project": raw, "goal": "Compare baseline and candidate", "mode": "manual", "budget": defaults.json()}
        result = client.post("/api/research-contracts/prepare", json=payload)
        assert result.status_code == 200
        direct = freeze_research_task(parse_project_contract(raw), goal=payload["goal"], mode="manual",
                                      budget=default_research_budget())
        assert result.json() == direct.model_dump(mode="json")
        assert client.post("/api/research-contracts/prepare", json={**payload, "mode": "unlimited"}).status_code == 422
        assert client.post("/api/research-contracts/prepare", json={**payload, "goal": " "}).status_code == 422
        del payload["budget"]["model_requests"]
        assert client.post("/api/research-contracts/prepare", json=payload).status_code == 422


def test_credential_fields_are_not_accepted(tmp_path: Path) -> None:
    raw = project_input(tmp_path)
    raw["execution"]["password"] = "not-a-real-secret"
    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_project_contract(raw)


def test_output_cannot_overlap_protected_sources_or_follow_symlinks(tmp_path: Path) -> None:
    raw = project_input(tmp_path)
    raw["paths"]["output"] = str(tmp_path / "code" / "src")
    assert "protected_output" in {issue.code for issue in preflight_project(parse_project_contract(raw)).issues}
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "code", target_is_directory=True)
    raw["paths"]["output"] = str(alias / "results")
    assert "symlink_output" in {issue.code for issue in preflight_project(parse_project_contract(raw)).issues}


def test_real_cli_and_application_share_contract_and_preserve_existing_output(tmp_path: Path) -> None:
    from app.main import create_app

    raw = project_input(tmp_path)
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    root = Path(__file__).resolve().parents[3]
    environment = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
                   "PYTHONPATH": os.pathsep.join(str(root / path) for path in (".", "backend", "posttrain/src", "projects/synthetic_regression/src"))}
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    def cli(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "app.cli", "project", *arguments], cwd=root,
                              env=environment, text=True, capture_output=True, timeout=30, check=False)

    defaults = cli("defaults")
    assert defaults.returncode == 0, defaults.stderr
    budget = json.loads(defaults.stdout)
    preflight = cli("preflight", "--config", str(config))
    assert preflight.returncode == 0, preflight.stderr
    assert json.loads(preflight.stdout)["ready"] is True
    output = tmp_path / "frozen-task.json"
    arguments = ("freeze", "--config", str(config), "--goal", "Compare declared metrics", "--mode", "manual", "--output", str(output))
    frozen = cli(*arguments)
    assert frozen.returncode == 0, frozen.stderr
    assert json.loads(frozen.stdout)["research_started"] is False
    original_output = output.read_bytes()
    with TestClient(create_app()) as client:
        api = client.post("/api/research-contracts/prepare", json={"project": raw, "goal": "Compare declared metrics",
                          "mode": "manual", "budget": budget})
        assert api.status_code == 200, api.text
        assert json.loads(original_output) == api.json()
        assert json.loads(frozen.stdout)["task_sha256"] == api.json()["task_sha256"]
    repeat = cli(*arguments)
    assert repeat.returncode != 0
    assert output.read_bytes() == original_output
    (tmp_path / "samples.json").unlink()
    unavailable = cli("preflight", "--config", str(config))
    assert unavailable.returncode == 2
    assert json.loads(unavailable.stdout)["ready"] is False
    invalid_config = tmp_path / "invalid.yaml"
    invalid_config.write_text("schema_id: research_project.v1\n", encoding="utf-8")
    assert cli("preflight", "--config", str(invalid_config)).returncode != 0
