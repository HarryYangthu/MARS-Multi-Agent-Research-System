"""Convenience wrapper around `ingester` for the sedimentation hooks."""
from __future__ import annotations

from typing import Any

from app.harness.kb.ingester import ingest_memory
from app.harness.kb.models import EvalStatus, MemoryType


def write_to_zone(
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
) -> int:
    records = ingest_memory(
        zone=zone,
        text=text,
        metadata=metadata,
        memory_type=memory_type,
        source_path=source_path,
        run_id=run_id,
        agent=agent,
        schema=schema,
        is_mock=is_mock,
        confidence=confidence,
        eval_status=eval_status,
        salience=salience,
        ttl_days=ttl_days,
        approved=approved,
    )
    return len(records)
