"""Real historical files remain viewable without inventing execution state."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient
from filelock import FileLock
import httpx
import pytest

from app.api import dependencies
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.bridge.workflow_service import build_standalone
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps
from app.harness.schema.validator import validate_document
from app.main import create_app
from app.settings import repo_root
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RunStore
from app.storage.run_state_store import RunStateStore
from app.storage.self_evolution_store import create_self_evolution_mutation


def _historical_run(root: Path, entrypoint: str, artifact: Literal["none", "draft", "approved"]) -> tuple[RunStore, RunHandle]:
    store = RunStore(root)
    run = store.create(task="human-authored-history", project="synthetic_regression", entrypoint=entrypoint)
    if artifact != "none":
        document = dumps({"schema": "proposal.v1", "project": run.project, "agent": "idea",
            "research_question": "Can historical documents remain readable?",
            "hypothesis": "Missing state is not execution evidence.",
            "novelty": "A human-authored archival contract, not a research result."},
            "This document is manually authored input, not an Agent or provider response.")
        assert validate_document(document, expected_schema="proposal.v1").valid
        artifacts = ArtifactStore(run)
        draft = artifacts.write(text=document)
        if artifact == "approved":
            artifacts.approve(draft)
    return store, run


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["pipeline", "idea"])
@pytest.mark.parametrize("artifact", ["none", "draft", "approved"])
async def test_missing_state_projection_cannot_be_driven_by_any_service_entry(
    tmp_path: Path, entrypoint: str, artifact: Literal["none", "draft", "approved"],
) -> None:
    store, run = _historical_run(tmp_path / "runs", entrypoint, artifact)
    original_files = _files(run.root)
    orch = Orchestrator(run_store=store)
    session = orch.session(run.run_id)
    assert session.read_only and session.read_only_reason == "missing_persisted_state"
    assert session.graph.state("idea") == {"none": NodeState.PENDING,
        "draft": NodeState.WAITING_REVIEW, "approved": NodeState.DONE}[artifact]
    assert all(node.metadata.get("recovery_source") == "artifacts_only" for node in session.graph.nodes.values())
    graph = session.graph.to_dict()
    assert not orch.start_owned_run(run.run_id)["ok"]
    assert not orch.resume_owned_run(run.run_id)["ok"]
    assert orch.migrate_run_state(run.run_id)["status"] == "missing_persisted_state"
    assert not orch._spawn_owned(session, "start", lambda: orch.run(run.run_id))
    with pytest.raises(ValueError, match="read-only"):
        await orch.run(run.run_id)
    assert (await orch.resume_after_artifact_approval(run_id=run.run_id, agent="idea"))["status"] == "read_only"
    assert (await orch._resume_approved_artifact(session, agent="idea"))["status"] == "read_only"
    assert (await orch.request_artifact_revision(run_id=run.run_id, agent="idea", reason="No replay"))["status"] == "read_only"
    assert (await orch.start_feedback_loop(run_id=run.run_id, diagnosis_version="v1"))["status"] == "read_only"
    assert (await orch.stop_owned_run(run.run_id))["status"] == "not_owned"
    assert orch.owned_tasks.active(run.run_id) is None
    assert session.graph.to_dict() == graph
    assert _files(run.root) == original_files


def test_historical_api_preserves_view_and_download_and_rejects_mutation(tmp_path: Path) -> None:
    store, run = _historical_run(tmp_path / "runs", "pipeline", "draft")
    download = run.subdir("writing") / "historical-note.md"
    download.write_text("Human-authored historical export; no execution claim.\n", encoding="utf-8")
    original_files = _files(run.root)
    dependencies.reset_for_tests()
    dependencies._run_store = store  # Real temporary store; all APIs use the actual bridge.
    try:
        client = TestClient(create_app())
        detail = client.get(f"/api/runs/{run.run_id}")
        assert detail.status_code == 200
        assert detail.json()["read_only"] is True
        assert detail.json()["read_only_reason"] == "missing_persisted_state"
        assert detail.json()["status"] is None
        versions = client.get(f"/api/artifacts/{run.run_id}/idea/idea_proposal/versions")
        assert versions.status_code == 200 and len(versions.json()) == 1
        artifact = client.get(f"/api/artifacts/{run.run_id}/idea/idea_proposal/v1")
        assert artifact.status_code == 200 and artifact.json()["valid"] is True
        assert artifact.json()["text"] == (run.subdir("idea") / "idea_proposal.v1.md").read_text()
        exported = client.get(f"/api/reports/{run.run_id}/files/{download.name}")
        assert exported.status_code == 200 and exported.content == download.read_bytes()
        operations = [
            (f"/api/runs/{run.run_id}/start", {}),
            (f"/api/runs/{run.run_id}/resume", {}),
            (f"/api/runs/{run.run_id}/migrate-state", {}),
            (f"/api/runs/{run.run_id}/feedback-loop/v1/start", {}),
            (f"/api/runs/{run.run_id}/agents/idea/retry", {"reason": "No replay"}),
            (f"/api/artifacts/{run.run_id}/idea/idea_proposal/v1/approve", {}),
            (f"/api/artifacts/{run.run_id}/idea/idea_proposal/v1/edit", {"body": "Do not write"}),
            (f"/api/artifacts/{run.run_id}/idea/idea_proposal/reject", {"reason": "No replay"}),
            (f"/api/artifacts/{run.run_id}/idea/idea_proposal/comment", {"text": "Do not write"}),
            (f"/api/artifacts/{run.run_id}/coding/patch/v1/approve", {}),
            (f"/api/artifacts/{run.run_id}/coding/patch/v1/reject", {"reason": "Do not write"}),
        ]
        for endpoint, payload in operations:
            response = client.post(endpoint, json=payload)
            assert response.status_code == 409, (endpoint, response.text)
        assert dependencies.get_orchestrator().owned_tasks.active(run.run_id) is None
        assert _files(run.root) == original_files
    finally:
        dependencies.reset_for_tests()


def _write_legacy_state(run: RunHandle) -> Path:
    graph = build_standalone("idea")
    graph.restore_state("idea", NodeState.WAITING_REVIEW)
    request = RunRequest(task=run.task, project=run.project, entrypoint="idea", standalone=True)
    path = run.root / "run_state.json"
    path.write_text(json.dumps({"schema": "run_state.v1", "run_id": run.run_id, "project": run.project,
        "task": run.task, "entrypoint": run.entrypoint, "status": "waiting_review", "updated_at": run.created_at,
        "request": asdict(request), "graph": graph.to_dict(), "failed_nodes": [], "failure_summary": None,
        "revision": 3}), encoding="utf-8")
    return path


def test_valid_legacy_state_requires_explicit_api_migration_and_never_autostarts(tmp_path: Path) -> None:
    store, run = _historical_run(tmp_path / "runs", "idea", "draft")
    legacy = _write_legacy_state(run)
    original_legacy = legacy.read_bytes()
    original_document = (run.subdir("idea") / "idea_proposal.v1.md").read_bytes()
    dependencies.reset_for_tests()
    dependencies._run_store = store
    try:
        client = TestClient(create_app())
        detail = client.get(f"/api/runs/{run.run_id}")
        assert detail.status_code == 200
        assert detail.json()["read_only_reason"] == "legacy_state_migration_required"
        assert detail.json()["available_actions"] == ["migrate_state"]
        cached = dependencies.get_orchestrator().session(run.run_id)
        assert cached.read_only
        assert client.post(f"/api/runs/{run.run_id}/start").status_code == 409
        assert client.post(f"/api/artifacts/{run.run_id}/idea/idea_proposal/v1/approve").status_code == 409
        assert legacy.read_bytes() == original_legacy
        with FileLock(run.root / "runtime.driver.lock", timeout=0):
            blocked = client.post(f"/api/runs/{run.run_id}/migrate-state")
            assert blocked.status_code == 409 and blocked.json()["detail"]["status"] == "migration_blocked"
            assert not (run.root / "run_state.authority.json").exists()
            assert legacy.read_bytes() == original_legacy
        result = client.post(f"/api/runs/{run.run_id}/migrate-state")
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "migrated" and result.json()["research_started"] is False
        recovered = dependencies.get_orchestrator().session(run.run_id)
        assert recovered is not cached and not recovered.read_only and recovered.read_only_reason is None
        assert recovered.graph.state("idea") == NodeState.WAITING_REVIEW
        current = RunStateStore(run).load()
        assert current is not None and not current.migration_required
        assert current.graph.to_dict() == recovered.graph.to_dict()
        assert (run.root / "run_state.legacy.json").read_bytes() == original_legacy
        authority = json.loads((run.root / "run_state.authority.json").read_text())
        assert authority["migration"]["sha256"] == hashlib.sha256(original_legacy).hexdigest()
        assert (run.subdir("idea") / "idea_proposal.v1.md").read_bytes() == original_document
        assert not (run.root / "agent_traces").exists()
        assert dependencies.get_orchestrator().owned_tasks.active(run.run_id) is None
        again = client.post(f"/api/runs/{run.run_id}/migrate-state")
        assert again.status_code == 200 and again.json()["status"] == "already_current"
        assert client.get(f"/api/runs/{run.run_id}").json()["available_actions"] == []
    finally:
        dependencies.reset_for_tests()


@pytest.mark.parametrize("source", ["missing", "invalid", "wrong_identity", "service_owned"])
def test_api_migration_rejects_missing_corrupt_or_service_owned_sources(tmp_path: Path, source: str) -> None:
    store, run = _historical_run(tmp_path / "runs", "model_discovery" if source == "service_owned" else "idea", "draft")
    if source in {"invalid", "wrong_identity"}:
        path = _write_legacy_state(run)
        if source == "invalid":
            path.write_text("{not valid JSON", encoding="utf-8")
        else:
            payload = json.loads(path.read_text())
            payload["run_id"] = "another-run"
            path.write_text(json.dumps(payload), encoding="utf-8")
    original = _files(run.root)
    dependencies.reset_for_tests()
    dependencies._run_store = store
    try:
        response = TestClient(create_app()).post(f"/api/runs/{run.run_id}/migrate-state")
        assert response.status_code == 409, response.text
        assert dependencies.get_orchestrator().owned_tasks.active(run.run_id) is None
        assert _files(run.root) == original
    finally:
        dependencies.reset_for_tests()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_pending", [False, True])
async def test_api_replays_only_committed_events_through_real_bus_without_advancing_graph(
    tmp_path: Path, with_pending: bool,
) -> None:
    runs, run = _historical_run(tmp_path / "runs", "idea", "none")
    state_store = RunStateStore(run)
    graph = build_standalone("idea")
    request = asdict(RunRequest(task=run.task, project=run.project, entrypoint="idea", standalone=True))
    revision = state_store.write(graph=graph, request=request, status="created", expected_revision=0)
    if with_pending:
        # A caller-authored persisted state transition tests actual journal
        # delivery; it does not claim that an Agent ran or a model responded.
        graph.transition("idea", NodeState.RUNNING)
        state_store.write(graph=graph, request=request, status="running", expected_revision=revision)
    committed = state_store.load()
    assert committed is not None
    events = state_store.pending_events()
    original_projection = state_store.path.read_bytes()
    bus = InProcessEventBus()
    dependencies.reset_for_tests()
    dependencies._run_store = runs
    dependencies._bus = bus
    try:
        async with bus.subscribe(f"run.{run.run_id}.agent_state") as queue:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
                response = await client.post(f"/api/runs/{run.run_id}/replay-state-events")
                assert response.status_code == 200, response.text
                assert response.json()["delivered_count"] == int(with_pending)
                assert response.json()["pending_count"] == 0
                assert response.json()["research_started"] is False
                if with_pending:
                    assert queue.get_nowait().payload == events[0]
                assert queue.empty()
                again = await client.post(f"/api/runs/{run.run_id}/replay-state-events")
                assert again.status_code == 200 and again.json()["delivered_count"] == 0
                assert queue.empty()
        after = state_store.load()
        assert after is not None and after.graph.to_dict() == committed.graph.to_dict()
        assert after.revision == committed.revision and after.status == committed.status
        assert state_store.path.read_bytes() == original_projection
        assert state_store.pending_events() == []
        orch = dependencies.get_orchestrator()
        assert orch.session(run.run_id).graph.to_dict() == committed.graph.to_dict()
        assert orch.owned_tasks.active(run.run_id) is None and not (run.root / "agent_traces").exists()
    finally:
        dependencies.reset_for_tests()
        await bus.close()


@pytest.mark.parametrize("source", ["missing", "legacy", "missing_database", "corrupt_database"])
def test_api_event_replay_rejects_untrusted_state_without_manufacturing_files(tmp_path: Path, source: str) -> None:
    runs, run = _historical_run(tmp_path / "runs", "idea", "none")
    store = RunStateStore(run)
    if source == "legacy":
        _write_legacy_state(run)
    elif source in {"missing_database", "corrupt_database"}:
        store.write(graph=build_standalone("idea"), request={"task": run.task, "project": run.project,
            "entrypoint": "idea"}, status="created", expected_revision=0)
        if source == "missing_database":
            store.database_path.unlink()
        else:
            store.database_path.write_bytes(b"malformed-database-private-marker")
    original = _files(run.root)
    dependencies.reset_for_tests()
    dependencies._run_store = runs
    try:
        response = TestClient(create_app()).post(f"/api/runs/{run.run_id}/replay-state-events")
        assert response.status_code == 409
        assert response.json()["detail"]["status"] == "state_replay_unavailable"
        assert "Traceback" not in response.text and "private-marker" not in response.text
        assert _files(run.root) == original
        assert dependencies._orchestrator is None
    finally:
        dependencies.reset_for_tests()


@pytest.mark.parametrize("history", ["artifacts_only", "legacy", "service_owned"])
def test_diagnosis_mutation_api_rejects_readonly_history_before_writes_or_evaluation(tmp_path: Path, history: str) -> None:
    store, run = _historical_run(tmp_path / "runs", "model_discovery" if history == "service_owned" else "idea", "draft")
    if history == "legacy":
        _write_legacy_state(run)
    context_file = repo_root() / "backend/app/agents/experiment/prompts/experiment_plan.md"
    original_context = context_file.read_bytes()
    proposed = original_context.decode("utf-8") + "\nHuman-authored historical proposal. No execution claimed.\n"
    # Create a real frozen proposal as historical input. Creation only archives
    # before/after text in this temporary run; it never applies global resources.
    mutation = create_self_evolution_mutation(run=run, lever_id="readonly-history-contract", agent="experiment",
        path="prompts/experiment_plan.md", proposed_content=proposed, rationale="Historical review input only.")
    assert mutation["status"] == "pending_review" and mutation["eval_gate"]["passed"] is False
    original_files = _files(run.root)
    dependencies.reset_for_tests()
    dependencies._run_store = store
    try:
        client = TestClient(create_app())
        detail = client.get(f"/api/runs/{run.run_id}")
        assert detail.status_code == 200 and detail.json()["read_only"] is True
        before = client.get(f"/api/runs/{run.run_id}/self-evolution/mutations")
        assert before.status_code == 200 and before.json()["items"][0]["id"] == mutation["id"]
        base = f"/api/runs/{run.run_id}/self-evolution/mutations"
        created = client.post(base, json={"lever_id": "blocked-new-proposal", "agent": "experiment",
            "path": "prompts/experiment_plan.md", "proposed_content": proposed + "Do not save this.\n",
            "rationale": "Must be rejected before storage."})
        assert created.status_code == 409 and "read-only" in created.text
        for action in ("approve", "evaluate", "rollback", "reject"):
            # A nonexistent suite is a second safeguard against any model call
            # if the admission guard regresses; rejection must happen first.
            payload = {"suite_id": "readonly_guard_must_not_execute"} if action == "evaluate" else {"reviewer_note": "Do not apply"}
            response = client.post(f"{base}/{mutation['id']}/{action}", json=payload)
            assert response.status_code == 409 and "read-only" in response.text, (action, response.text)
        after = client.get(base)
        assert after.status_code == 200 and after.json()["items"] == before.json()["items"]
        assert _files(run.root) == original_files
        assert context_file.read_bytes() == original_context
        assert dependencies.get_orchestrator().owned_tasks.active(run.run_id) is None
    finally:
        dependencies.reset_for_tests()
