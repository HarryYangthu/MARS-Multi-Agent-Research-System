"""Conflict resolution for MemoryRecord writes."""
from __future__ import annotations

from app.harness.kb.embedder import cosine, embed
from app.harness.kb.models import MemoryRecord, memory_from_kb_record
from app.harness.kb.stores import KBRecord, KBStores, get_stores


def resolve_for_write(
    *,
    record: KBRecord,
    stores: KBStores | None = None,
    semantic_threshold: float = 0.94,
    replace_source: bool = True,
) -> KBRecord | None:
    """Return a record to write, or None when it is an exact duplicate.

    Exact duplicate = same zone + content_hash. Same source_path gets upserted by
    deleting older source copies. High-similarity same-kind records are marked as
    superseded by the new record so default recall returns only the latest view.
    """
    s = stores or get_stores()
    memory = memory_from_kb_record(
        record_id=record.id,
        zone=record.zone,
        text=record.text,
        metadata=record.metadata,
    )
    zone = s.zone(record.zone)
    existing = zone.all(exclude_mock=False, exclude_superseded=False)
    for old in existing:
        old_memory = memory_from_kb_record(
            record_id=old.id,
            zone=old.zone,
            text=old.text,
            metadata=old.metadata,
        )
        if old_memory.content_hash == memory.content_hash:
            return None
    if replace_source and memory.source_path:
        zone.delete_by_source(memory.source_path)
        existing = zone.all(exclude_mock=False, exclude_superseded=False)
    new_vec = embed(record.text)
    supersedes: list[str] = []
    for old in existing:
        old_memory = memory_from_kb_record(
            record_id=old.id,
            zone=old.zone,
            text=old.text,
            metadata=old.metadata,
        )
        if old_memory.superseded_by or old_memory.memory_type != memory.memory_type:
            continue
        if _entity_key(old_memory) != _entity_key(memory):
            continue
        if cosine(new_vec, old.embedding) >= semantic_threshold:
            supersedes.append(old.id)
            zone.update_metadata(old.id, {"superseded_by": record.id})
    if supersedes:
        metadata = dict(record.metadata)
        metadata["supersedes"] = sorted(set(supersedes))
        record = KBRecord(
            id=record.id,
            zone=record.zone,
            text=record.text,
            metadata=metadata,
            embedding=record.embedding,
        )
    return record


def _entity_key(memory: MemoryRecord) -> str:
    if memory.source_path:
        return memory.source_path
    if memory.run_id and memory.agent:
        return f"{memory.run_id}:{memory.agent}:{memory.schema}:{memory.memory_type}"
    return f"{memory.agent}:{memory.schema}:{memory.memory_type}"
