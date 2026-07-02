"""Coding workspace store behavior."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import coding_workspace_store as store


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def fake_repo_root() -> Path:
        return tmp_path

    def fake_kb_memory(limit_per_zone: int = 4) -> tuple[store.CodingMemoryItem, ...]:
        del limit_per_zone
        return ()

    monkeypatch.setattr(store, "repo_root", fake_repo_root)
    monkeypatch.setattr(store, "_load_kb_memory_items", fake_kb_memory)

    project_dir = tmp_path / "projects" / "pimc"
    project_dir.mkdir(parents=True)
    (project_dir / "repo_link.yaml").write_text(
        "project: pimc\n"
        "repo_path: ../../workspace/repos/pimc-current\n"
        "ignore_patterns:\n"
        "  - data/\n"
        "  - '*.npz'\n",
        encoding="utf-8",
    )

    stub = tmp_path / "workspace" / "repos" / "pimc-stub"
    (stub / "libs").mkdir(parents=True)
    (stub / "data").mkdir(parents=True)
    (stub / "main.py").write_text("print('stub')\n", encoding="utf-8")
    (stub / "libs" / "Model.py").write_text("class Model: ...\n", encoding="utf-8")
    (stub / "data" / "sample.npz").write_bytes(b"npz")

    config_dir = tmp_path / "configs" / "agent_contexts"
    config_dir.mkdir(parents=True)
    (config_dir / "coding.yaml").write_text("agent: coding\n", encoding="utf-8")
    return tmp_path


def test_workspace_falls_back_to_stub_when_project_repo_missing(fake_repo: Path) -> None:
    del fake_repo
    workspace = store.build_coding_workspace(project="pimc", source="auto")
    assert workspace.selected_source == "pimc_stub"
    paths = {item.path for item in workspace.files}
    assert "main.py" in paths
    assert "libs/Model.py" in paths
    assert "data/sample.npz" not in paths


def test_workspace_reads_selected_code_file(fake_repo: Path) -> None:
    del fake_repo
    content = store.read_code_file(
        project="pimc",
        source="pimc_stub",
        path="libs/Model.py",
    )
    assert content.language == "python"
    assert "class Model" in content.content


def test_coding_memory_items_can_be_saved(fake_repo: Path) -> None:
    del fake_repo
    saved = store.save_coding_memory_items(
        [
            {
                "id": "patch_scope",
                "label": "Patch scope",
                "text": "Prefer small patches.",
                "enabled": True,
                "source": "custom",
            }
        ]
    )
    assert saved[0].id == "patch_scope"
    loaded = store.load_coding_memory_items()
    assert loaded[0].text == "Prefer small patches."
