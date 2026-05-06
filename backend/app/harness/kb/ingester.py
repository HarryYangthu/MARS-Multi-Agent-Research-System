"""Chunk + embed + write."""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

from app.harness.kb.embedder import embed
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
    z = s.zone(zone)
    out: list[KBRecord] = []
    for i, chunk in enumerate(_chunk(text, size=chunk_size, overlap=overlap)):
        rec_id = hashlib.sha256(f"{zone}:{i}:{chunk[:64]}".encode("utf-8")).hexdigest()[:16]
        rec = KBRecord(
            id=rec_id,
            zone=zone,
            text=chunk,
            metadata=dict(metadata or {}),
            embedding=embed(chunk),
        )
        z.add(rec)
        out.append(rec)
    return out
