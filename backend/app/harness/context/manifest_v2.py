"""Context Engineering V2 manifest model and writer.

This module intentionally stays storage-agnostic: callers pass a run root
``Path`` when they want durable files. The manifest records the exact message
preview prepared before an LLM call, plus the segment-level selection and
compression decisions that produced it.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.harness.llm.provider_base import Message

ContextKind = Literal[
    "system",
    "project",
    "task",
    "schema",
    "upstream",
    "kb",
    "tool",
    "memory",
    "self_context",
    "research_site",
]
ContextPriority = Literal["critical", "high", "medium", "low"]
CompressionKind = Literal["none", "summary", "reference", "relevance_prune", "trimmed"]
RiskFlag = Literal[
    "poisoning",
    "distraction",
    "confusion",
    "clash",
    "lost_in_middle",
]

SCHEMA_ID = "context_manifest.v2"
PREVIEW_CHARS = 1200


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class ContextSegment:
    id: str
    kind: ContextKind
    title: str
    source_ref: str
    text: str
    priority: ContextPriority
    selection_reason: str
    compression: CompressionKind = "none"
    risk_flags: list[RiskFlag] = field(default_factory=list)
    raw_ref: str | None = None

    @property
    def content_hash(self) -> str:
        return content_hash(self.text)

    @property
    def tokens_estimated(self) -> int:
        return estimate_tokens(self.text)

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "source_ref": self.source_ref,
            "content_hash": self.content_hash,
            "tokens_estimated": self.tokens_estimated,
            "priority": self.priority,
            "selection_reason": self.selection_reason,
            "compression": self.compression,
            "risk_flags": list(self.risk_flags),
            "text_preview": self.text[:PREVIEW_CHARS],
            "raw_ref": self.raw_ref,
        }


@dataclass(frozen=True)
class ContextBudget:
    max_tokens: int
    target_tokens: int
    used_tokens: int

    @property
    def over_budget(self) -> bool:
        return self.used_tokens > self.max_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "max": self.max_tokens,
            "target": self.target_tokens,
            "used": self.used_tokens,
            "over_budget": self.over_budget,
        }


@dataclass
class ContextManifestV2:
    run_id: str
    agent: str
    node_key: str
    project: str
    output_schema: str
    budget: ContextBudget
    segments: list[ContextSegment]
    render_order: list[str]
    messages_preview: list[dict[str, str]]
    diagnostics: dict[str, Any]
    raw_refs: list[str]
    purpose: str = "draft"
    manifest_id: str = ""
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "manifest_id": self.manifest_id,
            "run_id": self.run_id,
            "agent": self.agent,
            "node_key": self.node_key,
            "project": self.project,
            "output_schema": self.output_schema,
            "purpose": self.purpose,
            "created_at": self.created_at,
            "budget": self.budget.to_dict(),
            "segments": [segment.to_manifest_dict() for segment in self.segments],
            "render_order": list(self.render_order),
            "messages_preview": list(self.messages_preview),
            "diagnostics": dict(self.diagnostics),
            "raw_refs": list(self.raw_refs),
        }


def estimate_tokens(text: str) -> int:
    """Cheap deterministic estimate used throughout V0/V2 tests."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def message_previews(messages: list[Message], *, limit: int = PREVIEW_CHARS) -> list[dict[str, str]]:
    return [{"role": msg.role, "content": msg.content[:limit]} for msg in messages]


def messages_token_estimate(messages: list[Message]) -> int:
    return sum(estimate_tokens(msg.content) for msg in messages)


def write_manifest_v2(*, run_root: Path, manifest: ContextManifestV2) -> Path:
    cdir = run_root / "context" / "agents" / _safe_id(manifest.agent) / "manifests"
    cdir.mkdir(parents=True, exist_ok=True)
    if not manifest.manifest_id:
        manifest.manifest_id = _manifest_id(
            agent=manifest.agent,
            node_key=manifest.node_key,
            purpose=manifest.purpose,
        )
    path = cdir / f"{manifest.manifest_id}.json"
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _update_index(run_root / "context", manifest=manifest, path=path)
    _update_agent_index(cdir, manifest=manifest, path=path)
    return path


