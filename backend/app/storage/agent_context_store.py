"""Persistent Agent context configuration.

The store keeps long-lived Agent context files in the repository so they are
available across runs. Runtime code is exposed as read-only context; docs,
prompts, examples, evals, and uploaded text/code can be edited from the UI.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.harness.kb.ingester import ingest_memory
from app.harness.kb.models import MemoryType
from app.harness.kb.stores import KBStores, get_stores
from app.settings import repo_root

SUPPORTED_AGENTS: frozenset[str] = frozenset(
    {"commander", "idea", "experiment", "coding", "execution", "writing"}
)
ACTIVE_MEMORY_STATUSES: frozenset[str] = frozenset({"approved", "active"})
MEMORY_LIFECYCLE_STATUSES: frozenset[str] = frozenset(
    {"approved", "active", "stale", "superseded", "rejected"}
)
CONTEXT_DIRS: tuple[str, ...] = ("docs", "prompts", "examples", "evals", "uploads")
EDITABLE_ROOTS: tuple[str, ...] = (
    "docs",
    "prompts",
    "examples",
    "evals",
    "uploads",
)
CREATE_CATEGORIES: tuple[str, ...] = (
    "docs",
    "prompts",
    "examples",
    "evals",
    "uploads/docs",
    "uploads/code",
)
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".c",
        ".cfg",
        ".cpp",
        ".css",
        ".cu",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".ipynb",
        ".js",
        ".json",
        ".jsx",
        ".m",
        ".md",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

DEFAULT_RESEARCH_SITES: tuple[Mapping[str, object], ...] = (
    {
        "id": "arxiv",
        "label": "arXiv",
        "url": "https://arxiv.org",
        "enabled": True,
        "source": "default",
    },
    {
        "id": "google_scholar",
        "label": "Google Scholar",
        "url": "https://scholar.google.com",
        "enabled": True,
        "source": "default",
    },
    {
        "id": "ieee_xplore",
        "label": "IEEE Xplore",
        "url": "https://ieeexplore.ieee.org",
        "enabled": True,
        "source": "default",
    },
    {
        "id": "openreview",
        "label": "OpenReview",
        "url": "https://openreview.net",
        "enabled": True,
        "source": "default",
    },
)


@dataclass(frozen=True)
class AgentContextFile:
    agent: str
    path: str
    category: str
    source: str
    editable: bool
    deletable: bool
    size_chars: int
    content: str


@dataclass(frozen=True)
class AgentResearchSite:
    id: str
    label: str
    url: str
    enabled: bool
    source: str


@dataclass(frozen=True)
class AgentMemoryItem:
    id: str
    label: str
    text: str
    enabled: bool
    status: str
    source: str
    evidence_refs: tuple[str, ...]


def list_agent_context_files(
    agent: str,
    *,
    include_runtime_code: bool = True,
    max_chars_per_file: int = 20000,
) -> tuple[AgentContextFile, ...]:
    root = _agent_root(agent)
    files: list[AgentContextFile] = []
    for dirname in CONTEXT_DIRS:
        directory = root / dirname
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if _is_context_file(path):
                files.append(
                    _file_view(
                        agent=agent,
                        root=root,
                        path=path,
                        source="uploaded" if dirname == "uploads" else "default",
                        editable=True,
                        deletable=True,
                        max_chars=max_chars_per_file,
                    )
                )

    if include_runtime_code and root.exists():
        for path in sorted(root.glob("*.py")):
            if _is_context_file(path):
                files.append(
                    _file_view(
                        agent=agent,
                        root=root,
                        path=path,
                        source="runtime_code",
                        editable=False,
                        deletable=False,
                        max_chars=max_chars_per_file,
                    )
                )
    return tuple(files)


def load_agent_memory_items(agent: str) -> tuple[AgentMemoryItem, ...]:
    """Load approved long-term memory for an Agent.

    Pending feedback-loop candidates live under ``runs/<id>/memory`` and are
    intentionally excluded from this loader until a review path promotes them
    into the Agent config with ``status: approved``.
    """
    cfg = _load_config(agent)
    raw_items = cfg.get("memory_items")
    if not isinstance(raw_items, list):
        return ()
    items: list[AgentMemoryItem] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status", "pending_review"))
        enabled = bool(raw.get("enabled", True))
        if not enabled or status not in ACTIVE_MEMORY_STATUSES:
            continue
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        evidence_raw = raw.get("evidence_refs", [])
        evidence_refs = (
            tuple(str(item) for item in evidence_raw)
            if isinstance(evidence_raw, list)
            else ()
        )
        label = str(raw.get("label", "")).strip() or f"Memory {index + 1}"
        items.append(
            AgentMemoryItem(
                id=str(raw.get("id", "")).strip() or _site_id(label, index),
                label=label,
                text=text[:2000],
                enabled=enabled,
                status=status,
                source=str(raw.get("source", "agent_memory")),
                evidence_refs=evidence_refs,
            )
        )
    return tuple(items)


def append_approved_agent_memory(
    agent: str,
    *,
    item: Mapping[str, object],
    project: str = "mars",
    stores: KBStores | None = None,
) -> AgentMemoryItem:
    """Promote one reviewed memory item into approved long-term Agent memory."""
    cfg = _load_config(agent)
    raw_items = cfg.get("memory_items")
    items = list(raw_items) if isinstance(raw_items, list) else []
    memory_id = str(item.get("id", "")).strip()
    if not memory_id:
        raise ValueError("memory item id is required")
    evidence_raw = item.get("evidence_refs", [])
    evidence_refs = (
        [str(ref) for ref in evidence_raw]
        if isinstance(evidence_raw, list)
        else []
    )
    normalized = {
        "id": memory_id,
        "label": str(item.get("label", "") or item.get("id", memory_id)),
        "text": str(item.get("text", "")).strip(),
        "enabled": bool(item.get("enabled", True)),
        "status": "approved",
        "source": str(item.get("source", "commander_feedback")),
        "evidence_refs": evidence_refs,
        "validity_scope": str(item.get("validity_scope", "")),
        "ttl": str(item.get("ttl", "5_runs")),
    }
    if not normalized["text"]:
        raise ValueError("memory item text is required")

    replaced = False
    for index, existing in enumerate(items):
        if isinstance(existing, Mapping) and str(existing.get("id", "")) == memory_id:
            items[index] = normalized
            replaced = True
            break
    if not replaced:
        items.append(normalized)
    cfg["memory_items"] = items
    _save_config(agent, cfg)
    loaded = [item for item in load_agent_memory_items(agent) if item.id == memory_id]
    if not loaded:
        raise ValueError("approved memory was not persisted")
    sync_approved_agent_memory_to_kb(
        agent,
        loaded[0],
        project=project,
        stores=stores,
    )
    return loaded[0]


def record_agent_memory_outcome(
    agent: str,
    *,
    memory_ids: Sequence[str],
    run_id: str,
    attempt: int,
    success: bool,
) -> tuple[str, ...]:
    """Record whether approved memories helped and stale repeated failures.

    A memory item is marked ``stale`` after two consecutive negative outcomes.
    The approved-memory loader already ignores non-approved statuses, so stale
    items stop entering future Agent context without deleting the audit trail.
    """
    ids = {str(memory_id).strip() for memory_id in memory_ids if str(memory_id).strip()}
    if not ids:
        return ()
    cfg = _load_config(agent)
    raw_items = cfg.get("memory_items")
    if not isinstance(raw_items, list):
        return ()

    changed = False
    stale_ids: list[str] = []
    created = datetime.now(tz=timezone.utc).isoformat()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        memory_id = str(raw.get("id", "")).strip()
        if memory_id not in ids:
            continue
        history_raw = raw.get("outcome_history")
        history = list(history_raw) if isinstance(history_raw, list) else []
        history.append(
            {
                "run_id": run_id,
                "attempt": attempt,
                "success": success,
                "created": created,
            }
        )
        raw["outcome_history"] = history[-10:]
        if not success and _has_two_consecutive_failures(raw["outcome_history"]):
            raw["status"] = "stale"
            raw["enabled"] = False
            raw["stale_reason"] = "two_consecutive_negative_outcomes"
            raw["stale_at"] = created
            stale_ids.append(memory_id)
        changed = True

    if changed:
        cfg["memory_items"] = raw_items
        _save_config(agent, cfg)
    return tuple(stale_ids)


def update_agent_memory_status(
    agent: str,
    *,
    memory_id: str,
    status: str,
    reason: str = "",
    superseded_by: str = "",
) -> AgentMemoryItem | None:
    """Update lifecycle status for approved long-term Agent memory.

    Non-active statuses are disabled so future context loads cannot inject
    stale, rejected, or superseded lessons.
    """
    if status not in MEMORY_LIFECYCLE_STATUSES:
        raise ValueError(f"unsupported agent memory status '{status}'")
    cfg = _load_config(agent)
    raw_items = cfg.get("memory_items")
    if not isinstance(raw_items, list):
        raise ValueError(f"memory item '{memory_id}' not found")

    changed = False
    created = datetime.now(tz=timezone.utc).isoformat()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("id", "")).strip() != memory_id:
            continue
        raw["status"] = status
        raw["enabled"] = status in ACTIVE_MEMORY_STATUSES
        raw["reviewed_at"] = created
        if reason:
            raw["reviewer_note"] = reason
        if superseded_by:
            raw["superseded_by"] = superseded_by
        changed = True
        break
    if not changed:
        raise ValueError(f"memory item '{memory_id}' not found")

    cfg["memory_items"] = raw_items
    _save_config(agent, cfg)
    loaded = [item for item in load_agent_memory_items(agent) if item.id == memory_id]
    return loaded[0] if loaded else None


def create_agent_context_file(
    agent: str,
    *,
    category: str,
    filename: str,
    content: str,
) -> AgentContextFile:
    normalized_category = _normalize_category(category)
    target = _agent_root(agent) / normalized_category / _safe_filename(
        filename=filename,
        default_suffix=".py" if normalized_category == "uploads/code" else ".md",
    )
    _validate_suffix(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return _file_view(
        agent=agent,
        root=_agent_root(agent),
        path=target,
        source="uploaded" if normalized_category.startswith("uploads") else "default",
        editable=True,
        deletable=True,
        max_chars=max(len(content), 1),
    )


def update_agent_context_file(agent: str, *, path: str, content: str) -> AgentContextFile:
    target = _editable_path(agent, path)
    target.write_text(content, encoding="utf-8")
    return _file_view(
        agent=agent,
        root=_agent_root(agent),
        path=target,
        source="uploaded" if path.startswith("uploads/") else "default",
        editable=True,
        deletable=True,
        max_chars=max(len(content), 1),
    )


def delete_agent_context_file(agent: str, *, path: str) -> None:
    target = _editable_path(agent, path)
    target.unlink(missing_ok=True)


def register_agent_context_memory(
    agent: str,
    *,
    project: str = "mars",
    stores: KBStores | None = None,
) -> int:
    """Register editable Agent context files as governed MemoryRecord items."""
    written = 0
    for item in list_agent_context_files(agent, include_runtime_code=False):
        mapping = _context_memory_mapping(item.category)
        if mapping is None:
            continue
        zone, memory_type = mapping
        records = ingest_memory(
            zone=zone,
            text=item.content,
            metadata={
                "project": project,
                "kind": f"agent_context_{item.category}",
                "title": item.path,
            },
            memory_type=memory_type,
            source_path=f"agents/{agent}/{item.path}",
            agent=agent,
            schema="agent_context.v1",
            confidence=0.7,
            salience=0.45,
            approved=True,
            stores=stores,
        )
        written += len(records)
    return written


def sync_agent_context_file_to_memory(
    agent: str,
    item: AgentContextFile,
    *,
    project: str = "mars",
    stores: KBStores | None = None,
) -> int:
    """Upsert one editable Agent context file into governed KB memory."""
    mapping = _context_memory_mapping(item.category)
    if mapping is None:
        return 0
    zone, memory_type = mapping
    records = ingest_memory(
        zone=zone,
        text=item.content,
        metadata={
            "project": project,
            "kind": f"agent_context_{item.category}",
            "title": item.path,
            "agent_context_path": item.path,
            "agent_context_source": item.source,
        },
        memory_type=memory_type,
        source_path=_memory_source_path(agent, item.path),
        agent=agent,
        schema="agent_context.v1",
        confidence=0.7,
        salience=0.45,
        approved=True,
        stores=stores,
    )
    return len(records)


def delete_agent_context_memory(
    agent: str,
    *,
    path: str,
    stores: KBStores | None = None,
) -> int:
    """Delete governed KB memory associated with one Agent context file."""
    s = stores or get_stores()
    return s.delete_by_source(_memory_source_path(agent, path))


def sync_approved_agent_memory_to_kb(
    agent: str,
    item: AgentMemoryItem,
    *,
    project: str = "mars",
    stores: KBStores | None = None,
) -> int:
    """Write an approved self-evolution memory item into governed KB memory."""
    records = ingest_memory(
        zone="methodology",
        text=item.text,
        metadata={
            "project": project,
            "kind": "agent_memory_candidate",
            "title": item.label,
            "memory_item_id": item.id,
            "memory_source": item.source,
            "evidence_refs": list(item.evidence_refs),
        },
        memory_type="procedural",
        source_path=f"agents/{agent}/memory_items/{item.id}",
        agent=agent,
        schema="agent_memory_candidate.v1",
        confidence=0.65,
        salience=0.5,
        approved=True,
        stores=stores,
    )
    return len(records)


def load_agent_research_sites(agent: str) -> tuple[AgentResearchSite, ...]:
    cfg = _load_config(agent)
    raw_sites = cfg.get("research_sites")
    if not isinstance(raw_sites, list):
        raw_sites = [dict(item) for item in DEFAULT_RESEARCH_SITES]
    sites: list[AgentResearchSite] = []
    for index, item in enumerate(raw_sites):
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        label = str(item.get("label", "")).strip() or url
        sites.append(
            AgentResearchSite(
                id=str(item.get("id", "")).strip() or _site_id(label, index),
                label=label,
                url=url,
                enabled=bool(item.get("enabled", True)),
                source=str(item.get("source", "custom")) or "custom",
            )
        )
    return tuple(sites)


def save_agent_research_sites(
    agent: str,
    sites: Sequence[AgentResearchSite | Mapping[str, object]],
) -> tuple[AgentResearchSite, ...]:
    normalized: list[AgentResearchSite] = []
    for index, site in enumerate(sites):
        item = asdict(site) if isinstance(site, AgentResearchSite) else dict(site)
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        label = str(item.get("label", "")).strip() or url
        normalized.append(
            AgentResearchSite(
                id=str(item.get("id", "")).strip() or _site_id(label, index),
                label=label,
                url=url,
                enabled=bool(item.get("enabled", True)),
                source=str(item.get("source", "custom")) or "custom",
            )
        )
    cfg = _load_config(agent)
    cfg["research_sites"] = [asdict(site) for site in normalized]
    _save_config(agent, cfg)
    return tuple(normalized)


def _agent_root(agent: str) -> Path:
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent context '{agent}'")
    if agent == "commander":
        return repo_root() / "backend" / "app" / "bridge"
    return repo_root() / "backend" / "app" / "agents" / agent


def _context_memory_mapping(category: str) -> tuple[str, MemoryType] | None:
    if category == "prompts":
        return ("methodology", "procedural")
    if category == "examples":
        return ("run_archive", "episodic")
    if category == "evals":
        return ("methodology", "procedural")
    if category.startswith("uploads"):
        return ("literature", "semantic")
    return None


def _memory_source_path(agent: str, path: str) -> str:
    return f"agents/{agent}/{path.strip('/')}"


def _config_path(agent: str) -> Path:
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent context '{agent}'")
    return repo_root() / "configs" / "agent_contexts" / f"{agent}.yaml"


def _load_config(agent: str) -> dict[str, Any]:
    path = _config_path(agent)
    if not path.exists():
        return {"agent": agent, "research_sites": [dict(s) for s in DEFAULT_RESEARCH_SITES]}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {"agent": agent, "research_sites": [dict(s) for s in DEFAULT_RESEARCH_SITES]}
    return raw


def _save_config(agent: str, cfg: Mapping[str, Any]) -> None:
    path = _config_path(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(cfg), allow_unicode=True, sort_keys=False), encoding="utf-8")


def _has_two_consecutive_failures(history: object) -> bool:
    if not isinstance(history, list):
        return False
    tail = [item for item in history if isinstance(item, Mapping)][-2:]
    return len(tail) == 2 and all(not bool(item.get("success", False)) for item in tail)


def _file_view(
    *,
    agent: str,
    root: Path,
    path: Path,
    source: str,
    editable: bool,
    deletable: bool,
    max_chars: int,
) -> AgentContextFile:
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    return AgentContextFile(
        agent=agent,
        path=rel,
        category=rel.split("/", 1)[0],
        source=source,
        editable=editable,
        deletable=deletable,
        size_chars=len(text),
        content=text[:max_chars],
    )


def _is_context_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and "__pycache__" not in path.parts


def _editable_path(agent: str, rel_path: str) -> Path:
    root = _agent_root(agent)
    path = Path(rel_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("invalid context file path")
    if path.parts[0] not in EDITABLE_ROOTS:
        raise ValueError("context file is not editable")
    target = (root / path).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents:
        raise ValueError("context file escapes agent directory")
    _validate_suffix(target)
    return target


def _normalize_category(category: str) -> str:
    normalized = category.strip().strip("/")
    if normalized not in CREATE_CATEGORIES:
        raise ValueError(f"invalid context category '{category}'")
    return normalized


def _validate_suffix(path: Path) -> None:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError(f"unsupported context file type '{path.suffix}'")


def _safe_filename(*, filename: str, default_suffix: str) -> str:
    raw = Path(filename.strip()).name
    if not raw:
        raw = "context"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    if not cleaned:
        cleaned = "context"
    if Path(cleaned).suffix == "":
        cleaned += default_suffix
    return cleaned


def _site_id(label: str, index: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return base or f"site_{index + 1}"
