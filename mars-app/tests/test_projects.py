"""Real temporary folders verify project isolation and the no-core-write boundary."""
import json
from pathlib import Path

import pytest
import yaml

from mars_app import library
from mars_app.launcher import validate_contract
from mars_app.projects import ProjectStore, saved_events


def test_bundled_working_project_is_preferred_but_writable(tmp_path: Path, monkeypatch):
    root = tmp_path / "工作项目"
    store = ProjectStore(tmp_path / "app", tmp_path / "engine")
    project = store.create(str(root), {"name": "真实项目"})
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(root))
    store.supports_projects = lambda: True
    found = store.get(project["id"])
    assert found["preferred"] and found["connected"] and not found["demo"]


def test_relative_project_data_and_repository_paths_are_checked_separately(tmp_path: Path):
    import subprocess
    root = tmp_path / "项目"
    repo = root / "source"
    (repo / "configs").mkdir(parents=True)
    (repo / "configs/base.yaml").write_text("width: 8\n")
    (root / "data").mkdir()
    (root / "data/capture.bin").write_bytes(b"real file check")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", "-C", str(repo), "commit", "-qm", "source"], check=True)
    store = ProjectStore(tmp_path / "app", tmp_path / "engine")
    store.supports_projects = lambda: True
    checks = store.preflight(str(root), {"repo": {"path": "source", "ref": "HEAD"},
        "datasets": [{"name": "main", "config": "configs/base.yaml", "data": "data/capture.bin"}]})
    assert all(c["status"] == "pass" for c in checks if c["key"] in {"repo", "dataset-0-config", "dataset-0-data", "engine"})


def test_project_creation_preserves_existing_files_and_filters_credentials(tmp_path: Path):
    core = tmp_path / "engine"
    core.mkdir()
    store = ProjectStore(tmp_path / "app", core)
    root = tmp_path / "research"
    root.mkdir()
    (root / "existing.txt").write_text("retain this file")
    project = store.create(str(root), {"name": "课题", "api_key": "not-for-project-files",
                                    "repo": {"path": "/research", "token": "excluded"},
                                    "remote": {"name": "gpu", "password": "excluded"}})
    assert not project["connected"]
    assert (root / "existing.txt").read_text() == "retain this file"
    text = (root / ".mars/project.yaml").read_text()
    assert "not-for-project-files" not in text and "excluded" not in text
    assert set(p.name for p in root.iterdir()) >= {"context", "tasks", "runs", "reports", "workspace"}
    reopened = store.create(str(root), {}, opening=True)
    assert reopened["id"] == project["id"]
    assert len(store.list()) == 1  # only real project folders; MARS itself holds no workspace
    (root / ".mars/project.yaml").write_text((root / ".mars/project.yaml").read_text() + "evaluate: {command: run}\n")
    store.update(project["id"], {"name": "新名称", "repo": {"path": "/research", "editable_files": ["a.py"]}})
    saved = store.get(project["id"])
    assert saved["name"] == "新名称" and saved["config"]["evaluate"] == {"command": "run"}  # undeclared-by-form fields kept
    assert saved["config"]["schema_version"] == 2


def test_project_manager_cannot_write_core_or_ancestors(tmp_path: Path):
    core = tmp_path / "code" / "mars-core"
    core.mkdir(parents=True)
    marker = core / "api.py"
    marker.write_text("read-only engine")
    store = ProjectStore(tmp_path / "app", core)
    for path in [core, core / "nested", core.parent, tmp_path]:
        with pytest.raises(ValueError):
            store.create(str(path), {"name": "invalid"})
    with pytest.raises(ValueError, match="新建或打开"):
        store.update("current", {"name": "应用层名称"})  # no project yet: nothing to write
    assert store.get("current")["id"] == "none" and not store.get("current")["connected"]
    assert list(core.iterdir()) == [marker]
    assert marker.read_text() == "read-only engine"


def test_references_are_archived_with_original_names(tmp_path: Path):
    store = ProjectStore(tmp_path / "app", tmp_path / "engine")
    source = tmp_path / "资料.md"
    source.write_text("reference material")
    root = tmp_path / "research"
    project = store.create(str(root), {"name": "课题", "context_sources": [str(source)]})
    assert (root / "context/资料.md").read_text() == "reference material"
    assert project["config"]["context_sources"] == ["context/资料.md"]
    store.update(project["id"], project["config"])
    assert list((root / "context").iterdir()) == [root / "context/资料.md"]
    assert source.read_text() == "reference material"