def manifest_file_for_id(*, run_root: Path, manifest_id: str) -> Path:
    clean = _safe_id(manifest_id)
    if clean.endswith(".json"):
        clean = clean[:-5]
    indexed = _manifest_file_from_index(run_root=run_root, manifest_id=clean)
    if indexed is not None:
        return indexed
    legacy = run_root / "context" / f"{clean}.json"
    if legacy.exists():
        return legacy
    matches = sorted((run_root / "context" / "agents").glob(f"*/manifests/{clean}.json"))
    if matches:
        return matches[0]
    return legacy


def _update_index(context_dir: Path, *, manifest: ContextManifestV2, path: Path) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    index_path = context_dir / "context_manifest.v2.json"
    index: dict[str, Any] = {
        "schema": "context_manifest_index.v2",
        "updated_at": _now(),
        "manifests": [],
    }
    if index_path.exists():
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
        if isinstance(raw, dict):
            manifests = raw.get("manifests", [])
            index["manifests"] = manifests if isinstance(manifests, list) else []
    record = {
        "manifest_id": manifest.manifest_id,
        "agent": manifest.agent,
        "node_key": manifest.node_key,
        "purpose": manifest.purpose,
        "created_at": manifest.created_at,
        "path": path.relative_to(context_dir.parent).as_posix(),
        "used_tokens": manifest.budget.used_tokens,
        "over_budget": manifest.budget.over_budget,
        "risk_counts": manifest.diagnostics.get("risk_counts", {}),
    }
    existing = [
        item
        for item in index["manifests"]
        if isinstance(item, dict) and item.get("manifest_id") != manifest.manifest_id
    ]
    existing.append(record)
    index["manifests"] = existing[-500:]
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _update_agent_index(cdir: Path, *, manifest: ContextManifestV2, path: Path) -> None:
    index_path = cdir / "context_manifest.v2.json"
    index: dict[str, Any] = {
        "schema": "agent_context_manifest_index.v2",
        "agent": manifest.agent,
        "updated_at": _now(),
        "manifests": [],
    }
    if index_path.exists():
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
        if isinstance(raw, dict):
            manifests = raw.get("manifests", [])
            index["manifests"] = manifests if isinstance(manifests, list) else []
    record = {
        "manifest_id": manifest.manifest_id,
        "node_key": manifest.node_key,
        "purpose": manifest.purpose,
        "created_at": manifest.created_at,
        "path": path.relative_to(cdir).as_posix(),
        "used_tokens": manifest.budget.used_tokens,
        "over_budget": manifest.budget.over_budget,
        "risk_counts": manifest.diagnostics.get("risk_counts", {}),
    }
    existing = [
        item
        for item in index["manifests"]
        if isinstance(item, dict) and item.get("manifest_id") != manifest.manifest_id
    ]
    existing.append(record)
    index["manifests"] = existing[-500:]
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _manifest_file_from_index(*, run_root: Path, manifest_id: str) -> Path | None:
    index_path = run_root / "context" / "context_manifest.v2.json"
    if not index_path.exists():
        return None
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    manifests = raw.get("manifests", []) if isinstance(raw, dict) else []
    if not isinstance(manifests, list):
        return None
    for item in manifests:
        if not isinstance(item, dict):
            continue
        if str(item.get("manifest_id", "")) != manifest_id:
            continue
        rel = Path(str(item.get("path", "")))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            return None
        return run_root / rel
    return None


def _manifest_id(*, agent: str, node_key: str, purpose: str) -> str:
    base = _safe_id(".".join([SCHEMA_ID, node_key or agent, purpose]))
    return f"{base}.{uuid.uuid4().hex[:8]}"


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "context_manifest.v2"
