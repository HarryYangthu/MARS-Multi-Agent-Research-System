"""Balanced pairwise scheduling and Elo updates."""
from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations
from math import pow

from app.agents.idea.discovery.backend import DiscoveryRoleBackend
from app.agents.idea.discovery.models import (
    DiscoveryContext,
    stable_hash,
    stable_id,
    stable_time,
)
from app.harness.discovery import HypothesisRecord, PairwiseMatchRecord


async def rank_hypotheses(
    *,
    backend: DiscoveryRoleBackend,
    context: DiscoveryContext,
    hypotheses: Sequence[HypothesisRecord],
    existing_matches: Sequence[PairwiseMatchRecord],
    round_index: int,
    max_new_matches: int,
    elo_k: float,
) -> tuple[tuple[HypothesisRecord, ...], tuple[PairwiseMatchRecord, ...]]:
    if max_new_matches <= 0:
        return tuple(hypotheses), ()
    pairs = _schedule_pairs(
        hypotheses,
        existing_matches=existing_matches,
        round_index=round_index,
        limit=max_new_matches,
    )
    if not pairs:
        return tuple(hypotheses), ()
    decisions = await backend.judge(context, pairs)
    if len(decisions) != len(pairs):
        raise RuntimeError("pairwise backend returned an unexpected decision count")

    ratings = {item.hypothesis_id: item.elo for item in hypotheses}
    matches: list[PairwiseMatchRecord] = []
    for index, ((left, right), decision) in enumerate(zip(pairs, decisions, strict=True)):
        left_before = ratings[left.hypothesis_id]
        right_before = ratings[right.hypothesis_id]
        expected_left = 1.0 / (1.0 + pow(10.0, (right_before - left_before) / 400.0))
        if decision.outcome == "left":
            actual_left = 1.0
        elif decision.outcome == "right":
            actual_left = 0.0
        else:
            actual_left = 0.5
        left_after = left_before + elo_k * (actual_left - expected_left)
        right_after = right_before + elo_k * ((1.0 - actual_left) - (1.0 - expected_left))
        ratings[left.hypothesis_id] = left_after
        ratings[right.hypothesis_id] = right_after
        match_id = stable_id(
            "match",
            context.run_id,
            round_index,
            index,
            left.hypothesis_id,
            right.hypothesis_id,
        )
        matches.append(
            PairwiseMatchRecord(
                match_id=match_id,
                run_id=context.run_id,
                round_index=round_index,
                left_id=left.hypothesis_id,
                right_id=right.hypothesis_id,
                outcome=decision.outcome,
                reason=decision.reason,
                evidence_refs=decision.evidence_refs,
                left_rating_before=left_before,
                right_rating_before=right_before,
                left_rating_after=left_after,
                right_rating_after=right_after,
                created_at=stable_time(context.run_id, match_id),
            )
        )
    updated = tuple(
        item.model_copy(update={"elo": ratings[item.hypothesis_id]})
        for item in hypotheses
    )
    return updated, tuple(matches)


def select_top_hypotheses(
    hypotheses: Sequence[HypothesisRecord], *, top_k: int
) -> tuple[str, ...]:
    legal = sorted(
        (item for item in hypotheses if not item.blocked),
        key=lambda item: (-item.elo, item.hypothesis_id),
    )
    selected: list[HypothesisRecord] = []
    seen_clusters: set[str] = set()
    for item in legal:
        cluster = item.cluster_id or item.hypothesis_id
        if cluster in seen_clusters:
            continue
        selected.append(item)
        seen_clusters.add(cluster)
        if len(selected) == top_k:
            break
    if len(selected) < top_k:
        selected_ids = {item.hypothesis_id for item in selected}
        for item in legal:
            if item.hypothesis_id in selected_ids:
                continue
            selected.append(item)
            if len(selected) == top_k:
                break
    return tuple(item.hypothesis_id for item in selected)


def _schedule_pairs(
    hypotheses: Sequence[HypothesisRecord],
    *,
    existing_matches: Sequence[PairwiseMatchRecord],
    round_index: int,
    limit: int,
) -> tuple[tuple[HypothesisRecord, HypothesisRecord], ...]:
    legal = [item for item in hypotheses if not item.blocked]
    seen = {
        frozenset((item.left_id, item.right_id)) for item in existing_matches
    }
    candidates: list[tuple[tuple[float, ...], HypothesisRecord, HypothesisRecord]] = []
    for first, second in combinations(legal, 2):
        if frozenset((first.hypothesis_id, second.hypothesis_id)) in seen:
            continue
        involves_new = float(
            first.round_index == round_index or second.round_index == round_index
        )
        cross_cluster = float(first.cluster_id != second.cluster_id)
        rating_distance = abs(first.elo - second.elo)
        stable_order = int(
            stable_hash(round_index, first.hypothesis_id, second.hypothesis_id)[:8],
            16,
        )
        priority = (-involves_new, -cross_cluster, rating_distance, float(stable_order))
        candidates.append((priority, first, second))
    candidates.sort(key=lambda item: item[0])
    output: list[tuple[HypothesisRecord, HypothesisRecord]] = []
    for index, (_priority, first, second) in enumerate(candidates[:limit]):
        if index % 2:
            output.append((second, first))
        else:
            output.append((first, second))
    return tuple(output)
