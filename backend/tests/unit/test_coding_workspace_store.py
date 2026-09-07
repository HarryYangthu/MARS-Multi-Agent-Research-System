"""Coding workspace store behavior."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import coding_workspace_store as store


@pytest.fixture
def temporary_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def temporary_repo_root() -> Path:
        return tmp_path

    monkeypatch.setattr(store, "repo_root", temporary_repo_root)
    from app.harness.kb.stores import reset_for_tests
    reset_for_tests(tmp_path / "knowledge")

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

    source = tmp_path / "workspace" / "repos" / "pimc-current"
    (source / "libs").mkdir(parents=True)
    (source / "data").mkdir(parents=True)
    (source / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "libs" / "Model.py").write_text("class Model: ...\n", encoding="utf-8")
    (source / "data" / "sample.npz").write_bytes(b"npz")

    config_dir = tmp_path / "configs" / "agent_contexts"
    config_dir.mkdir(parents=True)
    (config_dir / "coding.yaml").write_text("agent: coding\n", encoding="utf-8")
    return tmp_path


def test_workspace_selects_actual_linked_repository(temporary_repo: Path) -> None:
    workspace = store.build_coding_workspace(project="pimc", source="auto")
    assert workspace.selected_source == "project_repo"
    paths = {item.path for item in workspace.files}
    assert "main.py" in paths
    assert "libs/Model.py" in paths
    assert "data/sample.npz" not in paths


def test_missing_project_repository_does_not_select_a_substitute(temporary_repo: Path) -> None:
    import shutil
    shutil.rmtree(temporary_repo / "workspace/repos/pimc-current")
    workspace = store.build_coding_workspace(project="pimc", source="auto")
    assert workspace.selected_source == "empty"
    assert workspace.files == ()
    assert "pimc_stub" not in {source.id for source in workspace.sources}


def test_workspace_reads_selected_code_file(temporary_repo: Path) -> None:
    del temporary_repo
    content = store.read_code_file(
        project="pimc",
        source="project_repo",
        path="libs/Model.py",
    )
    assert content.language == "python"
    assert "class Model" in content.content


def test_coding_memory_items_can_be_saved(temporary_repo: Path) -> None:
    del temporary_repo
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
