"""Project repository resolution and path checks for code tools."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.harness.project_workspace import project_root
from app.harness.runtime.project_scope import ProjectScope, current_project_scope
from app.settings import repo_root


TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".cfg",
        ".c",
        ".cpp",
        ".cu",
        ".h",
        ".hpp",
        ".ini",
        ".json",
        ".m",
        ".md",
        ".py",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)


@dataclass(frozen=True)
class ProjectRepo:
    project: str
    root: Path
    repo_mode: str
    read_only: bool
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    ignore_patterns: tuple[str, ...]
    scope: ProjectScope | None = None


def load_project_repo(project: str) -> ProjectRepo:
    scope = current_project_scope(project)
    if scope is not None:
        return ProjectRepo(project=project, root=scope.candidate_root, repo_mode="run_bound",
            read_only=False, allowed_paths=scope.allowed_write_paths,
            protected_paths=scope.protected_paths, ignore_patterns=scope.excluded_paths, scope=scope)
    project_dir = project_root(project)
    cfg_path = project_dir / "repo_link.yaml"
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        raw = loaded if isinstance(loaded, dict) else {}
    raw_path = str(raw.get("repo_path") or raw.get("local_path") or "").strip()
    if raw_path:
        candidate = Path(raw_path)
        root = candidate if candidate.is_absolute() else (project_dir / candidate)
    else:
        raise ValueError(f"no real repository configured for project {project!r}")
    allowed = _tuple(raw.get("allowed_paths")) or ("",)
    return ProjectRepo(
        project=project,
        root=root.resolve(),
        repo_mode=str(raw.get("repo_mode", "local_path")),
        read_only=bool(raw.get("read_only", False)),
        allowed_paths=allowed,
        protected_paths=_tuple(raw.get("protected_paths")),
        ignore_patterns=_tuple(raw.get("ignore_patterns")),
    )


def resolve_allowed_path(
    repo: ProjectRepo,
    rel_path: str,
    *,
    require_exists: bool = False,
    require_text: bool = False,
    for_write: bool = False,
) -> Path:
    if repo.scope is not None:
        if current_project_scope(repo.project) != repo.scope:
            raise ValueError("Project repository capability is not bound to this execution")
        target = repo.scope.resolve_file(rel_path, write=for_write, must_exist=require_exists)
        if require_text and target.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError(f"unsupported text file type '{target.suffix}'")
        return target
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("path must be a relative path inside the project repo")
    normalized = rel.as_posix()
    if _ignored(normalized, repo.ignore_patterns):
        raise ValueError(f"path '{normalized}' is ignored by project rules")
    if not _allowed(normalized, repo.allowed_paths):
        raise ValueError(f"path '{normalized}' is outside repo_link.yaml allowed_paths")
    target = (repo.root / rel).resolve()
    if not _is_relative_to(target, repo.root):
        raise ValueError("path escapes the project repo")
    if require_exists and not target.exists():
        raise ValueError(f"path '{normalized}' does not exist")
    if require_text and target.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError(f"unsupported text file type '{target.suffix}'")
    return target


def validate_repo_writable(repo: ProjectRepo) -> None:
    if repo.scope is not None:
        raise ValueError("External coding adapters are not admitted for a bound research contract")
    if repo.read_only:
        raise ValueError("project repo is read_only in repo_link.yaml")
    if not repo.root.exists():
        raise ValueError(f"project repo does not exist: {repo.root}")
    if not repo.root.is_dir():
        raise ValueError(f"project repo is not a directory: {repo.root}")


def _tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _allowed(path: str, patterns: tuple[str, ...]) -> bool:
    for pat in patterns:
        normalized = pat.strip()
        if not normalized:
            return True
        if normalized.endswith("/"):
            directory = normalized.rstrip("/")
            if path == directory or path.startswith(normalized):
                return True
            continue
        if path == normalized or fnmatch.fnmatch(path, normalized):
            return True
    return False


def _ignored(path: str, patterns: tuple[str, ...]) -> bool:
    for pat in patterns:
        normalized = pat.strip()
        if not normalized:
            continue
        if normalized.endswith("/"):
            if path.startswith(normalized):
                return True
            continue
        if fnmatch.fnmatch(path, normalized):
            return True
    return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
