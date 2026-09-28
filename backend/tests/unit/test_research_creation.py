"""Real catalog, filesystem, HTTP clients and killed processes; no service substitutes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from collections.abc import Iterator
import json
import os
import secrets
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any

import httpx
import pytest

from app.bridge.orchestrator import Orchestrator
from app.bridge.research_run_service import (
    ResearchRunCreated, lookup_research_creation, save_research_run,
)
from app.storage.research_creation_store import CreationCatalogIntegrityError, ResearchCreationStore
from app.storage.run_store import RunStore
from tests.unit.test_cli_runtime_client import LiveBackend, ROOT, backend as backend
from tests.unit.test_research_run_admission import frozen_input, files


def _http(backend: LiveBackend) -> httpx.Client:
    return httpx.Client(base_url=backend.origin, headers={"X-MARS-Desktop-Token": backend.token},
                        timeout=30, trust_env=False)


def _payload(tmp_path: Path, request_id: str) -> dict[str, Any]:
    return {"name": "Persistent creation", "contract": frozen_input(tmp_path).model_dump(mode="json"), "request_id": request_id}


def test_lookup_missing_is_readonly(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    assert lookup_research_creation(store, "missing-request") is None
    assert list(store.runs_root.iterdir()) == []


def test_actual_http_replays_same_run_with_changed_live_source(backend: LiveBackend, tmp_path: Path) -> None:
    payload = _payload(tmp_path, "replay-source-request")
    with _http(backend) as client:
        created = client.post("/api/research-contracts/runs", json=payload)
        assert created.status_code == 201, created.text
        run = created.json()
        assert run["idempotent"] and run["request_id"] == payload["request_id"]
        assert run["research_started"] is False
        shutil.rmtree(tmp_path / "code")
        replay = client.post("/api/research-contracts/runs", json=payload)
        assert replay.status_code == 201 and replay.json() == run
        root = backend.runtime / "runs" / run["run_id"]
        before = files(backend.runtime / "runs")
        for _ in range(3):
            status = client.get("/api/research-contracts/requests/" + payload["request_id"])
            assert status.status_code == 200 and status.json()["status"] == "created"
            assert status.json()["run"] == run
        assert files(backend.runtime / "runs") == before
        assert root.exists() and not (root / "resources").exists()


def test_actual_concurrent_http_clients_allocate_once(backend: LiveBackend, tmp_path: Path) -> None:
    payload = _payload(tmp_path, "concurrent-http-request")
    def submit(_: int) -> httpx.Response:
        with _http(backend) as client:
            return client.post("/api/research-contracts/runs", json=payload)
    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(submit, range(24)))
    assert {response.status_code for response in responses} <= {201, 202, 409}, [response.text for response in responses]
    for response in responses:
        if response.status_code == 409:
            assert response.json()["detail"]["reason"] == "creation_preflight_in_progress"
    ids = {response.json()["run_id"] for response in responses if response.status_code == 201}
    assert len(ids) == 1
    run_id = ids.pop()
    with _http(backend) as client:
        status = client.get("/api/research-contracts/requests/" + payload["request_id"])
        assert status.json()["status"] == "created" and status.json()["run_id"] == run_id
    store = RunStore(backend.runtime / "runs")
    assert sum(run.task == payload["name"] and run.meta.get("research_task_sha256") == payload["contract"]["task_sha256"] for run in store.list()) == 1


@pytest.mark.parametrize("change", ["name", "contract"])
def test_same_key_different_body_is_conflict(backend: LiveBackend, tmp_path: Path, change: str) -> None:
    payload = _payload(tmp_path, "conflict-request-" + change)
    with _http(backend) as client:
        first = client.post("/api/research-contracts/runs", json=payload)
        assert first.status_code == 201
        if change == "name":
            payload["name"] = "Different research task"
        else:
            from app.bridge.research_contract_service import freeze_research_task
            frozen = frozen_input(tmp_path / "other")
            payload["contract"] = freeze_research_task(frozen.task.project, goal="Another goal", mode="manual", budget=frozen.task.budget).model_dump(mode="json")
        rejected = client.post("/api/research-contracts/runs", json=payload)
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "creation_request_conflict"
        lookup = client.get("/api/research-contracts/requests/" + payload["request_id"])
        assert lookup.json()["run_id"] == first.json()["run_id"]


@pytest.mark.parametrize("damage", ["contract", "authority", "metadata", "deleted", "symlink"])
def test_corrupt_creation_evidence_never_allocates_replacement(tmp_path: Path, damage: str) -> None:
    store = RunStore(tmp_path / "runs")
    frozen = frozen_input(tmp_path)
    owner = Orchestrator(run_store=store)
    first = save_research_run(owner, name="Integrity evidence", contract=frozen, request_id="corrupt-request")
    assert isinstance(first, ResearchRunCreated)
    root = store.runs_root / first.run_id
    path = root / ("input/research_task.v1.json" if damage == "contract" else "run_state.sqlite3" if damage == "authority" else "run_meta.json")
    if damage == "deleted":
        store.trash(first.run_id)
    elif damage == "symlink":
        external = tmp_path / "external-run"
        root.rename(external)
        root.symlink_to(external, target_is_directory=True)
    else:
        path.write_text("corrupt")
    before = files(store.runs_root)
    lookup = lookup_research_creation(store, "corrupt-request")
    assert lookup is not None and lookup.status == "unknown" and lookup.run_id == first.run_id
    replay = save_research_run(Orchestrator(run_store=store), name="Integrity evidence", contract=frozen, request_id="corrupt-request")
    assert replay.status == "unknown"
    # POST may rewrite only its OS lease inode; all stored bytes stay unchanged.
    assert files(store.runs_root) == before


@pytest.mark.parametrize("damage", [
    "drop_identity", "drop_run_state", "drop_state_events", "sequence", "event_id", "revision", "payload", "published",
    "outbox_view", "outbox_column_type",
])
def test_actual_http_incomplete_journal_is_unknown_without_repair_or_reallocation(
    backend: LiveBackend, tmp_path: Path, damage: str,
) -> None:
    payload = _payload(tmp_path, "journal-schema-" + damage)
    with _http(backend) as client:
        created = client.post("/api/research-contracts/runs", json=payload)
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        root = backend.runtime / "runs" / run_id
        with sqlite3.connect(root / "run_state.sqlite3") as connection:
            if damage.startswith("drop_"):
                table = damage.removeprefix("drop_")
                connection.execute(f"DROP TABLE {table}")
            elif damage == "outbox_view":
                connection.execute("ALTER TABLE state_events RENAME TO saved_events")
                connection.execute("CREATE VIEW state_events AS SELECT * FROM saved_events")
            elif damage == "outbox_column_type":
                connection.execute("DROP TABLE state_events")
                connection.execute("CREATE TABLE state_events (sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL, "
                                   "revision INTEGER NOT NULL, payload TEXT NOT NULL, published TEXT NOT NULL)")
            else:
                connection.execute(f"ALTER TABLE state_events RENAME COLUMN {damage} TO damaged_column")
        before = files(backend.runtime / "runs")
        lookup = client.get("/api/research-contracts/requests/" + payload["request_id"])
        assert lookup.status_code == 200
        status = lookup.json()
        assert status["status"] == "unknown" and status["run_id"] == run_id
        assert status["run"] is None and status["admitted"] is None and status["research_started"] is False
        assert status["reason"] == "creation_evidence_unavailable"
        assert files(backend.runtime / "runs") == before
        replay = client.post("/api/research-contracts/runs", json=payload)
        assert replay.status_code == 409 and replay.json()["detail"] == status
        assert files(backend.runtime / "runs") == before


CHILD_CREATION = r'''
import json, sys, time
from pathlib import Path
from app.bridge.orchestrator import Orchestrator
from app.bridge.research_contract_service import FrozenResearchTask
from app.bridge.research_run_service import create_research_run
from app.storage.research_creation_store import ResearchCreationStore
from app.storage.run_store import RunStore
root, input_path, phase, ready = map(Path, sys.argv[1:])
payload = json.loads(input_path.read_text())
store = RunStore(root)
catalog = ResearchCreationStore(root)
catalog.initialize()
with catalog.lease(payload['request_id']) as acquired:
    assert acquired
    intent, fresh = catalog.reserve(payload['request_id'], name=payload['name'], task_sha256=payload['contract']['task_sha256'])
    assert fresh
    def wait():
        ready.write_text('ready')
        while True:
            time.sleep(1)
    if str(phase) == 'intent':
        wait()
    def allocated(run):
        if str(phase) == 'unbound':
            wait()
        catalog.bind_run(payload['request_id'], run.run_id)
        if str(phase) == 'allocated':
            wait()
    create_research_run(Orchestrator(run_store=store), name=payload['name'],
        contract=FrozenResearchTask.model_validate(payload['contract']), on_run_allocated=allocated)
    # Deliberately kill after the real authority commit but before catalog finish.
    wait()
'''


@contextmanager
def interrupted_creation(root: Path, tmp_path: Path, payload: dict[str, Any], phase: str) -> Iterator[subprocess.Popen[bytes]]:
    input_path, ready = tmp_path / "request.json", tmp_path / "ready"
    input_path.write_text(json.dumps(payload))
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "backend"), "PYTHONNOUSERSITE": "1",
           "MARS_RUNTIME_MODE": "development", "MARS_ENABLE_NETWORK_TOOLS": "false"}
    with (tmp_path / "child.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-c", CHILD_CREATION, str(root), str(input_path), phase, str(ready)],
                                   env=env, cwd=ROOT, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert process.poll() is None, (tmp_path / "child.log").read_text()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize("phase", ["intent", "unbound", "allocated", "committed"])
def test_real_process_death_http_lookup_and_replay(backend: LiveBackend, tmp_path: Path, phase: str) -> None:
    payload = _payload(tmp_path, "killed-request-" + phase)
    store = RunStore(backend.runtime / "runs")
    route = "/api/research-contracts/requests/" + payload["request_id"]
    with interrupted_creation(store.runs_root, tmp_path, payload, phase) as process, _http(backend) as client:
        pending = client.get(route)
        assert pending.status_code == 200 and pending.json()["status"] == "pending"
        duplicate = client.post("/api/research-contracts/runs", json=payload)
        assert duplicate.status_code == 202 and duplicate.json()["status"] == "pending"
        process.kill()
        process.wait(timeout=5)
        before = files(store.runs_root)
        lookup = client.get(route)
        expected = "created" if phase == "committed" else "unknown"
        assert lookup.status_code == 200 and lookup.json()["status"] == expected, lookup.text
        assert files(store.runs_root) == before
        replay = client.post("/api/research-contracts/runs", json=payload)
        assert replay.status_code == (201 if phase == "committed" else 409), replay.text
        assert files(store.runs_root) == before
        if phase in {"intent", "unbound"}:
            assert lookup.json()["run_id"] is None
        else:
            assert lookup.json()["run_id"] is not None
        if phase == "committed":
            assert replay.json() == lookup.json()["run"]


def test_missing_catalog_is_not_reinitialized(tmp_path: Path) -> None:
    catalog = ResearchCreationStore(tmp_path)
    catalog.initialize()
    with catalog.lease("lost-catalog-request") as held:
        assert held
        catalog.reserve("lost-catalog-request", name="Saved", task_sha256="1" * 64)
    catalog.path.unlink()
    with pytest.raises(CreationCatalogIntegrityError):
        catalog.initialize()
    assert not catalog.path.exists()


def test_legacy_clients_remain_explicitly_nonidempotent(backend: LiveBackend, tmp_path: Path) -> None:
    payload = _payload(tmp_path, "not-used-request")
    payload.pop("request_id")
    with _http(backend) as client:
        first = client.post("/api/research-contracts/runs", json=payload)
        second = client.post("/api/research-contracts/runs", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["run_id"] != second.json()["run_id"]
    assert first.json()["request_id"] is None and first.json()["idempotent"] is False


@contextmanager
def fresh_backend(runtime: Path, scratch: Path) -> Iterator[LiveBackend]:
    scratch.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.set_inheritable(True)
        token = secrets.token_urlsafe(32)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "backend"), "PYTHONNOUSERSITE": "1",
            "MARS_RUNTIME_ROOT": str(runtime), "MARS_RUNTIME_MODE": "development", "MARS_DISTRIBUTION": "v30-core",
            "MARS_DESKTOP_SESSION_TOKEN": token, "MARS_CORS_ORIGINS": "http://127.0.0.1",
            "MARS_ENABLE_NETWORK_TOOLS": "false", "BACKEND_PORT": str(port)}
        startup = "import socket,sys,uvicorn; listener=socket.socket(fileno=int(sys.argv[1])); uvicorn.Server(uvicorn.Config('app.main:create_app',factory=True,log_level='warning',access_log=False)).run(sockets=[listener])"
        with (scratch / "backend.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-c", startup, str(listener.fileno())], pass_fds=(listener.fileno(),),
                env=env, cwd=scratch, stdout=log, stderr=log)
    backend = LiveBackend(f"http://127.0.0.1:{port}", token, runtime)
    try:
        deadline = time.monotonic() + 30
        with _http(backend) as client:
            while True:
                assert process.poll() is None, (scratch / "backend.log").read_text()
                try:
                    health = client.get("/health", timeout=0.2)
                    assert health.status_code < 500, "Real backend failed: " + (scratch / "backend.log").read_text()[-3000:]
                    if health.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.03)
        yield backend
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX inherited listener process fixture; Windows process fixture remains separate")
def test_actual_http_lookup_survives_backend_restart(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    for line in (ROOT / "scripts/release/runtime_assets.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            path = runtime / line
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / line, path)
    payload = _payload(tmp_path, "backend-restart-request")
    with fresh_backend(runtime, tmp_path / "first") as server, _http(server) as client:
        response = client.post("/api/research-contracts/runs", json=payload)
        assert response.status_code == 201, response.text
        original = response.json()
    before = files(runtime / "runs")
    with fresh_backend(runtime, tmp_path / "second") as server, _http(server) as client:
        lookup = client.get("/api/research-contracts/requests/" + payload["request_id"])
        assert lookup.status_code == 200 and lookup.json()["run"] == original
        replay = client.post("/api/research-contracts/runs", json=payload)
        assert replay.status_code == 201 and replay.json() == original
    assert files(runtime / "runs") == before


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_preallocation_rejection_is_durable_and_explicit(backend: LiveBackend, tmp_path: Path, damage: str) -> None:
    payload = _payload(tmp_path, "preflight-rejected-" + damage)
    baseline = tmp_path / "code/baseline.py"
    original = baseline.read_bytes()
    if damage == "changed":
        baseline.write_text("# changed after freezing")
    else:
        baseline.unlink()
    store = RunStore(backend.runtime / "runs")
    before = {run.run_id for run in store.list()}
    with _http(backend) as client:
        rejected = client.post("/api/research-contracts/runs", json=payload)
        assert rejected.status_code == 422, rejected.text
        evidence = rejected.json()["detail"]
        assert evidence == {"request_id": payload["request_id"], "task_sha256": payload["contract"]["task_sha256"], "status": "rejected", "admitted": False,
            "run_id": None, "research_started": False, "run": None, "reason": "creation_preflight_rejected"}
        baseline.write_bytes(original)
        # A now-valid live input does not silently reset an old rejected key.
        repeated = client.post("/api/research-contracts/runs", json=payload)
        assert repeated.status_code == 422 and repeated.json()["detail"] == evidence
        lookup = client.get("/api/research-contracts/requests/" + payload["request_id"])
        assert lookup.status_code == 200 and lookup.json() == evidence
        assert {run.run_id for run in store.list()} == before
        # An explicit new request is a new action, safely admitted after repair.
        payload["request_id"] += "-new"
        created = client.post("/api/research-contracts/runs", json=payload)
        assert created.status_code == 201 and created.json()["research_started"] is False


def test_http_lookup_unknown_id_and_auth_never_mutate_catalog(backend: LiveBackend) -> None:
    before = files(backend.runtime / "runs")
    route = "/api/research-contracts/requests/unregistered-key"
    with _http(backend) as client:
        unknown = client.get(route)
        assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "creation_request_not_found"
    with httpx.Client(base_url=backend.origin, trust_env=False) as client:
        assert client.get(route).status_code == 401
    assert files(backend.runtime / "runs") == before


@pytest.mark.parametrize("path", ["catalog.sqlite3", ".initialize.lock", "leases"])
def test_catalog_symlink_is_rejected_without_external_changes(tmp_path: Path, path: str) -> None:
    catalog = ResearchCreationStore(tmp_path)
    catalog.initialize()
    target = catalog.root / path
    external = tmp_path / "external"
    target.rename(external)
    target.symlink_to(external, target_is_directory=external.is_dir())
    before = files(tmp_path)
    with pytest.raises(CreationCatalogIntegrityError):
        catalog.initialize()
    with pytest.raises(CreationCatalogIntegrityError):
        catalog.get("symlink-request")
    assert files(tmp_path) == before


def test_real_http_timeout_can_lookup_completed_original_request(backend: LiveBackend, tmp_path: Path) -> None:
    payload = _payload(tmp_path, "actual-http-timeout-request")
    catalog = ResearchCreationStore(backend.runtime / "runs")
    catalog.initialize()
    # A real SQLite writer holds the production request briefly. The client
    # disconnects after sending its body; no route or service is replaced.
    with catalog.connection(writable=True) as connection, _http(backend) as client:
        connection.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(httpx.ReadTimeout):
                client.post("/api/research-contracts/runs", json=payload, timeout=0.05)
        finally:
            connection.rollback()
        deadline = time.monotonic() + 10
        while True:
            lookup = client.get("/api/research-contracts/requests/" + payload["request_id"])
            if lookup.status_code == 200 and lookup.json()["status"] == "created":
                break
            assert lookup.status_code in {200, 404}, lookup.text
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert lookup.json()["task_sha256"] == payload["contract"]["task_sha256"]
        repeated = client.post("/api/research-contracts/runs", json=payload)
        assert repeated.status_code == 201 and repeated.json() == lookup.json()["run"]



def test_initialization_lock_hardlink_is_rejected_before_external_truncation(tmp_path: Path) -> None:
    catalog = ResearchCreationStore(tmp_path)
    catalog.root.mkdir()
    external = tmp_path / "external-sentinel.txt"
    external.write_text("REVIEW_SENTINEL_MUST_REMAIN")
    (catalog.root / ".initialize.lock").hardlink_to(external)
    before = external.read_bytes()
    with pytest.raises(CreationCatalogIntegrityError, match="initialization lease"):
        catalog.initialize()
    assert external.read_bytes() == before
    assert not catalog.path.exists()


@pytest.mark.parametrize("request_id", ["bad!", "short", "../outside"])
def test_corrupt_decoded_request_id_has_catalog_integrity_error(request_id: str) -> None:
    from app.storage.research_creation_store import binding_sha256
    with pytest.raises(CreationCatalogIntegrityError):
        ResearchCreationStore._decode((request_id, "name", "1" * 64,
            binding_sha256("name", "1" * 64), None, "pending"))
