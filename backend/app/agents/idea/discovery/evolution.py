"""Parent selection and hypothesis evolution."""
from __future__ import annotations

from collections.abc import Sequence

from app.agents.idea.discovery.backend import DiscoveryRoleBackend
from app.agents.idea.discovery.models import (
    DiscoveryContext,
    EvolutionRequest,
    stable_id,
    stable_time,
)
from app.harness.discovery import HypothesisRecord


EVOLUTION_OPERATORS: tuple[str, ...] = (
    "strengthen",
    "combine",
    "simplify",
    "diverge",
)


async def evolve_hypotheses(
    *,
    backend: DiscoveryRoleBackend,
    context: DiscoveryContext,
    hypotheses: Sequence[HypothesisRecord],
    round_index: int,
    child_count: int,
) -> tuple[HypothesisRecord, ...]:
    legal = sorted(
        (item for item in hypotheses if not item.blocked),
        key=lambda item: (-item.elo, item.cluster_id, item.hypothesis_id),
    )
    if not legal:
        return ()
    representatives = _diverse_representatives(legal)
    requests: list[EvolutionRequest] = []
    for index in range(child_count):
        operator = EVOLUTION_OPERATORS[index % len(EVOLUTION_OPERATORS)]
        primary = representatives[index % len(representatives)]
        parents: tuple[HypothesisRecord, ...] = (primary,)
        if operator == "combine" and len(representatives) > 1:
            secondary = representatives[(index + 1) % len(representatives)]
            if secondary.hypothesis_id != primary.hypothesis_id:
                parents = (primary, secondary)
        requests.append(
            EvolutionRequest(
                round_index=round_index,
                operator=operator,
                parents=parents,
            )
        )

    drafts = await backend.evolve(context, requests)
    if len(drafts) != len(requests):
        raise RuntimeError("evolution backend returned an unexpected child count")
    output: list[HypothesisRecord] = []
    for index, (request, draft) in enumerate(zip(requests, drafts, strict=True)):
        parent_ids = tuple(parent.hypothesis_id for parent in request.parents)
        hypothesis_id = stable_id(
            "hyp",
            context.run_id,
            round_index,
            index,
            request.operator,
            parent_ids,
            draft.statement,
        )
        output.append(
            HypothesisRecord(
                hypothesis_id=hypothesis_id,
                run_id=context.run_id,
                round_index=round_index,
                parent_ids=parent_ids,
                mechanism=draft.mechanism,
                statement=draft.statement,
                testable_predictions=draft.testable_predictions,
                evidence_refs=draft.evidence_refs,
                constraints=draft.constraints,
                uncertainty=draft.uncertainty,
                operator=request.operator,
                created_at=stable_time(context.run_id, hypothesis_id),
            )
        )
    return tuple(output)


def _diverse_representatives(
    hypotheses: Sequence[HypothesisRecord],
) -> tuple[HypothesisRecord, ...]:
    by_cluster: dict[str, HypothesisRecord] = {}
    for item in hypotheses:
        cluster = item.cluster_id or item.hypothesis_id
        by_cluster.setdefault(cluster, item)
    representatives = sorted(
        by_cluster.values(),
        key=lambda item: (-item.elo, item.hypothesis_id),
    )
    return tuple(representatives or hypotheses[:1])
