"""Deterministic semantic conflict hints for memory writes."""
from __future__ import annotations

from dataclasses import dataclass

from app.harness.memory.semantic import extract_semantics


@dataclass(frozen=True)
class ConflictAssessment:
    decision: str
    overlap: float
    reason: str


def assess_conflict(*, old_text: str, new_text: str) -> ConflictAssessment:
    old_entities = set(extract_semantics(old_text, {}).entities)
    new_entities = set(extract_semantics(new_text, {}).entities)
    if not old_entities or not new_entities:
        return ConflictAssessment(decision="new", overlap=0.0, reason="no shared entities")
    overlap = len(old_entities & new_entities) / max(1, len(old_entities | new_entities))
    old_neg = _has_negation(old_text)
    new_neg = _has_negation(new_text)
    if overlap > 0.45 and old_neg != new_neg:
        return ConflictAssessment(decision="conflict", overlap=round(overlap, 4), reason="shared entities with opposing polarity")
    if overlap > 0.75:
        return ConflictAssessment(decision="duplicate_or_update", overlap=round(overlap, 4), reason="high entity overlap")
    if overlap > 0.35:
        return ConflictAssessment(decision="complementary", overlap=round(overlap, 4), reason="partial entity overlap")
    return ConflictAssessment(decision="new", overlap=round(overlap, 4), reason="low entity overlap")


def _has_negation(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered or term in text for term in ("not ", "never", "不能", "不要", "失败", "不可"))
