"""Folder-backed project identity and metadata, shared by API, agents and tools."""
from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.harness.agent_loop.trace import atomic_json
from app.settings import get_settings, repo_root

_LOCK = threading.RLock()
_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class FolderProject:
    name: str
    display_name: str
    root: Path
    error: str = ""

    @property
    def metadata_root(self) -> Path:
        return self.root / ".mars"


def registry_path() -> Path:
    configured = get_settings().mars_folder_projects_registry
    return Path(configured).expanduser() if configured else repo_root() / "workspace/folder_projects.json"


def _entries(registry: Path) -> dict[str, str]:
    if not registry.exists():
        return {}
    raw = json.loads(registry.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in raw.items()):
        raise ValueError("项目目录索引损坏，请检查 folder_projects.json")
    return raw


def _read_project(root: Path) -> FolderProject:
    path = root / ".mars/project.yaml"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("项目配置不能指向文件夹之外")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != "folder_project.v1" or not re.fullmatch(r"folder_[0-9a-f]{32}", str(raw.get("name", ""))):
        raise ValueError("无效的 .mars/project.yaml 项目身份")
    return FolderProject(str(raw["name"]), str(raw.get("display_name") or root.name), root.resolve())


def list_folder_projects(*, registry: Path | None = None) -> list[FolderProject]:
    result = []
    for name, path in _entries(registry or registry_path()).items():
        root = Path(path)
        try:
            project = _read_project(root)
            if project.name != name:
                raise ValueError("项目身份已变化，请重新打开文件夹")
            result.append(project)
        except (OSError, ValueError, yaml.YAMLError):
            result.append(FolderProject(name, root.name, root, "文件夹或项目配置不可用，请重新打开"))
    return result


def folder_project(name: str, *, registry: Path | None = None) -> FolderProject | None:
    if not _PROJECT_ID.fullmatch(name) or name in {".", ".."}:
        raise ValueError("invalid project name")
    path = _entries(registry or registry_path()).get(name)
    if path is None:
        return None
    project = _read_project(Path(path))
    if project.name != name:
        raise ValueError("项目身份与目录索引不匹配")
    return project


def project_root(project: str) -> Path:
    """Metadata root; the research repository itself is resolved via repo_link."""
    folder = folder_project(project)
    if folder is not None:
        return folder.metadata_root
    candidate = repo_root() / "projects" / project
    if not candidate.resolve().is_relative_to((repo_root() / "projects").resolve()):
        raise ValueError("invalid project path")
    return candidate


def open_folder(path: str, *, create: bool = False, registry: Path | None = None) -> FolderProject:
    """Initialize only project-owned metadata; never replace existing project files."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("请选择文件夹的完整路径")
    root = candidate.resolve()
    with _LOCK:
        if create:
            if not root.parent.is_dir():
                raise ValueError("新项目的上级文件夹不存在")
            root.mkdir(exist_ok=False)
        if not root.is_dir():
            raise ValueError("项目文件夹不存在")
        metadata = root / ".mars"
        if metadata.is_symlink():
            raise ValueError(".mars 必须是项目文件夹内的真实目录")
        metadata.mkdir(exist_ok=True)
        marker = metadata / "project.yaml"
        index = registry or registry_path()
        entries = _entries(index)
        if marker.exists():
            project = _read_project(root)
        else:
            project = FolderProject("folder_" + uuid.uuid4().hex, root.name, root)
            with marker.open("x", encoding="utf-8") as stream:
                yaml.safe_dump({"schema": "folder_project.v1", "name": project.name, "display_name": root.name,
                                "description": "", "context_files": ["AGENTS.md", "README.md", "context/**/*.md"]},
                               stream, allow_unicode=True, sort_keys=False)
        previous = entries.get(project.name)
        if previous and Path(previous).resolve() != root and Path(previous).exists():
            raise ValueError("该项目身份已属于另一个文件夹；复制项目时请移除副本的 .mars/project.yaml 后重新打开")
        repo_link = metadata / "repo_link.yaml"
        if repo_link.is_symlink():
            raise ValueError("repo_link.yaml 不能是符号链接")
        if not repo_link.exists():
            with repo_link.open("x", encoding="utf-8") as stream:
                yaml.safe_dump({"project": project.name, "repo_mode": "local_path", "repo_path": "..",
                                "read_only": False, "allowed_paths": [], "protected_paths": ["baseline/"],
                                "ignore_patterns": [".git/", ".mars/", ".env*", "**/.env*", "node_modules/", ".venv/"]},
                               stream, allow_unicode=True, sort_keys=False)
        if create:
            (root / "context").mkdir(exist_ok=True)
            (root / "README.md").write_text(f"# {root.name}\n\n在这里记录项目目的和研究对象。\n\n"
                                           "背景资料放入 context/，项目约定写入 AGENTS.md；新任务会自动加载这些 Markdown 文件。\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("# 项目约定\n\n请补充研究目标、术语、约束和必须保留的接口。\n", encoding="utf-8")
        entries[project.name] = str(root)
        atomic_json(index, entries)
        return project


def read_project_config(project: FolderProject) -> dict[str, Any]:
    raw = yaml.safe_load((project.metadata_root / "project.yaml").read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}
