"""Meta-review stage."""
from __future__ import annotations

from collections.abc import Sequence

from app.agents.idea.discovery.backend import DiscoveryRoleBackend
from app.agents.idea.discovery.models import (
    DiscoveryContext,
    stable_id,
    stable_time,
)
from app.harness.discovery import HypothesisRecord, MetaReviewRecord, ReflectionRecord


async def build_meta_review(
    *,
    backend: DiscoveryRoleBackend,
    context: DiscoveryContext,
    round_index: int,
    hypotheses: Sequence[HypothesisRecord],
    reflections: Sequence[ReflectionRecord],
) -> MetaReviewRecord:
    draft = await backend.meta_review(
        context,
        round_index=round_index,
        hypotheses=hypotheses,
        reflections=reflections,
    )
    review_id = stable_id(
        "meta",
        context.run_id,
        round_index,
        draft.recurring_errors,
        draft.successful_patterns,
    )
    return MetaReviewRecord(
        meta_review_id=review_id,
        run_id=context.run_id,
        round_index=round_index,
        recurring_errors=draft.recurring_errors,
        successful_patterns=draft.successful_patterns,
        evidence_gaps=draft.evidence_gaps,
        unexplored_regions=draft.unexplored_regions,
        next_round_guidance=draft.next_round_guidance,
        created_at=stable_time(context.run_id, review_id),
    )
