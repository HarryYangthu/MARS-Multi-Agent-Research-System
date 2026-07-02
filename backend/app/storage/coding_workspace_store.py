"""Read-only Coding Agent workspace projections for the UI."""
from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from app.harness.kb.selector import select_memory
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
        ".ipynb",
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
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    "node_modules/",
    "data/",
    "logs/",
    "runs/",
    "*.npy",
    "*.npz",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.pkl",
)
MAX_TREE_ITEMS = 500
MAX_FILE_CHARS = 60000
MAX_CONTEXT_CHARS = 16000


@dataclass(frozen=True)
class CodeSource:
    id: str
    label: str
    path: str
    exists: bool
    read_only: bool
    kind: str


@dataclass(frozen=True)
class CodeTreeItem:
    path: str
    name: str
    kind: str
    depth: int
    size_chars: int
    language: str


@dataclass(frozen=True)
class CodeFileContent:
    source_id: str
    path: str
    language: str
    size_chars: int
    truncated: bool
    content: str


@dataclass(frozen=True)
class UpstreamContextItem:
    id: str
    agent: str
    title: str
    path: str
    kind: str
    content: str


@dataclass(frozen=True)
class CodingMemoryItem:
    id: str
    label: str
    text: str
    enabled: bool
    source: str
    editable: bool


@dataclass(frozen=True)
class CodingWorkspace:
    project: str
    selected_source: str
    sources: tuple[CodeSource, ...]
    files: tuple[CodeTreeItem, ...]
    upstream_context: tuple[UpstreamContextItem, ...]
    memory_items: tuple[CodingMemoryItem, ...]
    kb_memory_items: tuple[CodingMemoryItem, ...]


DEFAULT_CODING_MEMORY: tuple[Mapping[str, object], ...] = (
    {
        "id": "baseline_compatibility",
        "label": "Baseline compatibility",
        "text": "Avoid touching protected baseline paths unless Gate 5 approval is explicit.",
        "enabled": True,
        "source": "default",
    },
    {
        "id": "simulation_contract",
        "label": "Simulation contract",
        "text": "Prefer small, testable patches that keep mock simulation and schema-valid artifacts working.",
        "enabled": True,
        "source": "default",
    },
)


def build_coding_workspace(
    *,
    project: str,
    source: str = "auto",
    run_root: Path | None = None,
) -> CodingWorkspace:
    project_cfg = _project_config(project)
    sources = _code_sources(project=project, project_cfg=project_cfg)
    selected_source = _select_source(source, sources)
    files = _list_code_tree(
        root=_source_root(selected_source, sources),
        ignore_patterns=_ignore_patterns(project_cfg),
    )
    return CodingWorkspace(
        project=project,
        selected_source=selected_source,
        sources=sources,
        files=files,
        upstream_context=_load_upstream_context(run_root),
        memory_items=load_coding_memory_items(),
        kb_memory_items=_load_kb_memory_items(),
    )


def read_code_file(*, project: str, source: str, path: str) -> CodeFileContent:
    project_cfg = _project_config(project)
    sources = _code_sources(project=project, project_cfg=project_cfg)
    selected_source = _select_source(source, sources)
    root = _source_root(selected_source, sources)
    if root is None:
        raise ValueError("selected code source has no files")
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("invalid code file path")
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if not _is_relative_to(target, root_resolved):
        raise ValueError("code file escapes source root")
    if not target.is_file():
        raise ValueError("code file not found")
    if target.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError(f"unsupported code file type '{target.suffix}'")
    text = target.read_text(encoding="utf-8", errors="replace")
    return CodeFileContent(
        source_id=selected_source,
        path=rel.as_posix(),
        language=_language_for(target),
        size_chars=len(text),
        truncated=len(text) > MAX_FILE_CHARS,
        content=text[:MAX_FILE_CHARS],
    )


def load_coding_memory_items() -> tuple[CodingMemoryItem, ...]:
    cfg = _load_coding_config()
    raw_items = cfg.get("memory_items")
    if not isinstance(raw_items, list):
        raw_items = [dict(item) for item in DEFAULT_CODING_MEMORY]
    return tuple(_memory_item(item, index=index, editable=True) for index, item in enumerate(raw_items))


