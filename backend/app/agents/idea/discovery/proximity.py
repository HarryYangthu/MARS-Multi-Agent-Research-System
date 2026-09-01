"""Deterministic local proximity graph and duplicate rejection."""
from __future__ import annotations

import re
from collections.abc import Sequence

from app.agents.idea.discovery.models import (
    ProximityEdge,
    ProximityGraph,
    stable_id,
)
from app.harness.discovery import HypothesisRecord


def build_proximity_graph(
    hypotheses: Sequence[HypothesisRecord],
    *,
    round_index: int,
    threshold: float,
) -> tuple[tuple[HypothesisRecord, ...], ProximityGraph, tuple[str, ...]]:
    parents: dict[str, str] = {item.hypothesis_id: item.hypothesis_id for item in hypotheses}
    normalized = {
        item.hypothesis_id: _normalize_statement(item.statement) for item in hypotheses
    }
    features = {
        item.hypothesis_id: _features(item.statement) for item in hypotheses
    }
    edges: list[ProximityEdge] = []
    duplicate_ids: list[str] = []
    ordered = list(hypotheses)
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            exact = normalized[left.hypothesis_id] == normalized[right.hypothesis_id]
            similarity = 1.0 if exact else _jaccard(
                features[left.hypothesis_id], features[right.hypothesis_id]
            )
            if exact or similarity >= threshold:
                edges.append(
                    ProximityEdge(
                        left_id=left.hypothesis_id,
                        right_id=right.hypothesis_id,
                        similarity=round(similarity, 6),
                        exact_duplicate=exact,
                    )
                )
                _union(parents, left.hypothesis_id, right.hypothesis_id)
            if exact:
                duplicate_ids.append(right.hypothesis_id)

    grouped: dict[str, list[str]] = {}
    for item in ordered:
        root = _find(parents, item.hypothesis_id)
        grouped.setdefault(root, []).append(item.hypothesis_id)
    clusters: dict[str, tuple[str, ...]] = {}
    cluster_for: dict[str, str] = {}
    for members in sorted(grouped.values(), key=lambda values: sorted(values)[0]):
        ordered_members = tuple(sorted(members))
        cluster_id = stable_id("cluster", round_index, ordered_members)
        clusters[cluster_id] = ordered_members
        for hypothesis_id in ordered_members:
            cluster_for[hypothesis_id] = cluster_id

    duplicate_set = set(duplicate_ids)
    updated = tuple(
        item.model_copy(
            update={
                "cluster_id": cluster_for[item.hypothesis_id],
                "blocked": item.blocked or item.hypothesis_id in duplicate_set,
            }
        )
        for item in ordered
    )
    graph = ProximityGraph(
        round_index=round_index,
        clusters=clusters,
        edges=tuple(edges),
    )
    return updated, graph, tuple(sorted(duplicate_set))


def _normalize_statement(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())


def _features(text: str) -> set[str]:
    normalized = _normalize_statement(text)
    grams = {
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
    }
    words = set(re.findall(r"[a-z0-9_]{3,}", text.lower()))
    return grams | words


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _find(parents: dict[str, str], item: str) -> str:
    current = item
    while parents[current] != current:
        current = parents[current]
    root = current
    current = item
    while parents[current] != current:
        next_item = parents[current]
        parents[current] = root
        current = next_item
    return root


def _union(parents: dict[str, str], left: str, right: str) -> None:
    left_root = _find(parents, left)
    right_root = _find(parents, right)
    if left_root == right_root:
        return
    keep, merge = sorted((left_root, right_root))
    parents[merge] = keep
