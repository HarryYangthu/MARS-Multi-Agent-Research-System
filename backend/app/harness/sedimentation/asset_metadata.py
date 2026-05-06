"""Common metadata stamped onto every sedimented record."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def make(
    *,
    project: str,
    agent: str,
    run_id: str,
    schema: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "project": project,
        "agent": agent,
        "run_id": run_id,
        "schema": schema,
        "sedimented_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if extra:
        out.update(extra)
    return out
