"""Real experiment records in temporary project metadata; no fakes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.bridge import experiment_service as service
from app.storage.run_store import RunStore


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project-root"
    root.mkdir()
    monkeypatch.setattr(service, "project_root", lambda name: root)
    return root


def test_create_persists_schema_record_and_lists_newest_first(project_root: Path) -> None:
    first = service.create_experiment("proj", "残差降 2dB")
    second = service.create_experiment("proj", "参数量对比", description="固定精度")
    assert first["schema"] == "experiment.v1" and first["project"] == "proj"
    assert len(first["id"]) == 32
    stored = yaml.safe_load((project_root / "experiments" / f"{first['id']}.yaml").read_text())
    assert stored["name"] == "残差降 2dB"
    records = service.list_experiments("proj")
    assert [r["id"] for r in records] == [second["id"], first["id"]]


def test_duplicate_name_rejected_and_name_validated(project_root: Path) -> None:
    service.create_experiment("proj", "基线复现")
    with pytest.raises(ValueError, match="已存在"):
        service.create_experiment("proj", " 基线复现 ")
    with pytest.raises(ValueError, match="1-120"):
        service.create_experiment("proj", "   ")
    with pytest.raises(ValueError, match="1-120"):
        service.create_experiment("proj", "x" * 121)


def test_get_require_delete_and_invalid_ids(project_root: Path) -> None:
    record = service.create_experiment("proj", "消融实验")
    assert service.get_experiment("proj", record["id"])["name"] == "消融实验"
    assert service.require_experiment("proj", record["id"])["id"] == record["id"]
    assert service.require_experiment("proj", "") == {}
    with pytest.raises(ValueError, match="无效"):
        service.require_experiment("proj", "../escape")
    with pytest.raises(ValueError, match="不存在"):
        service.require_experiment("proj", "0" * 32)
    service.delete_experiment("proj", record["id"])
    with pytest.raises(ValueError, match="不存在"):
        service.get_experiment("proj", record["id"])


def test_run_store_records_experiment_ownership(tmp_path: Path) -> None:
    store = RunStore(runs_root=tmp_path / "runs")
    handle = store.create(task="残差研究", project="proj", experiment_id="a" * 32)
    meta = json.loads((handle.root / "run_meta.json").read_text())
    assert meta["experiment_id"] == "a" * 32
    plain = store.create(task="无归属", project="proj")
    assert "experiment_id" not in json.loads((plain.root / "run_meta.json").read_text())
    owned = [r for r in store.list() if (r.meta or {}).get("experiment_id") == "a" * 32]
    assert [r.run_id for r in owned] == [handle.run_id]
