"""Generation stage for Idea deep discovery."""
from __future__ import annotations

from app.agents.idea.discovery.backend import DiscoveryRoleBackend
from app.agents.idea.discovery.models import (
    DeepDiscoveryConfig,
    DiscoveryContext,
    stable_id,
    stable_time,
)
from app.harness.discovery import HypothesisRecord


async def generate_initial_hypotheses(
    *,
    backend: DiscoveryRoleBackend,
    context: DiscoveryContext,
    config: DeepDiscoveryConfig,
) -> tuple[HypothesisRecord, ...]:
    drafts = await backend.generate(context, count=config.initial_hypotheses)
    if len(drafts) != config.initial_hypotheses:
        raise RuntimeError("generation backend returned an unexpected hypothesis count")
    output: list[HypothesisRecord] = []
    for index, draft in enumerate(drafts):
        hypothesis_id = stable_id(
            "hyp",
            context.run_id,
            config.seed,
            0,
            index,
            draft.mechanism,
            draft.statement,
        )
        output.append(
            HypothesisRecord(
                hypothesis_id=hypothesis_id,
                run_id=context.run_id,
                round_index=0,
                mechanism=draft.mechanism,
                statement=draft.statement,
                testable_predictions=draft.testable_predictions,
                evidence_refs=draft.evidence_refs,
                constraints=draft.constraints,
                uncertainty=draft.uncertainty,
                operator="generate",
                created_at=stable_time(context.run_id, hypothesis_id),
            )
        )
    return tuple(output)
