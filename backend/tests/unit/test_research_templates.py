"""Real saved contracts/SQLite/API and filesystem; no execution substitutes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.bridge.orchestrator import Orchestrator, RunRequest
from app.bridge.research_contract_service import freeze_research_task, preflight_project
from app.bridge.research_run_service import create_research_run
from app.bridge.research_templates import (
    TemplateMissing, TemplateUnavailable, list_research_settings, load_research_settings, template_limits,
)
from app.harness.runtime.research_contract import ProjectContract
from app.storage.run_store import RunStore
from tests.unit.test_research_run_admission import actual_api as actual_api, frozen_input


def _files(root: Path) -> dict[str, tuple[str, int]]:
    return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def test_reuse_preserves_all_project_budget_mode_fields_and_never_reuses_old_observations(tmp_path: Path) -> None:
    original = frozen_input(tmp_path)
    declaration = original.task.project.model_dump(mode="json")
    declaration["commands"].append({**declaration["commands"][1], "name": "extra_train", "arguments": ["train.py", "", "two\nlines", " leading", "trailing "]})
    declaration["protected_paths"] = ["reference"]
    declaration["metrics"].append({"name": "other", "unit": "unitless", "direction": "maximize", "target": 2.0, "tolerance": 0.1})
    frozen = freeze_research_task(ProjectContract.model_validate(declaration), goal=original.task.goal,
        mode=original.task.mode, budget=original.task.budget)
    store = RunStore(tmp_path / "runs")
    run = create_research_run(Orchestrator(run_store=store), name="Original task", contract=frozen).run
    before = _files(tmp_path)
    saved = load_research_settings(store, run.run_id)
    assert saved.project == frozen.task.project
    assert saved.budget == frozen.task.budget and len(saved.budget.model_fields) == 23
    assert saved.mode == frozen.task.mode
    assert saved.requires_preflight and saved.research_started is False
    assert set(saved.model_dump()) == {"schema_id", "source_run_id", "project", "budget", "mode", "requires_preflight", "research_started"}
    page = list_research_settings(store)
    assert len(page.items) == 1 and page.items[0].run_id == run.run_id
    assert _files(tmp_path) == before


def test_source_moved_can_load_but_actual_preflight_fails(tmp_path: Path) -> None:
    frozen = frozen_input(tmp_path)
    store = RunStore(tmp_path / "runs")
    run = create_research_run(Orchestrator(run_store=store), name="Source moved", contract=frozen).run
    shutil.move(tmp_path / "code", tmp_path / "moved-code")
    before = _files(tmp_path)
    settings = load_research_settings(store, run.run_id)
    assert settings.project == frozen.task.project
    report = preflight_project(settings.project)
    assert not report.ready and any(issue.code == "missing_directory" for issue in report.issues)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("damage", ["missing_db", "bad_db", "outbox", "identity", "request", "frozen", "metadata", "authority", "symlink", "hardlink"])
def test_invalid_authority_cannot_reuse_or_repair(tmp_path: Path, damage: str) -> None:
    frozen = frozen_input(tmp_path)
    store = RunStore(tmp_path / "runs")
    run = create_research_run(Orchestrator(run_store=store), name="Damaged evidence", contract=frozen).run
    database = run.root / "run_state.sqlite3"
    if damage == "missing_db":
        database.unlink()
    elif damage == "bad_db":
        database.write_bytes(b"not SQLite")
    elif damage in {"outbox", "identity", "request"}:
        with sqlite3.connect(database) as connection:
            if damage == "outbox":
                connection.execute("DROP TABLE state_events")
            elif damage == "identity":
                connection.execute("UPDATE identity SET run_id='another-run'")
            else:
                raw = json.loads(connection.execute("SELECT payload FROM run_state").fetchone()[0])
                raw["request"]["extra"]["research_task_sha256"] = "0" * 64
                connection.execute("UPDATE run_state SET payload=?", (json.dumps(raw),))
    else:
        name = {"frozen": "input/research_task.v1.json", "metadata": "run_meta.json", "authority": "run_state.authority.json", "symlink": "input/research_task.v1.json", "hardlink": "input/research_task.v1.json"}[damage]
        path = run.root / name
        if damage in {"symlink", "hardlink"}:
            external = tmp_path / "foreign.json"
            external.write_bytes(path.read_bytes())
            path.unlink()
            if damage == "symlink":
                path.symlink_to(external)
            else:
                os.link(external, path)
        else:
            path.write_text("{}")
    before = _files(tmp_path)
    with pytest.raises(TemplateUnavailable):
        load_research_settings(store, run.run_id)
    page = list_research_settings(store)
    assert page.items == () and page.unavailable_count == 1
    assert _files(tmp_path) == before


def test_legacy_missing_and_unsafe_identifiers_create_no_state(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = Orchestrator(run_store=store).create_session(RunRequest(task="Legacy", project="synthetic_regression", entrypoint="idea")).run
    before = _files(tmp_path)
    for name in (run.run_id, "missing-run"):
        with pytest.raises(TemplateMissing):
            load_research_settings(store, name)
    for name in ("../escape", ".", "..", "x/y", "x%2Fy"):
        with pytest.raises(ValueError):
            load_research_settings(store, name)
    assert list_research_settings(store).items == ()
    assert _files(tmp_path) == before


def test_list_is_bounded_and_cursor_preserves_distinct_same_project_runs(tmp_path: Path) -> None:
    frozen = frozen_input(tmp_path)
    store = RunStore(tmp_path / "runs")
    owner = Orchestrator(run_store=store)
    ids = {create_research_run(owner, name="Same project settings", contract=frozen).run.run_id for _ in range(3)}
    before = _files(tmp_path)
    first = list_research_settings(store, limit=2)
    assert len(first.items) == 2 and first.next_cursor is not None
    second = list_research_settings(store, limit=2, cursor=first.next_cursor)
    assert len(second.items) == 1 and second.next_cursor is None
    assert {item.run_id for item in (*first.items, *second.items)} == ids
    for size in (0, -1, template_limits().max_page_size + 1):
        with pytest.raises(ValueError):
            list_research_settings(store, limit=size)
    assert _files(tmp_path) == before


def test_bounded_record_and_sql_payload_refuse_without_truncating(tmp_path: Path) -> None:
    frozen = frozen_input(tmp_path)
    store = RunStore(tmp_path / "runs")
    run = create_research_run(Orchestrator(run_store=store), name="Bounded settings", contract=frozen).run
    limits = template_limits().model_copy(update={"max_record_bytes": 8})
    with pytest.raises(TemplateUnavailable):
        load_research_settings(store, run.run_id, limits=limits)
    limits = template_limits()
    with sqlite3.connect(run.root / "run_state.sqlite3") as connection:
        connection.execute("UPDATE run_state SET payload=?", ("x" * (limits.max_record_bytes + 1),))
    before = _files(tmp_path)
    with pytest.raises(TemplateUnavailable):
        load_research_settings(store, run.run_id)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("active", [False, True])
def test_real_wal_is_rejected_before_sqlite_can_create_or_change_sidecars(tmp_path: Path, active: bool) -> None:
    frozen = frozen_input(tmp_path)
    store = RunStore(tmp_path / "runs")
    run = create_research_run(Orchestrator(run_store=store), name="WAL remains unsupported", contract=frozen).run
    connection = sqlite3.connect(run.root / "run_state.sqlite3")
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    if active:
        connection.execute("UPDATE identity SET run_id=run_id")
        connection.commit()
    else:
        connection.close()
        assert not (run.root / "run_state.sqlite3-wal").exists()
    before = _files(tmp_path)
    try:
        with pytest.raises(TemplateUnavailable):
            load_research_settings(store, run.run_id)
        assert list_research_settings(store).unavailable_count == 1
        assert _files(tmp_path) == before
    finally:
        if active:
            connection.close()


def test_real_api_load_then_repreflight_refreeze_and_explicit_save_creates_new_task(
    tmp_path: Path, actual_api: tuple[TestClient, RunStore],
) -> None:
    client, store = actual_api
    frozen = frozen_input(tmp_path)
    created = client.post("/api/research-contracts/runs", json={"name": "Source", "contract": frozen.model_dump(mode="json")})
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    before = _files(store.runs_root)
    response = client.get(f"/api/research-templates/{run_id}")
    assert response.status_code == 200
    settings: dict[str, Any] = response.json()
    assert client.get("/api/research-templates").json()["items"][0]["run_id"] == run_id
    assert len(store.list()) == 1 and _files(store.runs_root) == before
    (tmp_path / "code/train.py").write_text("raise RuntimeError('new declaration must not execute this')\n")
    assert client.post("/api/research-contracts/preflight", json=settings["project"]).json()["ready"]
    fresh = client.post("/api/research-contracts/prepare", json={"project": settings["project"], "budget": settings["budget"], "mode": settings["mode"], "goal": "A newly stated goal"})
    assert fresh.status_code == 200 and fresh.json()["task_sha256"] != frozen.task_sha256
    assert fresh.json()["task"]["input_fingerprints"] != frozen.model_dump(mode="json")["task"]["input_fingerprints"]
    assert _files(store.runs_root) == before
    new = client.post("/api/research-contracts/runs", json={"name": "New research", "contract": fresh.json(), "request_id": "explicit-template-reuse"})
    assert new.status_code == 201 and new.json()["run_id"] != run_id
    assert new.json()["research_started"] is False
    assert client.get("/api/research-templates/missing-run").status_code == 404
    assert client.get("/api/research-templates", params={"limit": 0}).status_code == 422
    original_root = store.runs_root / run_id
    assert _files(original_root) == {key[len(run_id) + 1:]: value for key, value in before.items() if key.startswith(run_id + "/")}
