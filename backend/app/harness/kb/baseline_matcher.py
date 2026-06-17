"""Baseline matcher.

Looks up the run_archive zone for plans similar to the candidate plan.
A match above ``match_threshold`` triggers the HITL "Reuse?" gate
(DESIGN §7.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.harness.kb.embedder import cosine, embed
from app.harness.kb.profiles import read_baseline_current
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
    profile = read_baseline_current(project, base=s.base) if project else None
    if profile is not None:
        signature = str(profile.get("signature", "") or profile.get("text", ""))
        if not signature and isinstance(profile.get("plan"), dict):
            signature = _plan_signature(profile["plan"])
        if signature:
            score = cosine(embed(_plan_signature(plan)), embed(signature))
            run_id = profile.get("run_id") or profile.get("matched_run_id")
            return BaselineMatch(
                matched_run_id=str(run_id) if run_id else None,
                match_score=score,
                record=None,
            )

    zone = s.zone("run_archive")
    records = zone.all(exclude_mock=True, exclude_superseded=True)
    if not records:
        return BaselineMatch(matched_run_id=None, match_score=0.0, record=None)

    sig = _plan_signature(plan)
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
