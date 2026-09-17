"""Chunk + embed + write."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock

from app.harness.kb.embedder import configured_embed, embedding_metadata, embedding_spec
from app.harness.kb.models import EvalStatus, MemoryRecord, MemoryType, infer_memory_type
from app.harness.kb.provenance import verified_memory
from app.harness.kb.resolver import resolve_for_write
from app.harness.kb.stores import KBRecord, KBStores, get_stores


def _chunk(text: str, *, size: int = 1500, overlap: int = 200) -> Iterable[str]:
    if not text:
        return
    step = max(1, size - overlap)
    for i in range(0, len(text), step):
        chunk = text[i : i + size]
        if not chunk.strip():
            continue
        yield chunk


def ingest(
    *,
    zone: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    chunk_size: int = 1500,
    overlap: int = 200,
    stores: KBStores | None = None,
) -> list[KBRecord]:
    s = stores or get_stores()
    spec = embedding_spec()
    z = s.zone(zone)
    out: list[KBRecord] = []
    for i, chunk in enumerate(_chunk(text, size=chunk_size, overlap=overlap)):
        rec_id = hashlib.sha256(f"{zone}:{(metadata or {}).get('project', '')}:{i}:{chunk}".encode("utf-8")).hexdigest()[:16]
        rec = KBRecord(
            id=rec_id,
            zone=zone,
            text=chunk,
            metadata={**dict(metadata or {}), **embedding_metadata(spec)},
            embedding=configured_embed(chunk, spec=spec),
        )
        z.upsert(rec)
        out.append(rec)
    return out


def ingest_memory(
    *,
    zone: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    memory_type: MemoryType | None = None,
    source_path: str = "",
    run_id: str = "",
    agent: str = "",
    schema: str = "",
    is_mock: bool = False,
    confidence: float = 0.8,
    eval_status: EvalStatus | None = None,
    salience: float = 0.5,
    ttl_days: int | None = 180,
    approved: bool = False,
    chunk_size: int = 1500,
    overlap: int = 200,
    stores: KBStores | None = None,
) -> list[KBRecord]:
    s = stores or get_stores()
    spec = embedding_spec()
    z = s.zone(zone)
    base_meta = dict(metadata or {})
    inferred_type = memory_type or infer_memory_type(
        zone=zone, kind=str(base_meta.get("kind", ""))
    )
    chunks = list(_chunk(text, size=chunk_size, overlap=overlap))
    if not chunks:
        return []
    effective_source = source_path or str(base_meta.get("source_path", ""))
    prepared: list[KBRecord] = []
    for i, chunk in enumerate(chunks):
        record = MemoryRecord.create(
            zone=zone,
            text=chunk,
            memory_type=inferred_type,
            source_path=effective_source,
            run_id=run_id or str(base_meta.get("run_id", "")),
            agent=agent or str(base_meta.get("agent", "")),
            schema=schema or str(base_meta.get("schema", "")),
            is_mock=is_mock or bool(base_meta.get("is_mock", False)),
            confidence=confidence,
            eval_status=eval_status,
            salience=salience,
            ttl_days=ttl_days,
            approved=approved,
            extra_id_seed=f"project:{base_meta.get('project', '')}:chunk:{i}",
        )
        meta = {**base_meta, **record.to_metadata(), "chunk_index": i, **embedding_metadata(spec)}
        rec = KBRecord(
            id=record.record_id,
            zone=zone,
            text=chunk,
            metadata=meta,
            embedding=configured_embed(chunk, spec=spec),
        )
        prepared.append(rec)
    # All real embeddings must succeed before a replacement can touch old
    # records. Serialize source replacements across store instances/processes.
    s.base.mkdir(parents=True, exist_ok=True)
    out: list[KBRecord] = []
    with FileLock(str(s.base / "_ingestion.lock")):
        prior: list[KBRecord] = []
        if effective_source:
            prior = [old for old in z.all() if old.metadata.get("source_path") == effective_source
                     and old.metadata.get("project", "") == base_meta.get("project", "")]
            trusted_input = approved and all(verified_memory(item.text, item.metadata, base=s.base) for item in prepared)
            if not trusted_input and any(old.metadata.get("approved") is True
                                         and verified_memory(old.text, old.metadata, base=s.base) for old in prior):
                raise ValueError("cannot replace approved memory without approved receipt-backed input")
        for rec in prepared:
            resolved = rec if effective_source else resolve_for_write(record=rec, stores=s, replace_source=False)
            if resolved is None:
                continue
            z.upsert(resolved)
            _index_semantics(base=s.base, record=resolved)
            out.append(resolved)
        # Publish new chunks before deleting superseded chunks; an interrupted
        # replacement retains the previous source instead of erasing it.
        keep = {record.id for record in out}
        for old in prior:
            if old.id not in keep:
                z.delete(old.id)
    return out


def _index_semantics(*, base: Path, record: KBRecord) -> None:
    try:
        from app.harness.memory.semantic import index_semantic_record

        index_semantic_record(base=base, record=record)
    except Exception:
        return
