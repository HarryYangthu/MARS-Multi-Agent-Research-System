"""Baseline matcher.

Looks up the run_archive zone for plans similar to the candidate plan.
A match above ``match_threshold`` triggers the HITL "Reuse?" gate
(DESIGN §7.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.harness.kb.embedder import cosine, embed
from app.harness.kb.stores import KBRecord, KBStores, get_stores


@dataclass
class BaselineMatch:
    matched_run_id: str | None
    match_score: float
    record: KBRecord | None

    def above(self, threshold: float) -> bool:
        return self.match_score >= threshold


def _plan_signature(plan: dict[str, Any]) -> str:
    """Stringify the plan into a deterministic representation for embedding."""
    bits: list[str] = []
    for key in ("project", "variables", "metrics", "ablations"):
        bits.append(f"{key}={plan.get(key)}")
    return " | ".join(bits)


def find_match(
    *,
    plan: dict[str, Any],
    threshold: float = 0.85,
    stores: KBStores | None = None,
) -> BaselineMatch:
    s = stores or get_stores()
    project = str(plan.get("project", "") or "")
    zone = s.zone("run_archive")
    records = zone.all(filters={"project": project} if project else None,
                       exclude_mock=True, exclude_superseded=True)
    if not records:
        return BaselineMatch(matched_run_id=None, match_score=0.0, record=None)

    sig = _plan_signature(plan)
    # Bag-of-words embeddings can collapse distinct numeric configurations.
    # Prefer a byte-identical canonical plan before approximate retrieval.
    for record in records:
        if record.text == sig:
            return BaselineMatch(matched_run_id=record.metadata.get("run_id"),
                                 match_score=1.0, record=record)
    q_vec = embed(sig)
    best_score = -1.0
    best_rec: KBRecord | None = None
    for rec in records:
        score = cosine(q_vec, rec.embedding)
        if score > best_score:
            best_score = score
            best_rec = rec
    if best_rec is None:
        return BaselineMatch(matched_run_id=None, match_score=0.0, record=None)
    return BaselineMatch(
        matched_run_id=best_rec.metadata.get("run_id"),
        match_score=best_score,
        record=best_rec if best_score >= threshold else None,
    )
