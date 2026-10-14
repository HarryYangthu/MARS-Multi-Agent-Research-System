"""Trusted folder storage routing; global roots remain legacy read locations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.harness.project_workspace import FolderProject, folder_project, list_folder_projects
from app.harness.runtime.project_scope import safe_scope_path
from app.settings import repo_root


def storage_policy() -> dict[str, Any]:
    raw = yaml.safe_load((repo_root() / "configs/project_storage.yaml").read_text(encoding="utf-8"))
    policy = raw["project_storage"]
    if not isinstance(policy, dict):
        raise ValueError("Invalid project storage policy")
    return policy


def folder_directory(folder: FolderProject, kind: str, *, create: bool = True) -> Path:
    relative = str(storage_policy()["directories"][kind])
    root = safe_scope_path(folder.root, relative + "/.storage-check").parent
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def project_storage_root(project: str, kind: str, legacy: Path) -> Path:
    folder = folder_project(project)
    return folder_directory(folder, kind) if folder is not None else legacy


def storage_roots(legacy: Path, kind: str) -> list[Path]:
    """Only registered, valid project folders may contribute read locations."""
    result: list[Path] = []
    for folder in list_folder_projects():
        if folder.error:
            continue
        try:
            root = folder_directory(folder, kind, create=False)
        except (OSError, ValueError):
            continue
        if root.exists() and root not in result:
            result.append(root)
    return result + [legacy]
