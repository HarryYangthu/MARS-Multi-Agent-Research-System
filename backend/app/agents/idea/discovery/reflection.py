"""Reflection and hard-blocker handling."""
from __future__ import annotations

from collections.abc import Sequence

from app.agents.idea.discovery.backend import DiscoveryRoleBackend
from app.agents.idea.discovery.models import (
    DiscoveryContext,
    stable_id,
    stable_time,
)
from app.harness.discovery import HypothesisRecord, ReflectionRecord


async def reflect_hypotheses(
    *,
    backend: DiscoveryRoleBackend,
    context: DiscoveryContext,
    hypotheses: Sequence[HypothesisRecord],
) -> tuple[tuple[HypothesisRecord, ...], tuple[ReflectionRecord, ...]]:
    drafts = await backend.reflect(context, hypotheses)
    updated: list[HypothesisRecord] = []
    reflections: list[ReflectionRecord] = []
    for item in hypotheses:
        draft = drafts.get(item.hypothesis_id)
        if draft is None:
            raise RuntimeError(f"reflection missing hypothesis {item.hypothesis_id}")
        blockers = tuple(dict.fromkeys((*draft.blockers, *_hard_blockers(item))))
        blocked = item.blocked or bool(blockers)
        updated.append(item.model_copy(update={"blocked": blocked}))
        reflection_id = stable_id(
            "reflection", context.run_id, item.hypothesis_id, blockers
        )
        reflections.append(
            ReflectionRecord(
                reflection_id=reflection_id,
                hypothesis_id=item.hypothesis_id,
                correctness=draft.correctness,
                novelty=draft.novelty,
                falsifiability=draft.falsifiability,
                assumptions=draft.assumptions,
                failure_modes=draft.failure_modes,
                evidence_refs=draft.evidence_refs,
                blockers=blockers,
                created_at=stable_time(context.run_id, reflection_id),
            )
        )
    return tuple(updated), tuple(reflections)


def add_reflection_blocker(
    reflections: Sequence[ReflectionRecord],
    *,
    hypothesis_id: str,
    blocker: str,
) -> tuple[ReflectionRecord, ...]:
    output: list[ReflectionRecord] = []
    for item in reflections:
        if item.hypothesis_id != hypothesis_id or blocker in item.blockers:
            output.append(item)
            continue
        output.append(
            item.model_copy(update={"blockers": (*item.blockers, blocker)})
        )
    return tuple(output)


def _hard_blockers(item: HypothesisRecord) -> tuple[str, ...]:
    blockers: list[str] = []
    if item.blocked:
        blockers.append("preblocked")
    if not item.testable_predictions:
        blockers.append("missing_testable_predictions")
    if not item.evidence_refs:
        blockers.append("missing_evidence_refs")
    if len(item.statement.strip()) < 12:
        blockers.append("statement_too_short")
    lowered = item.statement.lower()
    if any(token in lowered for token in ("不可验证", "无法测试", "guaranteed")):
        blockers.append("not_falsifiable")
    return tuple(blockers)
