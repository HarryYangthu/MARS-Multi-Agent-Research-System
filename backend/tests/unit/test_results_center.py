"""Real CPU measurements, real journals/files and ASGI routes; no success doubles."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from app.api import dependencies
from app.api.results import router
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.bridge.research_contract_service import default_research_budget, freeze_research_task
from app.bridge.research_run_service import create_research_run
from app.bridge.results_export import create_results_export, read_results_export, render_files
from app.bridge.results_service import ResultReader, collect_run_results, load_results_policy, public_text, sha256
from app.bridge.workflow_service import build_standalone
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.runtime.research_contract import ProjectContract
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle, RunStore

ROOT = Path(__file__).resolve().parents[3]


def create_measured_run(root: Path, *, repeats: int = 2) -> RunHandle:
    """Reusable actual CPU seed: saved run stays created, jobs really calculate.

    Root may be an isolated application runtime for a browser acceptance run.
    No model/Agent run or research success is claimed by the authored graph.
    """
    root.mkdir(parents=True, exist_ok=True)
    run = RunStore(root / "runs").create(task="real-cpu-regression-results", project="synthetic_regression", entrypoint="idea",
        user_request="Compare recorded validation MSE from bounded CPU linear regression; scientific outcome remains unknown.")
    graph = build_standalone("idea")
    RunStateStore(run).write(graph=graph, request=asdict(RunRequest(task=run.task, project=run.project,
        entrypoint="idea", standalone=True)), status="created", expected_revision=0)
    fixture = root / "measurement_fixture"
    fixture.mkdir(exist_ok=True)
    worker = fixture / "linear_regression.py"
    worker.write_text('''import json, os, random
from pathlib import Path
request = json.loads(Path(os.environ["MARS_JOB_REQUEST"]).read_text())
rng = random.Random(request["seed"])
xs = [i / 10 for i in range(-10, 11)]
ys = [2 * x + 0.5 for x in xs]
a, b = rng.uniform(-0.1, 0.1), 0.0
curve = []
for step in range(request["steps"]):
    errors = [a * x + b - y for x, y in zip(xs, ys)]
    curve.append(sum(error * error for error in errors) / len(errors))
    a -= 0.1 * sum(error * x for error, x in zip(errors, xs)) / len(xs)
    b -= 0.1 * sum(errors) / len(xs)
predictions = [a * x + b for x in xs]
mse = sum((predicted - actual) ** 2 for predicted, actual in zip(predictions, ys)) / len(ys)
Path("measurements.json").write_text(json.dumps({"samples": xs, "targets": ys, "predictions": predictions, "weights": [a, b]}))
Path(os.environ["MARS_RESULT_PATH"]).write_text(json.dumps({"schema": "local_command_result.v1",
    "run_id": request["run_id"], "experiment_id": request["experiment_id"], "invocation_id": request["invocation_id"],
    "status": "completed", "metrics": {"MSE": mse}, "evidence_paths": ["measurements.json"], "loss_curve": curve}))
''', encoding="utf-8")
    execution = fixture / "execution.yaml"
    execution.write_text(yaml.safe_dump({"execution": {"command_timeout_seconds": 15,
        "local_commands": [{"id": "linear-regression", "argv": [sys.executable, str(worker)], "required_metrics": ["MSE"]}]}}))
    tools = fixture / "tools.yaml"
    tools.write_text(yaml.safe_dump({"tools": {"execution.simulation_runner": {
        "command_allowlist": [[sys.executable, str(worker)]], "process_backend": "local_process", "require_isolation": False}}}))
    driver = '''import asyncio, sys
from pathlib import Path
from app.harness.tools.execution.local_command import LocalCommandJob, run_local_command
async def execute():
    for index in range(int(sys.argv[4])):
        result = await run_local_command(LocalCommandJob(run_id=sys.argv[1], experiment_id="candidate-a", project=sys.argv[2],
            run_root=Path(sys.argv[3]), config={"role": "candidate", "method": "linear_regression"}, seed=index + 1,
            steps=12, command_id="linear-regression"))
        if result.status != "completed":
            raise RuntimeError("Actual CPU command did not complete")
asyncio.run(execute())
'''
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    env.update(PYTHONPATH=str(ROOT / "backend"), MARS_EXECUTION_CONFIG_PATH=str(execution), MARS_TOOLS_CONFIG_PATH=str(tools))
    result = subprocess.run([sys.executable, "-c", driver, run.run_id, run.project, str(run.root), str(repeats)],
                            cwd=fixture, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    return run


@pytest.fixture
def measured_run(tmp_path: Path) -> RunHandle:
    return create_measured_run(tmp_path)


def _files(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes()) for path in root.rglob("*") if path.is_file()}


def test_result_reads_real_measurements_without_promoting_run_or_goal(measured_run: RunHandle) -> None:
    before = _files(measured_run.root)
    result = collect_run_results(measured_run)
    assert result["state"]["authority"] == "sqlite" and result["state"]["status"] == "created"
    assert result["state"]["read_only"] is True and result["outcome"]["status"] == "unknown"
    assert result["evidence"] == {"level": "receipt_verified", "verified_jobs": 2, "total_jobs": 2}
    assert len(result["metrics"]) == 2 and all(row["name"] == "MSE" and row["value"] > 0 for row in result["metrics"])
    assert len(result["curves"]) == 2 and all(len(row["points"]) == 12 for row in result["curves"])
    assert result["statistics"][0]["n"] == 2 and result["statistics"][0]["standard_deviation"] > 0
    assert result["statistics"][0]["independent_repeats"] is False
    assert result["resources"]["input_tokens"] is None and result["resources"]["cost"] is None
    assert str(measured_run.root) not in json.dumps(result)
    assert _files(measured_run.root) == before


def test_single_job_does_not_invent_variance_or_independent_reproduction(tmp_path: Path) -> None:
    run = create_measured_run(tmp_path, repeats=1)
    result = collect_run_results(run)
    summary = result["statistics"][0]
    assert summary["n"] == 1 and summary["standard_deviation"] is None and summary["independent_repeats"] is False
    assert result["reproduction"]["status"] == "reviewable_only" and result["reproduction"]["independent_rerun_verified"] is False


@pytest.mark.parametrize("changed", ["result", "request", "evidence", "missing", "symlink", "identity"])
def test_changed_or_missing_receipt_chain_never_publishes_verified_metrics(measured_run: RunHandle, tmp_path: Path, changed: str) -> None:
    receipt = next((measured_run.root / "execution/local_commands").glob("*/*/execution_receipt.json"))
    attempt = receipt.parent
    if changed == "result":
        value = json.loads((attempt / "result.json").read_text())
        value["metrics"]["MSE"] = 0
        (attempt / "result.json").write_text(json.dumps(value))
    elif changed == "request":
        value = json.loads((attempt / "job.json").read_text())
        value["seed"] = 100
        (attempt / "job.json").write_text(json.dumps(value))
    elif changed in {"evidence", "missing", "symlink"}:
        path = attempt / "measurements.json"
        if changed == "evidence":
            path.write_text("changed actual measurement")
        else:
            original = path.read_bytes()
            path.unlink()
            if changed == "symlink":
                outside = tmp_path / "private_original.json"
                outside.write_bytes(original)
                path.symlink_to(outside)
    else:
        value = json.loads(receipt.read_text())
        value["run_id"] = "another-run"
        receipt.write_text(json.dumps(value))
    result = collect_run_results(measured_run)
    assert result["evidence"]["verified_jobs"] == 1 and len(result["metrics"]) == 1
    assert any(item["verification"] == "invalid" for item in result["experiments"])
    assert result["outcome"]["status"] == "unknown"


def test_metrics_file_alone_is_unverified_and_missing_status_is_not_completed(tmp_path: Path) -> None:
    run = RunStore(tmp_path / "runs").create(task="untrusted-projection", project="second-domain")
    (run.root / "execution/metrics.json").write_text(json.dumps([{"experiment_id": "observed-record", "metrics": {"accuracy": 0.8}}]))
    result = collect_run_results(run)
    assert result["state"]["status"] == "unknown" and result["state"]["authority"] == "missing"
    assert result["evidence"]["verified_jobs"] == 0 and result["experiments"] == []
    assert result["metrics"][0]["verification"] == "unverified" and result["metrics"][0]["direction"] is None
    assert result["outcome"]["status"] == "unknown" and result["statistics"] == []


def test_missing_sqlite_never_falls_back_to_completed_projection(measured_run: RunHandle) -> None:
    (measured_run.root / "run_state.sqlite3").unlink()
    projection = json.loads((measured_run.root / "run_state.json").read_text())
    projection["status"] = "completed"
    (measured_run.root / "run_state.json").write_text(json.dumps(projection))
    result = collect_run_results(measured_run)
    assert result["state"]["authority"] == "invalid" and result["state"]["status"] == "unknown"
    assert result["outcome"]["status"] == "unknown"


def test_reading_state_does_not_recover_or_write_approval_transactions(measured_run: RunHandle) -> None:
    # A deliberately incomplete transaction must remain unchanged on a result GET.
    directory = measured_run.root / "idea/.approvals"
    directory.mkdir()
    (directory / "pending.json").write_text('{"incomplete":true}')
    before = _files(measured_run.root)
    assert collect_run_results(measured_run)["state"]["authority"] == "sqlite"
    assert _files(measured_run.root) == before


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts += 1
        self.links.extend(value for name, value in attrs if name in {"href", "src"} and value is not None)


def test_offline_export_hashes_links_and_contents_are_self_contained(measured_run: RunHandle, tmp_path: Path) -> None:
    expected = collect_run_results(measured_run)
    exported = create_results_export(measured_run)
    encoded = read_results_export(measured_run, exported["export_id"])
    assert sha256(encoded) == exported["archive_sha256"]
    destination = tmp_path / "another-machine"
    with zipfile.ZipFile(io.BytesIO(encoded)) as archive:
        archive.extractall(destination)
        names = set(archive.namelist())
        assert not any(name.endswith((".py", ".pth", ".env", ".log")) for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest == exported["manifest"]
        for entry in manifest["files"]:
            data = archive.read(entry["path"])
            assert len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"]
        result = json.loads(archive.read("results.json"))
        assert result == expected
        raw = b"\n".join(archive.read(name) for name in names)
        assert str(measured_run.root).encode() not in raw and b'"samples"' not in raw and b'"predictions"' not in raw
    parser = Links()
    parser.feed((destination / "report.html").read_text())
    assert parser.scripts == 0 and all((destination / name).is_file() for name in parser.links)
    md_links = re.findall(r"\]\(([^)]+)\)", (destination / "report.md").read_text())
    assert md_links and all((destination / name).is_file() for name in md_links)


def test_secret_paths_html_and_csv_are_safe_before_and_after_export(measured_run: RunHandle) -> None:
    payload = ('<script>alert("x")</script> [x](javascript:alert(1))\n'
               'api_key=private-secret-value\nC:\\Users\\alice\\private.csv /Users/alice/data.csv\n'
               'https://example.com/report?token=unpublished-secret\n'
               '-----BEGIN PRIVATE KEY-----\nprivate-key-bytes\n-----END PRIVATE KEY-----')
    (measured_run.root / "input/user_request.md").write_text(payload)
    result = collect_run_results(measured_run)
    encoded_result = json.dumps(result)
    for secret in ("private-secret-value", "unpublished-secret", "private-key-bytes", "/Users/alice", "C:\\\\Users"):
        assert secret not in encoded_result
    # Rendering also protects arbitrary projected text if an upstream field is
    # added later; these are text/schema inputs, never fabricated measurements.
    result["metrics"].extend({"experiment_id": value, "name": value, "verification": "unverified"}
                             for value in ("=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1"))
    files = render_files(result)
    parser = Links()
    parser.feed(files["report.html"].decode())
    assert parser.scripts == 0 and "javascript:" not in " ".join(parser.links)
    csv_text = files["metrics.csv"].decode("utf-8-sig")
    for value in ("=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1"):
        assert "'" + value in csv_text


@pytest.mark.parametrize("identifier", ["../escape", "a" * 31, "a" * 33, "a/" + "b" * 30, "../" + "a" * 32])
def test_export_identifier_cannot_cross_paths(measured_run: RunHandle, identifier: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        read_results_export(measured_run, identifier)


def test_modified_archive_or_symlink_is_rejected_at_download(measured_run: RunHandle, tmp_path: Path) -> None:
    exported = create_results_export(measured_run)
    path = measured_run.root / "results/exports" / exported["export_id"] / "results.zip"
    original = path.read_bytes()
    path.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        read_results_export(measured_run, exported["export_id"])
    path.unlink()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(original)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink"):
        read_results_export(measured_run, exported["export_id"])


def test_actual_api_uses_same_read_model_and_export_validation(measured_run: RunHandle) -> None:
    dependencies.reset_for_tests()
    dependencies._run_store = RunStore(measured_run.root.parent)
    app = FastAPI()
    app.include_router(router)
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/results/{measured_run.run_id}")
            assert response.status_code == 200 and response.json() == collect_run_results(measured_run)
            exported = client.post(f"/api/results/{measured_run.run_id}/exports")
            assert exported.status_code == 201
            downloaded = client.get(exported.json()["download_url"])
            assert downloaded.status_code == 200 and downloaded.headers["content-type"] == "application/zip"
            assert sha256(downloaded.content) == exported.json()["archive_sha256"]
            assert client.get("/api/results/missing-run").status_code == 404
            assert client.get(f"/api/results/{measured_run.run_id}/exports/invalid/download").status_code == 409
    finally:
        dependencies.reset_for_tests()


def _settled_budget(run: RunHandle, *, known: bool = True) -> RunModelBudget:
    """Persist real quota arithmetic, without executing or substituting an LLM."""
    configuration = {"schema": "runtime.resources.v1", "currency": "CNY",
        "prices": {"custom/arithmetic": {"input_per_million": 1, "output_per_million": 5}},
        "limits": {"max_model_requests": 10, "max_total_tokens": 100000,
            "max_parallel_model_calls": 2, "max_elapsed_seconds": 60, "max_cost": None}}
    budget = RunModelBudget(run.root, configuration=configuration)
    reservation = budget.reserve([Message("user", "Authored accounting input; no provider execution.")],
        LLMConfig(provider="custom", model="arithmetic", max_tokens=200, max_retries=2), {})
    budget.settle(reservation, usage={"prompt_tokens": 300, "completion_tokens": 100, "total_tokens": 450} if known else None,
        complete=known, outcome="completed" if known else "failed", sdk_attempts=3 if known else None, attempts_complete=known)
    return budget


def test_resources_include_residual_output_and_distinguish_sdk_attempts(tmp_path: Path) -> None:
    run = RunStore(tmp_path / "runs").create(task="real-budget-arithmetic", project="second-domain")
    _settled_budget(run)
    before = _files(run.root)
    resources = collect_run_results(run)["resources"]
    assert resources["status"] == "recorded" and resources["usage_complete"] is True
    assert resources["input_tokens"] == 300 and resources["billed_output_tokens"] == 150
    assert resources["cost"] == pytest.approx((300 + 150 * 5) / 1000000)
    assert resources["logical_records"] == resources["model_requests"] == 1
    assert resources["observed_sdk_attempts"] == resources["charged_sdk_attempts"] == resources["reserved_sdk_attempts"] == 3
    assert resources["observed_attempts_complete"] is True and resources["calls_with_unknown_attempt_count"] == 0
    assert _files(run.root) == before


def test_unknown_usage_and_attempts_never_present_reservations_as_consumption(tmp_path: Path) -> None:
    run = RunStore(tmp_path / "runs").create(task="unknown-budget-arithmetic", project="second-domain")
    _settled_budget(run, known=False)
    resources = collect_run_results(run)["resources"]
    assert resources["usage_complete"] is False and resources["input_tokens"] is None
    assert resources["billed_output_tokens"] is None and resources["cost"] is None
    assert resources["logical_records"] == 1 and resources["observed_sdk_attempts"] is None
    assert resources["observed_attempts_complete"] is False and resources["calls_with_unknown_attempt_count"] == 1
    assert resources["charged_sdk_attempts"] == resources["reserved_sdk_attempts"] == 3


@pytest.mark.parametrize("known", [True, False])
def test_legacy_token_buckets_do_not_invent_actual_usage(tmp_path: Path, known: bool) -> None:
    run = RunStore(tmp_path / "runs").create(task="legacy-budget-arithmetic", project="second-domain")
    budget = _settled_budget(run, known=known)
    ledger = json.loads(budget.path.read_text())
    row = next(iter(ledger["requests"].values()))
    for name in ("charged_input_tokens", "charged_output_tokens", "observed_attempts", "attempts_complete", "charged_attempts"):
        row.pop(name)
    budget.path.write_text(json.dumps(ledger))
    resources = collect_run_results(run)["resources"]
    assert resources["status"] == "recorded"
    assert resources["billed_output_tokens"] == (150 if known else None)
    assert resources["observed_sdk_attempts"] is None and resources["observed_attempts_complete"] is False


@pytest.mark.parametrize("changes", [
    {"charged_input_tokens": 299, "charged_output_tokens": 151},
    {"charged_cost": -1}, {"observed_attempts": True}, {"observed_attempts": 2},
])
def test_corrupt_budget_projection_does_not_publish_cost_or_tokens(tmp_path: Path, changes: dict[str, Any]) -> None:
    run = RunStore(tmp_path / "runs").create(task="invalid-budget-arithmetic", project="second-domain")
    budget = _settled_budget(run)
    ledger = json.loads(budget.path.read_text())
    next(iter(ledger["requests"].values())).update(changes)
    budget.path.write_text(json.dumps(ledger))
    resources = collect_run_results(run)["resources"]
    assert resources["status"] == "invalid" and resources["cost"] is None and resources["input_tokens"] is None


@pytest.mark.parametrize("text,forbidden", [
    ('{"api_key": "sensitive_key_value"}', "sensitive_key_value"),
    ('{"password": "sensitive_password"}', "sensitive_password"),
    (r'C:\Users\alice\private.csv', "private.csv"),
    (r'\\server\share\private.csv', "private.csv"),
    ('https://host/path?token=secret-query', "secret-query"),
    ('-----BEGIN RSA PRIVATE KEY-----\nsecret-pem\n-----END RSA PRIVATE KEY-----', "secret-pem"),
])
def test_public_projection_removes_secrets_before_json_rendering(text: str, forbidden: str) -> None:
    assert forbidden not in public_text(text)


def test_frozen_contract_is_a_declaration_and_does_not_prove_measurements(tmp_path: Path) -> None:
    code = tmp_path / "private-code"
    code.mkdir()
    (code / "baseline.py").write_text("# Authored source used for hash/preflight tests only.\n")
    project = ProjectContract.model_validate({"project_id": "second_domain", "display_name": "Second domain",
        "paths": {"code": str(code), "data": [], "knowledge": [], "output": str(tmp_path / "outputs")},
        "commands": [{"name": name, "purpose": name, "executable": sys.executable,
            "arguments": ["baseline.py"], "entrypoint_files": ["baseline.py"]} for name in ("check", "train", "evaluate")],
        "metrics": [{"name": "MAE", "unit": "unitless", "direction": "minimize", "target": 0.1, "tolerance": 0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}})
    frozen = freeze_research_task(project, goal="Compare declared MAE", mode="manual", budget=default_research_budget())
    session = create_research_run(Orchestrator(run_store=RunStore(tmp_path / "runs")), name="Bound declared research", contract=frozen)
    before = _files(session.run.root)
    # Offline inspection must survive source removal and must not preflight it.
    (code / "baseline.py").unlink()
    result = collect_run_results(session.run)
    assert result["protocol"]["status"] == "declared_contract"
    assert result["protocol"]["metrics"][0]["name"] == "MAE"
    assert result["protocol"]["data_fingerprints_verified"] is False
    assert result["metrics"] == [] and result["outcome"]["status"] == "unknown"
    assert result["state"]["status"] == "created" and result["state"]["read_only"] is True
    assert str(code) not in json.dumps(result) and sys.executable not in json.dumps(result)
    assert _files(session.run.root) == before
    assert "未启动" in render_files(result)["report.html"].decode()
    assert "尚未记录。" in render_files(result)["report.html"].decode()


def test_read_policy_bounds_actual_files_and_rejects_parent_symlinks(measured_run: RunHandle, tmp_path: Path) -> None:
    policy = load_results_policy()
    policy["max_input_file_bytes"] = 16
    reader = ResultReader(measured_run, policy)
    with pytest.raises(ValueError, match="read policy"):
        reader.read("input/user_request.md")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "document.json").write_text("{}")
    (measured_run.root / "input/symlink-directory").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        reader.read("input/symlink-directory/document.json")


@pytest.mark.parametrize("tamper", ["manifest_type", "traversal", "content", "missing_member"])
def test_download_checks_manifest_and_members_even_with_updated_outer_hash(measured_run: RunHandle, tamper: str) -> None:
    exported = create_results_export(measured_run)
    directory = measured_run.root / "results/exports" / exported["export_id"]
    with zipfile.ZipFile(io.BytesIO((directory / "results.zip").read_bytes())) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    if tamper == "manifest_type":
        manifest["files"][0] = "invalid-file-entry"
    elif tamper == "traversal":
        entry = manifest["files"][0]
        files["../outside"] = files.pop(entry["path"])
        entry["path"] = "../outside"
    elif tamper == "content":
        files["results.json"] += b"changed"
    else:
        files.pop("metrics.csv")
    files["manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    (directory / "results.zip").write_bytes(buffer.getvalue())
    (directory / "archive.sha256").write_text(sha256(buffer.getvalue()))
    with pytest.raises(ValueError):
        read_results_export(measured_run, exported["export_id"])
