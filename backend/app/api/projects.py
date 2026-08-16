"""Project metadata + repo_link inspection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.bridge.extension_runtime import get_extension_runtime
from app.harness.project_packs.registry import LoadedProjectPack
from app.settings import repo_root

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectSummary(BaseModel):
    name: str
    description: str = ""
    domain: str = ""
    tags: list[str] = Field(default_factory=list)
    repo_path: str = ""
    repo_exists: bool = False
    pack_version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    pack_distribution: Literal["public", "private"] | None = None
    compatibility_mode: Literal["v30_legacy", "v31_pack"] = "v30_legacy"


def _projects_dir() -> Path:
    return repo_root() / "projects"


def _read_yaml(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _resolve_repo_path(project_root: Path, raw: str) -> Path:
    if not raw:
        return Path("")
    if raw.startswith("/"):
        return Path(raw)
    return (project_root / raw).resolve()


def _summary(project_dir: Path) -> ProjectSummary:
    name = project_dir.name
    pj = _read_yaml(project_dir / "project.yaml")
    rl = _read_yaml(project_dir / "repo_link.yaml")
    raw_path = str(rl.get("repo_path", ""))
    abs_path = _resolve_repo_path(project_dir, raw_path) if raw_path else Path("")
    return ProjectSummary(
        name=name,
        description=str(pj.get("description", "")),
        domain=str(pj.get("domain", "")),
        tags=list(pj.get("tags", []) or []),
        repo_path=str(abs_path) if raw_path else "",
        repo_exists=bool(raw_path) and abs_path.exists(),
    )


def _pack_summary(pack: LoadedProjectPack) -> ProjectSummary:
    project_file = pack.file("project")
    project = _read_yaml(project_file)
    repo_link_path = pack.file("repo_link")
    repo_link = _read_yaml(repo_link_path) if repo_link_path.is_file() else {}
    raw_path = str(repo_link.get("repo_path", ""))
    abs_path = _resolve_repo_path(pack.root, raw_path) if raw_path else Path("")
    return ProjectSummary(
        name=pack.manifest.project_id,
        description=str(project.get("description", "")),
        domain=str(project.get("domain", "")),
        tags=list(project.get("tags", []) or []),
        repo_path=str(abs_path) if raw_path else "",
        repo_exists=bool(raw_path) and abs_path.exists(),
        pack_version=pack.manifest.pack_version,
        capabilities=list(pack.manifest.capabilities),
        pack_distribution=pack.manifest.distribution,
        compatibility_mode="v31_pack",
    )


def _loaded_packs() -> dict[str, LoadedProjectPack]:
    return {
        pack.manifest.project_id: pack
        for pack in get_extension_runtime().project_packs.list()
    }


@router.get("", response_model=list[ProjectSummary])
async def list_projects() -> list[ProjectSummary]:
    out: dict[str, ProjectSummary] = {
        name: _pack_summary(pack) for name, pack in _loaded_packs().items()
    }
    pdir = _projects_dir()
    if not pdir.exists():
        return [out[name] for name in sorted(out)]
    for entry in sorted(pdir.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "project.yaml").exists():
            continue
        if entry.name not in out:
            out[entry.name] = _summary(entry)
    return [out[name] for name in sorted(out)]


@router.get("/{name}", response_model=ProjectSummary)
async def get_project(name: str) -> ProjectSummary:
    pack = _loaded_packs().get(name)
    if pack is not None:
        return _pack_summary(pack)
    p = _projects_dir() / name
    if not (p / "project.yaml").exists():
        raise HTTPException(status_code=404, detail=f"unknown project '{name}'")
    return _summary(p)


@router.get("/{name}/ui-schema")
async def get_project_ui_schema(name: str) -> dict[str, Any]:
    pack = _loaded_packs().get(name)
    if pack is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "project_pack_ui_schema_unavailable",
                "message": f"project '{name}' uses V3.0 legacy configuration",
            },
        )
    path = pack.file("ui_schema")
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="invalid project UI schema") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=500, detail="invalid project UI schema")
    return value


@router.get("/{name}/baseline_rules")
async def baseline_rules(name: str) -> dict[str, Any]:
    pack = _loaded_packs().get(name)
    if pack is not None:
        repo_link_path = pack.file("repo_link")
        rules_path = pack.file("rules")
    else:
        project_path = _projects_dir() / name
        repo_link_path = project_path / "repo_link.yaml"
        rules_path = project_path / "AGENTS.md"
    rl = _read_yaml(repo_link_path)
    return {
        "project": name,
        "protected_paths": list(rl.get("protected_paths", []) or []),
        "agents_md": (
            rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
        ),
    }
