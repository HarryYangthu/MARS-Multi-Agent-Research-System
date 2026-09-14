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
from app.harness.project_workspace import folder_project, list_folder_projects, open_folder, project_root
from app.harness.context.folder_context import discover_folder_context
from app.harness.agent_loop.trace import digest

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectSummary(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    domain: str = ""
    tags: list[str] = Field(default_factory=list)
    repo_path: str = ""
    repo_exists: bool = False
    pack_version: str | None = None
    contract_version: Literal["project_pack.v1"] | None = None
    capabilities: list[str] = Field(default_factory=list)
    pack_distribution: Literal["public", "private"] | None = None
    compatibility_mode: Literal["v30_legacy", "v31_pack"] = "v30_legacy"
    folder_path: str = ""
    project_type: Literal["configured", "folder"] = "configured"


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


def _summary(project_dir: Path, name: str = "") -> ProjectSummary:
    name = name or project_dir.name
    pj = _read_yaml(project_dir / "project.yaml")
    rl = _read_yaml(project_dir / "repo_link.yaml")
    raw_path = str(rl.get("repo_path", ""))
    abs_path = _resolve_repo_path(project_dir, raw_path) if raw_path else Path("")
    return ProjectSummary(
        name=name,
        display_name=str(pj.get("display_name", name)),
        description=str(pj.get("description", "")),
        domain=str(pj.get("domain", "")),
        tags=list(pj.get("tags", []) or []),
        repo_path=str(abs_path) if raw_path else "",
        repo_exists=bool(raw_path) and abs_path.exists(),
        folder_path=str(project_dir.parent) if project_dir.name == ".mars" else str(project_dir),
        project_type="folder" if project_dir.name == ".mars" else "configured",
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
        display_name=pack.manifest.display_name,
        description=str(project.get("description", "")),
        domain=str(project.get("domain", "")),
        tags=list(project.get("tags", []) or []),
        repo_path=str(abs_path) if raw_path else "",
        repo_exists=bool(raw_path) and abs_path.exists(),
        pack_version=pack.manifest.pack_version,
        contract_version=pack.manifest.schema_id,
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
    for folder in list_folder_projects():
        if not folder.error:
            out[folder.name] = _summary(folder.metadata_root, folder.name)
        else:
            out[folder.name] = ProjectSummary(name=folder.name, display_name=folder.display_name,
                folder_path=str(folder.root), project_type="folder", description=folder.error)
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


class OpenFolderPayload(BaseModel):
    path: str = Field(min_length=1)
    create: bool = False


@router.post("/folder", response_model=ProjectSummary)
def open_project_folder(payload: OpenFolderPayload) -> ProjectSummary:
    try:
        folder = open_folder(payload.path, create=payload.create)
        return _summary(folder.metadata_root, folder.name)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/folders")
def browse_project_folders(path: str = "") -> dict[str, Any]:
    root = Path(path).expanduser() if path else Path.home()
    if not root.is_absolute() or not root.is_dir():
        raise HTTPException(status_code=422, detail="文件夹不存在，请输入完整路径")
    try:
        root = root.resolve()
        directories = sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.casefold())
        return {"path": str(root), "parent": str(root.parent), "directories": [
            {"name": p.name, "path": str(p)} for p in directories[:300]], "has_more": len(directories) > 300}
    except OSError as exc:
        raise HTTPException(status_code=422, detail="无法访问此文件夹") from exc


def project_context_record(name: str) -> dict[str, Any]:
    folder = folder_project(name)
    if folder is not None:
        return discover_folder_context(folder)
    root = project_root(name)
    cfg = _read_yaml(root / "project.yaml")
    if not cfg:
        raise ValueError("项目不存在")
    paths = [root / "AGENTS.md"]
    if cfg.get("knowledge_file"):
        paths.append(root / str(cfg["knowledge_file"]))
    files = []
    for path in paths:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("上下文文件越过项目边界")
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            files.append({"path": path.relative_to(root).as_posix(), "content": content,
                          "chars": len(content), "sha256": digest(content),
                          "role": "instructions" if path.name == "AGENTS.md" else "reference"})
    return {"project": name, "folder": str(root), "files": files,
            "total_chars": sum(f["chars"] for f in files), "warnings": []}


@router.get("/{name}/auto-context")
def get_project_auto_context(name: str) -> dict[str, Any]:
    try:
        record = project_context_record(name)
        return {**record, "files": [{k: v for k, v in f.items() if k != "content"} for f in record["files"]]}
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{name}/auto-context/document")
def get_project_context_document(name: str, path: str) -> dict[str, Any]:
    try:
        record = project_context_record(name)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    document = next((f for f in record["files"] if f["path"] == path), None)
    if document is None:
        raise HTTPException(status_code=404, detail="该文件不在自动加载的上下文中")
    return dict(document)


@router.get("/{name}", response_model=ProjectSummary)
async def get_project(name: str) -> ProjectSummary:
    try:
        folder = folder_project(name)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if folder is not None:
        return _summary(folder.metadata_root, folder.name)
    pack = _loaded_packs().get(name)
    if pack is not None:
        return _pack_summary(pack)
    p = project_root(name)
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
        project_path = project_root(name)
        repo_link_path = project_path / "repo_link.yaml"
        folder = folder_project(name)
        rules_path = (folder.root if folder else project_path) / "AGENTS.md"
    rl = _read_yaml(repo_link_path)
    return {
        "project": name,
        "protected_paths": list(rl.get("protected_paths", []) or []),
        "agents_md": (
            rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
        ),
    }
