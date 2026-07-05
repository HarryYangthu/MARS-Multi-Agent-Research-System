"""MemorySelector v2: filtered, scored, summary-oriented retrieval."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from app.harness.kb.config import current_profile, selector_config
from app.harness.kb.embedder import cosine, embed, tokenize
from app.harness.kb.models import MemoryRecord, memory_from_kb_record
from app.harness.kb.stores import KBRecord, KBStores, MAIN_ZONES, get_stores


@dataclass(frozen=True)
class MemoryHit:
    score: float
    similarity: float
    record: KBRecord
    memory: MemoryRecord
    injected_text: str


def select_memory(
    *,
    query: str,
    zones: Sequence[str] | None = None,
    top_k: int = 5,
    memory_type: str | None = None,
    project: str | None = None,
    include_mock: bool = False,
    include_superseded: bool = False,
    approved_only: bool = True,
    update_access: bool = True,
    stores: KBStores | None = None,
) -> list[MemoryHit]:
    selected_zones = list(zones or MAIN_ZONES)
    s = stores or get_stores()
    cfg = selector_config()
    weights_raw = cfg.get("weights", {})
    weights = weights_raw if isinstance(weights_raw, dict) else {}
    q_vec = embed(query)
    q_terms = set(tokenize(query))
    graph_related = _graph_related_ids(query=query, stores=s)
    hits: list[MemoryHit] = []
    filters: dict[str, Any] = {}
    if memory_type:
        filters["memory_type"] = memory_type
    if project:
        filters["project"] = project
    if approved_only:
        filters["approved"] = True
    for zone in selected_zones:
        for record in s.zone(zone).all(
            filters=filters,
            exclude_superseded=not include_superseded,
            exclude_mock=not include_mock,
        ):
            memory = memory_from_kb_record(
                record_id=record.id,
                zone=record.zone,
                text=record.text,
                metadata=record.metadata,
            )
            sim = cosine(q_vec, record.embedding)
            lexical = _lexical_overlap(q_terms, set(tokenize(record.text)))
            combined_similarity = min(1.0, max(0.0, sim + lexical * 0.1))
            graph_bonus = 0.08 if record.id in graph_related else 0.0
            score = (
                min(1.0, combined_similarity + graph_bonus)
                * _weight(weights, "similarity", 0.4)
                + _recency(memory.valid_from) * _weight(weights, "recency", 0.2)
                + memory.confidence * _weight(weights, "confidence", 0.2)
                + (1.0 if memory.eval_status.passed else 0.0)
                * _weight(weights, "eval_status", 0.1)
                + memory.salience * _weight(weights, "salience", 0.1)
            )
            hits.append(
                MemoryHit(
                    score=score,
                    similarity=combined_similarity,
                    record=record,
                    memory=memory,
                    injected_text=memory.summary or memory.text[:700],
                )
            )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    selected = hits[:top_k]
    if update_access:
        _record_access(selected, stores=s)
    return selected


def default_include_mock() -> bool:
    return current_profile() == "dev_e2e"


def _weight(raw: dict[Any, Any], key: str, default: float) -> float:
    try:
        return float(raw.get(key, default))
    except (TypeError, ValueError):
        return default


def _lexical_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / math.sqrt(len(left) * len(right))


def _recency(valid_from: str) -> float:
    try:
        dt = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
    except ValueError:
        return 0.4
    age_days = max(0.0, (datetime.now(tz=timezone.utc) - dt).total_seconds() / 86400)
    return math.exp(-age_days / 180.0)


def _record_access(hits: list[MemoryHit], *, stores: KBStores) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    for hit in hits:
        raw_count = hit.record.metadata.get("access_count", 0)
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 0
        stores.update_metadata(
            hit.record.zone,
            hit.record.id,
            {
                "access_count": count + 1,
                "last_accessed_at": now,
            },
        )


def _graph_related_ids(*, query: str, stores: KBStores) -> set[str]:
    try:
        from app.harness.memory.semantic import related_record_ids

        return set(related_record_ids(base=stores.base, query=query))
    except Exception:
        return set()
