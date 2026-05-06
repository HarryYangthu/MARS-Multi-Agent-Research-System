"""Cross-zone retrieval."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.harness.kb.stores import KBRecord, KBStores, get_stores


@dataclass
class Hit:
    score: float
    record: KBRecord


def query(
    *,
    query: str,
    zones: Sequence[str],
    top_k: int = 5,
    stores: KBStores | None = None,
) -> list[Hit]:
    s = stores or get_stores()
    raw = s.query_across(zones=list(zones), query=query, top_k=top_k)
    return [Hit(score=score, record=rec) for score, rec in raw]
