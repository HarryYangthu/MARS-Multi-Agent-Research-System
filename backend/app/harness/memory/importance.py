"""Deterministic memory importance scoring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_EXPLICIT_TERMS = ("记住", "别忘了", "remember", "do not forget", "keep this")
_FAILURE_TERMS = ("fail", "failed", "failure", "失败", "错误", "block", "regression")
_EVIDENCE_KEYS = ("evidence_refs", "chain_refs", "metrics", "fingerprint_hash")


@dataclass(frozen=True)
class ImportanceScore:
    score: float
    semantic_relevance: float
    evidence_strength: float
    reuse_value: float
    failure_value: float
    explicit_request: float

    def to_dict(self) -> dict[str, float]:
        return {
            "score": self.score,
            "semantic_relevance": self.semantic_relevance,
            "evidence_strength": self.evidence_strength,
            "reuse_value": self.reuse_value,
            "failure_value": self.failure_value,
            "explicit_request": self.explicit_request,
        }


def calculate_importance(
    *,
    agent: str,
    metadata: dict[str, Any],
    text: str,
) -> ImportanceScore:
    lowered = text.lower()
    semantic_relevance = _semantic_relevance(agent=agent, metadata=metadata, text=lowered)
    evidence_strength = _evidence_strength(metadata)
    reuse_value = _reuse_value(metadata=metadata, text=lowered)
    failure_value = 1.0 if any(term in lowered or term in text for term in _FAILURE_TERMS) else 0.0
    explicit_request = 1.0 if any(term in lowered or term in text for term in _EXPLICIT_TERMS) else 0.0
    score = min(
        1.0,
        semantic_relevance * 0.30
        + evidence_strength * 0.25
        + reuse_value * 0.20
        + failure_value * 0.15
        + explicit_request * 0.25,
    )
    return ImportanceScore(
        score=round(score, 4),
        semantic_relevance=semantic_relevance,
        evidence_strength=evidence_strength,
        reuse_value=reuse_value,
        failure_value=failure_value,
        explicit_request=explicit_request,
    )


def _semantic_relevance(*, agent: str, metadata: dict[str, Any], text: str) -> float:
    schema = str(metadata.get("schema", "") or "")
    if schema or str(metadata.get("project", "") or ""):
        return 0.75
    if agent and agent.lower() in text:
        return 0.65
    return 0.45


def _evidence_strength(metadata: dict[str, Any]) -> float:
    score = 0.0
    for key in _EVIDENCE_KEYS:
        value = metadata.get(key)
        if value:
            score += 0.25
    return min(1.0, score)


def _reuse_value(*, metadata: dict[str, Any], text: str) -> float:
    kind = str(metadata.get("kind", "") or "").lower()
    schema = str(metadata.get("schema", "") or metadata.get("artifact_schema", "") or "").lower()
    reusable_terms = ("method", "prompt", "eval", "run_log", "baseline", "fingerprint", "code")
    if any(term in kind or term in schema or term in text for term in reusable_terms):
        return 0.85
    return 0.4
