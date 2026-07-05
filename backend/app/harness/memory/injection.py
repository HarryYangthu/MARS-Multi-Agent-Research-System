"""Context V2 memory injection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.harness.context.manifest_v2 import ContextSegment
from app.harness.context.raw_store import write_raw_context
from app.harness.kb.selector import MemoryHit, default_include_mock, select_memory
from app.harness.kb.stores import KBStores
from app.harness.memory.selector_policy import policy_for_agent
from app.harness.memory.usage import append_memory_usage


@dataclass(frozen=True)
class MemoryInjectionResult:
    segments: list[ContextSegment]
    hits: list[MemoryHit]
    usage_path: Path | None


def build_memory_segments(
    *,
    agent: str,
    node_key: str,
    project: str,
    task: str,
    purpose: str,
    run_root: Path | None,
    stores: KBStores | None = None,
) -> MemoryInjectionResult:
    if not task.strip():
        return MemoryInjectionResult(segments=[], hits=[], usage_path=None)
    policy = policy_for_agent(agent, purpose=purpose)
    selected: list[MemoryHit] = []
    for memory_type in policy.memory_types:
        selected.extend(
            select_memory(
                query=task,
                zones=policy.zones,
                top_k=policy.top_k,
                memory_type=memory_type,
                project=project,
                include_mock=default_include_mock(),
                include_superseded=False,
                approved_only=True,
                stores=stores,
            )
        )
    deduped = _dedupe_hits(selected, min_score=policy.min_score, limit=policy.top_k)
    segments = [
        _segment_for_hit(hit, index=index, run_root=run_root, agent=agent)
        for index, hit in enumerate(deduped, start=1)
    ]
    usage_path = append_memory_usage(
        run_root=run_root,
        agent=agent,
        node_key=node_key,
        purpose=purpose,
        hits=deduped,
        segment_ids=[segment.id for segment in segments],
    )
    return MemoryInjectionResult(segments=segments, hits=deduped, usage_path=usage_path)


def _dedupe_hits(
    hits: list[MemoryHit],
    *,
    min_score: float,
    limit: int,
) -> list[MemoryHit]:
    by_id: dict[str, MemoryHit] = {}
    for hit in hits:
        if hit.score < min_score:
            continue
        existing = by_id.get(hit.record.id)
        if existing is None or hit.score > existing.score:
            by_id[hit.record.id] = hit
    ranked = sorted(by_id.values(), key=lambda item: item.score, reverse=True)
    return ranked[:limit]


def _segment_for_hit(
    hit: MemoryHit,
    *,
    index: int,
    run_root: Path | None,
    agent: str,
) -> ContextSegment:
    raw_ref = None
    if run_root is not None:
        raw_ref = write_raw_context(
            run_root=run_root,
            agent=agent,
            label=f"memory_{index}_{hit.memory.record_id}",
            payload={
                "record_id": hit.memory.record_id,
                "zone": hit.memory.zone,
                "memory_type": hit.memory.memory_type,
                "source_path": hit.memory.source_path,
                "score": hit.score,
                "similarity": hit.similarity,
                "text": hit.memory.text,
                "metadata": hit.record.metadata,
            },
        )
    text = "\n".join(
        [
            f"- record_id: `{hit.memory.record_id}`",
            f"- zone: `{hit.memory.zone}`",
            f"- memory_type: `{hit.memory.memory_type}`",
            f"- score: {hit.score:.3f}",
            f"- source: `{hit.memory.source_path or hit.record.id}`",
            "",
            hit.injected_text,
        ]
    )
    return ContextSegment(
        id=f"memory:{index}:{hit.memory.record_id}",
        kind="memory",
        title=f"{hit.memory.zone}/{hit.memory.memory_type}/{index}",
        source_ref=hit.memory.source_path or hit.record.id,
        text=text,
        priority="medium",
        selection_reason="selected by MemorySelector policy for this agent and task",
        compression="summary",
        raw_ref=raw_ref,
    )
