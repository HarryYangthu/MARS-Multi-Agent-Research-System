"""Project metadata + repo_link inspection."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.settings import repo_root

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectSummary(BaseModel):
    name: str
    description: str = ""
    domain: str = ""
    tags: list[str] = []
    repo_path: str = ""
    repo_exists: bool = False


def _projects_dir() -> Path:
    return repo_root() / "projects"


def _read_yaml(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _resolve_repo_path(project: str, raw: str) -> Path:
    if not raw:
        return Path("")
    if raw.startswith("/"):
        return Path(raw)
    return (repo_root() / "projects" / project / raw).resolve()


def _summary(project_dir: Path) -> ProjectSummary:
    name = project_dir.name
    pj = _read_yaml(project_dir / "project.yaml")
    rl = _read_yaml(project_dir / "repo_link.yaml")
    raw_path = str(rl.get("repo_path", ""))
    abs_path = _resolve_repo_path(name, raw_path) if raw_path else Path("")
    return ProjectSummary(
        name=name,
        description=str(pj.get("description", "")),
        domain=str(pj.get("domain", "")),
        tags=list(pj.get("tags", []) or []),
        repo_path=str(abs_path) if raw_path else "",
        repo_exists=bool(raw_path) and abs_path.exists(),
    )


@router.get("", response_model=list[ProjectSummary])
async def list_projects() -> list[ProjectSummary]:
    out: list[ProjectSummary] = []
    pdir = _projects_dir()
    if not pdir.exists():
        return out
    for entry in sorted(pdir.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "project.yaml").exists():
            continue
        out.append(_summary(entry))
    return out


@router.get("/{name}", response_model=ProjectSummary)
async def get_project(name: str) -> ProjectSummary:
    p = _projects_dir() / name
    if not (p / "project.yaml").exists():
        raise HTTPException(status_code=404, detail=f"unknown project '{name}'")
    return _summary(p)


@router.get("/{name}/baseline_rules")
async def baseline_rules(name: str) -> dict[str, Any]:
    p = _projects_dir() / name
    rl = _read_yaml(p / "repo_link.yaml")
    md_path = p / "AGENTS.md"
    return {
        "project": name,
        "protected_paths": list(rl.get("protected_paths", []) or []),
        "agents_md": md_path.read_text(encoding="utf-8") if md_path.exists() else "",
    }
