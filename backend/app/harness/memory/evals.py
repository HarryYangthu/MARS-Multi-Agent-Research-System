"""Memory-specific deterministic evaluation helpers."""
from __future__ import annotations

from dataclasses import dataclass

from app.harness.kb.selector import select_memory
from app.harness.kb.stores import KBStores


@dataclass(frozen=True)
class MemoryEvalResult:
    name: str
    score: float
    passed: bool
    details: dict[str, object]


def evaluate_retrieval_precision(
    *,
    query: str,
    expected_record_ids: set[str],
    zones: list[str],
    stores: KBStores,
    top_k: int = 5,
) -> MemoryEvalResult:
    hits = select_memory(query=query, zones=zones, top_k=top_k, stores=stores)
    returned = [hit.record.id for hit in hits]
    if not returned:
        score = 0.0
    else:
        score = len(set(returned) & expected_record_ids) / len(returned)
    return MemoryEvalResult(
        name="memory.retrieval_precision",
        score=round(score, 4),
        passed=score >= 0.6,
        details={"returned": returned, "expected": sorted(expected_record_ids)},
    )


def evaluate_pollution(
    *,
    query: str,
    zones: list[str],
    stores: KBStores,
    top_k: int = 10,
) -> MemoryEvalResult:
    hits = select_memory(query=query, zones=zones, top_k=top_k, stores=stores)
    polluted = [
        hit.record.id
        for hit in hits
        if hit.memory.is_mock or hit.memory.superseded_by or not hit.memory.approved
    ]
    score = 1.0 if not polluted else 0.0
    return MemoryEvalResult(
        name="memory.pollution_guard",
        score=score,
        passed=not polluted,
        details={"polluted": polluted, "returned": [hit.record.id for hit in hits]},
    )