def save_coding_memory_items(
    items: Sequence[CodingMemoryItem | Mapping[str, object]],
) -> tuple[CodingMemoryItem, ...]:
    normalized = tuple(_memory_item(item, index=index, editable=True) for index, item in enumerate(items))
    cfg = _load_coding_config()
    cfg["memory_items"] = [
        {
            "id": item.id,
            "label": item.label,
            "text": item.text,
            "enabled": item.enabled,
            "source": item.source,
        }
        for item in normalized
    ]
    _save_coding_config(cfg)
    return normalized


def _project_config(project: str) -> dict[str, Any]:
    project_dir = repo_root() / "projects" / project
    path = project_dir / "repo_link.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _code_sources(*, project: str, project_cfg: Mapping[str, Any]) -> tuple[CodeSource, ...]:
    raw_path = str(project_cfg.get("repo_path", "")).strip()
    project_path = _resolve_repo_path(project, raw_path) if raw_path else None
    stub_path = repo_root() / "workspace" / "repos" / "pimc-stub"
    return (
        CodeSource(
            id="empty",
            label="空项目",
            path="",
            exists=True,
            read_only=False,
            kind="empty",
        ),
        CodeSource(
            id="project_repo",
            label="项目 repo_link",
            path=str(project_path) if project_path is not None else "",
            exists=project_path is not None and project_path.exists(),
            read_only=bool(project_cfg.get("read_only", False)),
            kind="project_repo",
        ),
        CodeSource(
            id="pimc_stub",
            label="PIMC stub 仿真代码",
            path=str(stub_path),
            exists=stub_path.exists(),
            read_only=False,
            kind="stub",
        ),
    )


def _resolve_repo_path(project: str, raw: str) -> Path:
    raw_path = Path(raw)
    if raw_path.is_absolute():
        return raw_path
    return (repo_root() / "projects" / project / raw_path).resolve()


def _select_source(requested: str, sources: Sequence[CodeSource]) -> str:
    by_id = {source.id: source for source in sources}
    if requested != "auto":
        source = by_id.get(requested)
        if source is None:
            raise ValueError(f"unknown code source '{requested}'")
        if not source.exists:
            raise ValueError(f"code source '{requested}' is not available")
        return requested
    for candidate in ("project_repo", "pimc_stub", "empty"):
        source = by_id[candidate]
        if source.exists:
            return source.id
    return "empty"


def _source_root(source_id: str, sources: Sequence[CodeSource]) -> Path | None:
    for source in sources:
        if source.id == source_id:
            return Path(source.path) if source.path else None
    raise ValueError(f"unknown code source '{source_id}'")


def _ignore_patterns(project_cfg: Mapping[str, Any]) -> tuple[str, ...]:
    raw = project_cfg.get("ignore_patterns")
    if not isinstance(raw, list):
        return DEFAULT_IGNORE_PATTERNS
    return tuple(str(item) for item in raw) + DEFAULT_IGNORE_PATTERNS


