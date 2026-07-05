"""Lightweight semantic memory graph.

This is intentionally file-backed and deterministic. It extracts coarse
entities/relations from governed memory records so retrieval can later combine
vector recall with relation expansion without requiring Neo4j or an LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.kb.backends import KBRecord

_METRIC_RE = re.compile(r"\b(?:RES|PIM|APE|loss|latency|tokens|pass_rate|score)\b", re.I)
_IDENT_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b")
_PATH_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+")


@dataclass(frozen=True)
class SemanticExtraction:
    entities: tuple[str, ...]
    relations: tuple[dict[str, str], ...]


def extract_semantics(text: str, metadata: dict[str, Any]) -> SemanticExtraction:
    entities: set[str] = set()
    for key in ("agent", "project", "artifact_schema", "schema", "kind", "memory_type"):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            entities.add(f"{key}:{value}")
    for metric in _METRIC_RE.findall(text):
        entities.add(f"metric:{metric.lower()}")
    for path in _PATH_RE.findall(text):
        entities.add(f"path:{path}")
    for ident in _IDENT_RE.findall(text):
        if len(ident) >= 4 and ident.lower() not in {"this", "that", "with", "from"}:
            entities.add(f"term:{ident.lower()}")
    source = str(metadata.get("source_path", "") or metadata.get("record_id", "") or "")
    relations: list[dict[str, str]] = []
    for entity in sorted(entities)[:40]:
        relations.append({"source": source, "relation": "mentions", "target": entity})
    return SemanticExtraction(entities=tuple(sorted(entities)[:80]), relations=tuple(relations))


def index_semantic_record(*, base: Path, record: KBRecord) -> None:
    path = base / "_semantic_graph.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = _load_graph(path)
    extraction = extract_semantics(record.text, record.metadata)
    now = datetime.now(tz=timezone.utc).isoformat()
    graph["updated_at"] = now
    graph["records"][record.id] = {
        "id": record.id,
        "zone": record.zone,
        "source_path": str(record.metadata.get("source_path", "") or ""),
        "entities": list(extraction.entities),
        "relations": list(extraction.relations),
        "updated_at": now,
    }
    for entity in extraction.entities:
        graph.setdefault("entity_index", {}).setdefault(entity, [])
        ids = graph["entity_index"][entity]
        if record.id not in ids:
            ids.append(record.id)
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")


def related_record_ids(*, base: Path, query: str, limit: int = 20) -> list[str]:
    graph = _load_graph(base / "_semantic_graph.json")
    extraction = extract_semantics(query, {})
    ids: list[str] = []
    for entity in extraction.entities:
        for record_id in graph.get("entity_index", {}).get(entity, []):
            if record_id not in ids:
                ids.append(record_id)
            if len(ids) >= limit:
                return ids
    return ids


def _load_graph(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "memory_semantic_graph.v1",
            "updated_at": "",
            "records": {},
            "entity_index": {},
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("schema", "memory_semantic_graph.v1")
    raw.setdefault("updated_at", "")
    raw.setdefault("records", {})
    raw.setdefault("entity_index", {})
    return raw
