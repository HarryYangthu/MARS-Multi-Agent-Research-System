"""Layered exact, structural, behavioural, and semantic novelty checks."""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.harness.discovery.canonical import canonical_value, stable_hash


class DuplicateKind(str, Enum):
    EXACT = "exact"
    STRUCTURAL = "structural"
    BEHAVIORAL = "behavioral"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class FingerprintBundle:
    exact: str
    structural: str = ""
    behavioral: str = ""
    semantic: str = ""


@dataclass(frozen=True)
class NoveltyDecision:
    is_novel: bool
    duplicate_kind: DuplicateKind | None = None
    matching_candidate_id: str = ""
    semantic_similarity: float | None = None


def exact_fingerprint(value: Any) -> str:
    return stable_hash(value)


def normalized_ast_fingerprint(source: str) -> str:
    """Hash Python syntax while ignoring formatting and source locations."""
    tree = ast.parse(source)
    normalized = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return stable_hash(normalized)


def behavior_fingerprint(observation: Any, *, precision: int = 8) -> str:
    """Hash deterministic outputs after bounded numeric quantisation."""
    if precision < 0:
        raise ValueError("precision must be non-negative")
    return stable_hash(_quantize(canonical_value(observation), precision=precision))


def semantic_fingerprint(embedding: tuple[float, ...], *, precision: int = 8) -> str:
    normalized = _normalized_embedding(embedding)
    return stable_hash([round(value, precision) for value in normalized])


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embeddings must be non-empty and have equal dimensions")
    left_norm = _normalized_embedding(left)
    right_norm = _normalized_embedding(right)
    return sum(a * b for a, b in zip(left_norm, right_norm, strict=True))


class NoveltyIndex:
    """In-memory pure index; persistence is deliberately owned by another layer."""

    def __init__(self, *, semantic_threshold: float = 0.98) -> None:
        if not 0.0 <= semantic_threshold <= 1.0:
            raise ValueError("semantic_threshold must be in [0, 1]")
        self.semantic_threshold = semantic_threshold
        self._fingerprints: dict[str, FingerprintBundle] = {}
        self._embeddings: dict[str, tuple[float, ...]] = {}

    def assess(
        self,
        fingerprints: FingerprintBundle,
        *,
        embedding: tuple[float, ...] | None = None,
    ) -> NoveltyDecision:
        for candidate_id in sorted(self._fingerprints):
            existing = self._fingerprints[candidate_id]
            if fingerprints.exact and fingerprints.exact == existing.exact:
                return NoveltyDecision(False, DuplicateKind.EXACT, candidate_id)
        for candidate_id in sorted(self._fingerprints):
            existing = self._fingerprints[candidate_id]
            if fingerprints.structural and fingerprints.structural == existing.structural:
                return NoveltyDecision(False, DuplicateKind.STRUCTURAL, candidate_id)
        for candidate_id in sorted(self._fingerprints):
            existing = self._fingerprints[candidate_id]
            if fingerprints.behavioral and fingerprints.behavioral == existing.behavioral:
                return NoveltyDecision(False, DuplicateKind.BEHAVIORAL, candidate_id)
        for candidate_id in sorted(self._fingerprints):
            existing = self._fingerprints[candidate_id]
            if fingerprints.semantic and fingerprints.semantic == existing.semantic:
                return NoveltyDecision(
                    False,
                    DuplicateKind.SEMANTIC,
                    candidate_id,
                    semantic_similarity=1.0,
                )
        if embedding is not None:
            for candidate_id in sorted(self._embeddings):
                similarity = cosine_similarity(embedding, self._embeddings[candidate_id])
                if similarity >= self.semantic_threshold:
                    return NoveltyDecision(
                        False,
                        DuplicateKind.SEMANTIC,
                        candidate_id,
                        semantic_similarity=similarity,
                    )
        return NoveltyDecision(True)

    def register(
        self,
        candidate_id: str,
        fingerprints: FingerprintBundle,
        *,
        embedding: tuple[float, ...] | None = None,
        require_novel: bool = True,
    ) -> NoveltyDecision:
        if not candidate_id:
            raise ValueError("candidate_id is required")
        decision = self.assess(fingerprints, embedding=embedding)
        if require_novel and not decision.is_novel:
            return decision
        self._fingerprints[candidate_id] = fingerprints
        if embedding is not None:
            self._embeddings[candidate_id] = _normalized_embedding(embedding)
        return decision


def _normalized_embedding(embedding: tuple[float, ...]) -> tuple[float, ...]:
    if not embedding or any(not math.isfinite(value) for value in embedding):
        raise ValueError("embedding values must be finite")
    norm = math.sqrt(sum(value * value for value in embedding))
    if norm == 0.0:
        raise ValueError("embedding norm must be positive")
    return tuple(value / norm for value in embedding)


def _quantize(value: Any, *, precision: int) -> Any:
    if isinstance(value, float):
        return round(value, precision) if math.isfinite(value) else value
    if isinstance(value, list):
        return [_quantize(item, precision=precision) for item in value]
    if isinstance(value, dict):
        return {key: _quantize(item, precision=precision) for key, item in value.items()}
    return value
