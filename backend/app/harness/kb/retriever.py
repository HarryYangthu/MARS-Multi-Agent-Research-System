"""Cross-zone retrieval."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.harness.kb.stores import KBRecord, KBStores, get_stores
from app.harness.kb.selector import select_memory


@dataclass
class Hit:
    score: float
    record: KBRecord


def query(
    *,
    query: str,
    zones: Sequence[str],
    top_k: int = 5,
    project: str | None = None,
    memory_type: str | None = None,
    include_mock: bool = False,
    include_superseded: bool = False,
    stores: KBStores | None = None,
) -> list[Hit]:
    s = stores or get_stores()
    selected = select_memory(
        query=query,
        zones=list(zones),
        top_k=top_k,
        project=project,
        memory_type=memory_type,
        include_mock=include_mock,
        include_superseded=include_superseded,
        stores=s,
    )
    return [Hit(score=hit.score, record=hit.record) for hit in selected]
