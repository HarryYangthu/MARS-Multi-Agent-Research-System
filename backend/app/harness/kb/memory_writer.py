"""Convenience wrapper around `ingester` for the sedimentation hooks."""
from __future__ import annotations

from typing import Any

from app.harness.kb.ingester import ingest


def write_to_zone(
    *, zone: str, text: str, metadata: dict[str, Any] | None = None
) -> int:
    records = ingest(zone=zone, text=text, metadata=metadata)
    return len(records)
