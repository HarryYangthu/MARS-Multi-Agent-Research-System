"""Real run storage, SQLite recovery and production API; no research substitutes."""
from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
import sys
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.api import dependencies
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.bridge.research_contract_service import (
    FrozenResearchTask, ResearchContractIntegrityError, contract_sha256, default_research_budget,
    freeze_research_task, validate_frozen_research_task,
)
from app.bridge.research_run_service import (
    CONTRACT_FILE, CONTRACT_HASH_KEY, create_research_run, load_run_research_contract,
    persist_run_research_contract, research_execution_admission,
)
from app.harness.runtime.research_contract import ProjectContract, ResearchBudget, ResearchTaskContract
from app.harness.runtime.state_machine import NodeState
from app.main import create_app
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


def frozen_input(root: Path) -> FrozenResearchTask:
    code = root / "code"
    code.mkdir(parents=True)
    (code / "baseline.py").write_text("# Human-authored protected source, no measurement.\n")
    (code / "train.py").write_text("raise RuntimeError('contract admission must never execute research')\n")
    project = ProjectContract.model_validate({"project_id": "unregistered_example", "display_name": "Generic project",
        "paths": {"code": str(code), "data": [], "knowledge": [], "output": str(root / "results")},
        "commands": [{"name": name, "purpose": name, "executable": sys.executable,
                      "arguments": ["train.py"], "entrypoint_files": ["train.py"]}
                     for name in ("check", "train", "evaluate")],
        "metrics": [{"name": "MSE", "unit": "unitless", "direction": "minimize", "target": 0, "tolerance": 0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}})
    return freeze_research_task(project, goal="Compare measured baseline and candidate", mode="manual", budget=default_research_budget())


def files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def actual_api(tmp_path: Path) -> Iterator[tuple[TestClient, RunStore]]:
    dependencies.reset_for_tests()
    store = RunStore(tmp_path / "runs")
    dependencies._run_store = store  # Real storage injection; no runtime/service replacement.
    client = TestClient(create_app())
    try:
        yield client, store
    finally:
        client.close()
        dependencies.reset_for_tests()


def test_api_creates_same_owner_persisted_run_and_reports_execution_blocked(
    tmp_path: Path, actual_api: tuple[TestClient, RunStore],
) -> None:
    client, store = actual_api
    frozen = frozen_input(tmp_path)
    prepared = client.post("/api/research-contracts/prepare", json={
        "project": frozen.task.project.model_dump(mode="json"), "goal": frozen.task.goal,
        "mode": frozen.task.mode, "budget": frozen.task.budget.model_dump(mode="json")})
    assert prepared.status_code == 200 and prepared.json() == frozen.model_dump(mode="json")
    assert store.list() == []
    result = client.post("/api/research-contracts/runs", json={"name": "Actual admitted task", "contract": prepared.json()})
    assert result.status_code == 201, result.text
    response = result.json()
    assert response["status"] == "created" and response["research_started"] is False
    assert response["task_sha256"] == frozen.task_sha256
    admission = response["execution_admission"]
    assert admission["ready"] is False and admission["enforced_budget_fields"] == []
    assert {item["code"] for item in admission["blockers"]} == {
        "research_budget_enforcement_pending", "project_execution_adapter_pending"}
    assert set(admission["blockers"][1]["fields"]) == {"budget." + name for name in ResearchBudget.model_fields}
    run_id = response["run_id"]
    run = store.get(run_id)
    assert run is not None and len(store.list()) == 1
    snapshot = RunStateStore(run).load()
    assert snapshot is not None and snapshot.status == "created"
    assert snapshot.request["extra"][CONTRACT_HASH_KEY] == frozen.task_sha256
    assert run.meta[CONTRACT_HASH_KEY] == frozen.task_sha256
    assert load_run_research_contract(run, snapshot.request["extra"]) == frozen
    assert json.loads((run.subdir("input") / CONTRACT_FILE).read_text()) == prepared.json()
    assert all(value == NodeState.PENDING for value in snapshot.graph.all_states().values())
    before = files(run.root)
    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["research_task_sha256"] == frozen.task_sha256
    assert detail.json()["execution_admission"] == admission
    assert detail.json()["read_only_reason"] == "research_contract_execution_pending"
    assert any(item["run_id"] == run_id for item in client.get("/api/runs").json())
    for action in ("start", "resume"):
        denied = client.post(f"/api/runs/{run_id}/{action}")
        assert denied.status_code == 409
        assert denied.json()["detail"]["status"] == "contract_execution_blocked"
        assert denied.json()["detail"]["research_started"] is False
    # Existing review/feedback APIs may not bypass the same contract boundary.
    for route, payload in [
        (f"/api/runs/{run_id}/agents/idea/retry", {"reason": "Must remain blocked"}),
        (f"/api/runs/{run_id}/feedback-loop/v1/start", {}),
        (f"/api/artifacts/{run_id}/idea/idea_proposal/comment", {"text": "Must not write"}),
    ]:
        assert client.post(route, json=payload).status_code == 409
    assert dependencies.get_orchestrator().owned_tasks.active(run_id) is None
    assert files(run.root) == before and not (tmp_path / "results").exists()
    assert not (run.root / "agent_traces").exists() and not (run.root / "resources").exists()


@pytest.mark.asyncio
async def test_recovered_contract_stays_blocked_through_direct_orchestrator_entries(tmp_path: Path) -> None:
    frozen = frozen_input(tmp_path)
    store = RunStore(tmp_path / "runs")
    created = create_research_run(Orchestrator(run_store=store), name="Persistent pending task", contract=frozen)
    before = files(created.run.root)
    recovered_owner = Orchestrator(run_store=store)
    session = recovered_owner.session(created.run.run_id)
    assert session.read_only and session.read_only_reason == "research_contract_execution_pending"
    assert load_run_research_contract(session.run, session.request.extra) == frozen
    assert recovered_owner.start_owned_run(session.run.run_id)["status"] == "contract_execution_blocked"
    assert recovered_owner.resume_owned_run(session.run.run_id)["status"] == "contract_execution_blocked"
    with pytest.raises(ValueError, match="contract execution is blocked"):
        await recovered_owner.run(session.run.run_id)
    assert not recovered_owner._spawn_owned(session, "start", lambda: recovered_owner.run(session.run.run_id))
    assert (await recovered_owner.resume_after_artifact_approval(run_id=session.run.run_id, agent="idea"))["status"] == "read_only"
    assert (await recovered_owner.request_artifact_revision(run_id=session.run.run_id, agent="idea", reason="No execution"))["status"] == "read_only"
    assert (await recovered_owner.start_feedback_loop(run_id=session.run.run_id, diagnosis_version="v1"))["status"] == "read_only"
    assert files(session.run.root) == before


@pytest.mark.parametrize("tamper", ["task_hash", "project_hash", "missing_fingerprint", "duplicate_fingerprint", "budget"])
def test_invalid_frozen_contents_are_rejected_before_run_creation(
    tmp_path: Path, actual_api: tuple[TestClient, RunStore], tamper: str,
) -> None:
    client, store = actual_api
    raw = frozen_input(tmp_path).model_dump(mode="json")
    if tamper == "task_hash":
        raw["task_sha256"] = "0" * 64
    elif tamper == "budget":
        raw["task"]["budget"]["tool_executions"] += 1
    else:
        if tamper == "project_hash":
            raw["task"]["project_sha256"] = "0" * 64
        elif tamper == "missing_fingerprint":
            raw["task"]["input_fingerprints"].pop()
        else:
            raw["task"]["input_fingerprints"].append(raw["task"]["input_fingerprints"][0])
        raw["task_sha256"] = contract_sha256(ResearchTaskContract.model_validate(raw["task"]))
    response = client.post("/api/research-contracts/runs", json={"name": "Rejected", "contract": raw})
    assert response.status_code == 422 and store.list() == []
    assert not list(store.runs_root.iterdir())


@pytest.mark.parametrize("change", ["content", "missing", "symlink"])
def test_changed_declared_live_files_prevent_run_creation(
    tmp_path: Path, actual_api: tuple[TestClient, RunStore], change: str,
) -> None:
    client, store = actual_api
    frozen = frozen_input(tmp_path)
    baseline = Path(frozen.task.project.paths.code) / "baseline.py"
    if change == "content":
        baseline.write_text("# Changed after freeze\n")
    else:
        original = baseline.read_bytes()
        baseline.unlink()
        if change == "symlink":
            external = tmp_path / "external.py"
            external.write_bytes(original)
            baseline.symlink_to(external)
    response = client.post("/api/research-contracts/runs", json={"name": "Stale", "contract": frozen.model_dump(mode="json")})
    assert response.status_code == (409 if change == "content" else 422)
    assert store.list() == [] and not list(store.runs_root.iterdir())


@pytest.mark.parametrize("damage", ["missing", "json", "task", "metadata", "options", "symlink", "input_symlink", "run_symlink"])
def test_corrupted_saved_contract_recovers_readonly_and_never_falls_back_to_unbound_execution(tmp_path: Path, damage: str) -> None:
    frozen = frozen_input(tmp_path)
    store = RunStore(tmp_path / "runs")
    session = create_research_run(Orchestrator(run_store=store), name="Integrity check", contract=frozen)
    run = session.run
    target = run.subdir("input") / CONTRACT_FILE
    if damage == "missing":
        target.unlink()
    elif damage == "json":
        target.write_text("{")
    elif damage == "task":
        raw = json.loads(target.read_text())
        raw["task"]["goal"] = "Changed after creation"
        target.write_text(json.dumps(raw))
    elif damage in {"metadata", "options"}:
        path = run.root / "run_meta.json" if damage == "metadata" else run.subdir("input") / "run_request_options.v1.json"
        raw = json.loads(path.read_text())
        (raw if damage == "metadata" else raw["extra"])[CONTRACT_HASH_KEY] = "0" * 64
        path.write_text(json.dumps(raw))
    elif damage == "symlink":
        external = tmp_path / "external-contract.json"
        target.rename(external)
        target.symlink_to(external)
    else:
        source = run.subdir("input") if damage == "input_symlink" else run.root
        external = tmp_path / ("external-input" if damage == "input_symlink" else "external-run")
        source.rename(external)
        source.symlink_to(external, target_is_directory=True)
    # No unsafe input path may reach state loading's approval repair side effect.
    before = files(run.root)
    owner = Orchestrator(run_store=store)
    if damage in {"symlink", "input_symlink", "run_symlink"}:
        with pytest.raises(ResearchContractIntegrityError):
            owner.session(run.run_id)
        assert files(run.root) == before
        assert owner.owned_tasks.active(run.run_id) is None
        return
    recovered = owner.session(run.run_id)
    assert recovered.read_only and recovered.read_only_reason == "research_contract_integrity_error"
    result = owner.start_owned_run(run.run_id)
    assert result["status"] == "contract_execution_blocked" and result["research_started"] is False
    with pytest.raises(ResearchContractIntegrityError):
        load_run_research_contract(recovered.run, recovered.request.extra)
    assert files(run.root) == before
    assert owner.owned_tasks.active(run.run_id) is None


def test_saved_contract_inspection_is_independent_of_live_source_and_is_write_once(tmp_path: Path) -> None:
    frozen = frozen_input(tmp_path)
    session = create_research_run(Orchestrator(run_store=RunStore(tmp_path / "runs")), name="Inspect evidence", contract=frozen)
    path = session.run.subdir("input") / CONTRACT_FILE
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        persist_run_research_contract(session.run, frozen)
    assert path.read_bytes() == original
    (Path(frozen.task.project.paths.code) / "baseline.py").unlink()
    assert load_run_research_contract(session.run, session.request.extra) == frozen
    assert research_execution_admission(session.run, session.request.extra) is not None


def test_manual_extra_hash_cannot_create_an_unbound_contract_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with pytest.raises(ValueError, match="complete frozen contract"):
        Orchestrator(run_store=store).create_session(RunRequest(task="Invalid", project="anything",
            extra={CONTRACT_HASH_KEY: "0" * 64}))
    assert store.list() == []


def test_contract_validator_revalidates_model_copy_and_legacy_run_has_no_binding(tmp_path: Path) -> None:
    frozen = frozen_input(tmp_path)
    with pytest.raises(ResearchContractIntegrityError):
        validate_frozen_research_task(frozen.model_copy(update={"task_sha256": "0" * 64}))
    run = RunStore(tmp_path / "runs").create(task="Actual legacy metadata", project="synthetic_regression")
    assert load_run_research_contract(run) is None and research_execution_admission(run) is None


@pytest.mark.parametrize("fresh_owner", [False, True])
def test_contract_damage_is_visible_on_actual_api_without_live_source_or_model_reads(
    tmp_path: Path, actual_api: tuple[TestClient, RunStore], fresh_owner: bool,
) -> None:
    client, store = actual_api
    session = create_research_run(dependencies.get_orchestrator(), name="API integrity", contract=frozen_input(tmp_path))
    (session.run.subdir("input") / CONTRACT_FILE).unlink()
    if fresh_owner:
        dependencies.reset_for_tests()
        dependencies._run_store = store
    original = files(session.run.root)
    detail = client.get(f"/api/runs/{session.run.run_id}")
    assert detail.status_code == 200
    assert detail.json()["read_only_reason"] == "research_contract_integrity_error"
    for action in ("start", "resume"):
        denied = client.post(f"/api/runs/{session.run.run_id}/{action}")
        assert denied.status_code == 409 and denied.json()["detail"]["status"] == "contract_execution_blocked"
    assert files(session.run.root) == original


@pytest.mark.parametrize("fresh_owner", [False, True])
@pytest.mark.parametrize("state_damage", ["missing", "corrupt"])
def test_actual_get_api_rejects_unavailable_authority_without_500_or_inferred_success(
    tmp_path: Path, actual_api: tuple[TestClient, RunStore], fresh_owner: bool, state_damage: str,
) -> None:
    client, store = actual_api
    session = create_research_run(dependencies.get_orchestrator(), name="Unavailable authority", contract=frozen_input(tmp_path))
    state = session.run.root / "run_state.sqlite3"
    if state_damage == "missing":
        state.unlink()
    else:
        state.write_bytes(b"This is invalid SQLite; do not infer an executable graph")
    if fresh_owner:
        dependencies.reset_for_tests()
        dependencies._run_store = store
    original = files(session.run.root)
    detail = client.get(f"/api/runs/{session.run.run_id}")
    assert detail.status_code == 409
    assert detail.json()["detail"]["status"] == "run_state_unavailable"
    assert detail.json()["detail"]["research_started"] is False
    assert detail.json()["detail"]["error_type"] == "RunStateIntegrityError"
    assert detail.json()["detail"]["control"]["owned_task_active"] is False
    assert files(session.run.root) == original