def test_reference_archive_refuses_external_directory_links(tmp_path: Path):
    core = tmp_path / "engine"
    core.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    (root / "context").symlink_to(core)
    source = tmp_path / "reference.md"
    source.write_text("read only")
    store = ProjectStore(tmp_path / "app", core)
    with pytest.raises(ValueError):
        store.create(str(root), {"name": "课题", "context_sources": [str(source)]})
    assert list(core.iterdir()) == []


@pytest.mark.parametrize("runtime", ["codingagent", "zcode"])
def test_library_only_indexes_real_public_artifacts(tmp_path: Path, runtime: str):
    runs = tmp_path / "runs"
    run = runs / "run-a"
    iteration = run / "step2/P00/i1"
    iteration.mkdir(parents=True)
    (run / "contract.yaml").write_text(yaml.safe_dump({"title": "课题"}))
    (iteration / "record.json").write_text(json.dumps({"iteration": 1, "commit": "abc", "diff_files": ["model.py"]}))
    (iteration / runtime).mkdir()
    (iteration / runtime / "private.json").write_text("{}")
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (run / "external.json").symlink_to(outside)
    (runs / "external-run").symlink_to(run)
    items = library.index(runs, tmp_path / "reports")
    assert any(row["kind"] == "code" and row["virtual"] for row in items)
    assert not any(Path(row["path"]).name in {"private.json", "external.json"} for row in items)
    record = next(row for row in items if row["path"] == str(iteration / "record.json"))
    assert json.loads(library.preview(record)["text"])["iteration"] == 1


def test_preflight_and_event_reads_never_launch_research(tmp_path: Path):
    store = ProjectStore(tmp_path / "app", tmp_path / "engine")
    checks = store.preflight(str(tmp_path / "new"), {"name": "课题", "datasets": [{"name": "80", "data": str(tmp_path / "missing.mat")}]})
    assert any(c["status"] == "error" for c in checks)
    assert not (tmp_path / "new").exists()
    run = tmp_path / "saved-run"
    run.mkdir()
    (run / "events.jsonl").write_text('{"stage":"analysis","message":"saved"}\n{"partial"')
    assert saved_events(run) == [{"stage": "analysis", "message": "saved", "index": 0}]
    assert saved_events(run, 1) == []


def test_contract_rejects_invalid_numeric_budgets():
    contract = {"title": "test", "objective": "test", "targets": {"gates": [{"metric": "error"}]}, "budget": {"routes": -1}}
    assert any("budget.routes" in message for message in validate_contract(contract))


def test_current_project_is_the_preferred_or_first_registered(tmp_path: Path, monkeypatch) -> None:
    store = ProjectStore(tmp_path / "app", tmp_path / "engine")
    a = store.create(str(tmp_path / "a"), {"name": "A"})
    b = store.create(str(tmp_path / "b"), {"name": "B"})
    assert store.get("current")["id"] == b["id"]  # the most recently created is first in the list
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(tmp_path / "a"))
    assert store.get("current")["id"] == a["id"]


def test_moving_a_project_keeps_everything_and_reregisters(tmp_path: Path):
    core = tmp_path / "engine"
    core.mkdir()
    store = ProjectStore(tmp_path / "app", core)
    project = store.create(str(tmp_path / "a"), {"name": "课题"})
    (tmp_path / "a/.mars/project_id").write_text("keep-me", encoding="utf-8")
    (tmp_path / "a/runs/r1").mkdir()
    moved = store.move(project["id"], str(tmp_path / "新 位置" / "课题 A"))
    root = Path(moved["path"])
    assert (root / ".mars/project_id").read_text(encoding="utf-8") == "keep-me" and (root / "runs/r1").is_dir()
    assert not (tmp_path / "a").exists() and moved["id"] != project["id"]
    assert [p["path"] for p in store.list()] == [str(root)]
    (tmp_path / "occupied").mkdir()
    (tmp_path / "occupied/x.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        store.move(moved["id"], str(tmp_path / "occupied"))
    with pytest.raises(ValueError):
        store.move("none", str(tmp_path / "b"))  # no project selected: nothing to move