def _list_code_tree(
    *,
    root: Path | None,
    ignore_patterns: Sequence[str],
) -> tuple[CodeTreeItem, ...]:
    if root is None or not root.exists():
        return ()
    items: list[CodeTreeItem] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        rel = path.relative_to(root)
        if _is_ignored(rel, ignore_patterns):
            continue
        if path.is_dir():
            items.append(
                CodeTreeItem(
                    path=rel.as_posix(),
                    name=path.name,
                    kind="directory",
                    depth=len(rel.parts) - 1,
                    size_chars=0,
                    language="",
                )
            )
        elif path.suffix.lower() in TEXT_SUFFIXES:
            try:
                size = len(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                size = 0
            items.append(
                CodeTreeItem(
                    path=rel.as_posix(),
                    name=path.name,
                    kind="file",
                    depth=len(rel.parts) - 1,
                    size_chars=size,
                    language=_language_for(path),
                )
            )
        if len(items) >= MAX_TREE_ITEMS:
            break
    return tuple(items)


def _is_ignored(rel: Path, patterns: Sequence[str]) -> bool:
    posix = rel.as_posix()
    for pattern in patterns:
        clean = pattern.strip()
        if not clean:
            continue
        if clean.endswith("/") and (
            posix == clean.rstrip("/")
            or posix.startswith(clean)
            or clean.rstrip("/") in rel.parts
        ):
            return True
        if fnmatch.fnmatch(posix, clean):
            return True
    return False


def _load_upstream_context(run_root: Path | None) -> tuple[UpstreamContextItem, ...]:
    if run_root is None or not run_root.exists():
        return ()
    candidates: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
        (
            "task",
            "commander",
            "用户任务",
            "input",
            ("input/user_request.md",),
        ),
        (
            "diagnosis",
            "commander",
            "主 Agent 诊断",
            "artifact",
            ("diagnosis/diagnosis.approved.md", "diagnosis/diagnosis.v1.md"),
        ),
        (
            "idea",
            "idea",
            "Idea Agent proposal",
            "artifact",
            ("idea/idea_proposal.approved.md", "idea/idea_proposal.v1.md"),
        ),
        (
            "experiment",
            "experiment",
            "Experiment Agent plan",
            "artifact",
            (
                "experiment/experiment_plan.approved.md",
                "experiment/experiment_plan.v1.md",
            ),
        ),
        (
            "coding_pack",
            "system",
            "Coding context pack",
            "context",
            ("context/coding_context_snapshot.v2.md", "context/coding_context_pack.v2.json"),
        ),
    )
    out: list[UpstreamContextItem] = []
    for item_id, agent, title, kind, rel_paths in candidates:
        path = _first_existing(run_root, rel_paths)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out.append(
            UpstreamContextItem(
                id=item_id,
                agent=agent,
                title=title,
                path=path.relative_to(run_root).as_posix(),
                kind=kind,
                content=text[:MAX_CONTEXT_CHARS],
            )
        )
    return tuple(out)


def _first_existing(root: Path, rel_paths: Sequence[str]) -> Path | None:
    for rel_path in rel_paths:
        candidate = root / rel_path
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _load_kb_memory_items(limit_per_zone: int = 4) -> tuple[CodingMemoryItem, ...]:
    out: list[CodingMemoryItem] = []
    for zone in ("code_assets", "run_archive"):
        hits = select_memory(
            query="coding implementation baseline regression patch simulation",
            zones=[zone],
            top_k=limit_per_zone,
            include_mock=False,
            include_superseded=False,
        )
        for index, hit in enumerate(hits):
            record = hit.record
            label = str(record.metadata.get("title", "") or record.id)
            out.append(
                CodingMemoryItem(
                    id=f"{zone}:{record.id}",
                    label=label,
                    text=hit.injected_text[:1200],
                    enabled=True,
                    source=zone,
                    editable=False,
                )
            )
            if index + 1 >= limit_per_zone:
                break
    return tuple(out)


def _memory_item(
    raw: CodingMemoryItem | Mapping[str, object],
    *,
    index: int,
    editable: bool,
) -> CodingMemoryItem:
    item = asdict(raw) if isinstance(raw, CodingMemoryItem) else dict(raw)
    label = str(item.get("label", "")).strip() or f"Memory {index + 1}"
    raw_id = str(item.get("id", "")).strip()
    text = str(item.get("text", "")).strip()
    return CodingMemoryItem(
        id=raw_id or _slug(label, index),
        label=label,
        text=text,
        enabled=bool(item.get("enabled", True)),
        source=str(item.get("source", "custom")) or "custom",
        editable=editable,
    )


def _load_coding_config() -> dict[str, Any]:
    path = _coding_config_path()
    if not path.exists():
        return {"agent": "coding"}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {"agent": "coding"}


def _save_coding_config(cfg: Mapping[str, Any]) -> None:
    path = _coding_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(cfg), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _coding_config_path() -> Path:
    return repo_root() / "configs" / "agent_contexts" / "coding.yaml"


def _language_for(path: Path) -> str:
    suffix = path.suffix.lower()
    languages = {
        ".c": "c",
        ".cfg": "ini",
        ".cpp": "cpp",
        ".cu": "cuda",
        ".h": "c",
        ".hpp": "cpp",
        ".ini": "ini",
        ".ipynb": "json",
        ".json": "json",
        ".m": "matlab",
        ".md": "markdown",
        ".py": "python",
        ".sh": "shell",
        ".toml": "toml",
        ".txt": "text",
        ".yaml": "yaml",
        ".yml": "yaml",
    }
    return languages.get(suffix, "text")


def _slug(label: str, index: int) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in label).strip("_")
    return cleaned or f"memory_{index + 1}"


def _is_relative_to(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
