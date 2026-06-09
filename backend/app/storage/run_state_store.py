"""Durable run-state persistence: ``runs/<id>/run_state.json``.

This is what makes a run survive a backend restart. The orchestrator writes a
snapshot on every state transition; recovery reads it back to rehydrate the
in-memory ``RunGraph`` and ``RunState``.

Kept deliberately dumb — it serialises a plain dict (the graph is passed in as
``RunGraph.to_dict()``), so this storage module needs no dependency on the
harness runtime.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FILENAME = "run_state.json"


@dataclass
class RunStateRecord:
    run_id: str
    run_status: str
    graph: dict[str, Any]
    idempotency_key: str | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    feedback_attempts: int = 0
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunStateRecord":
        return cls(
            run_id=str(data["run_id"]),
            run_status=str(data["run_status"]),
            graph=dict(data.get("graph", {})),
            idempotency_key=data.get("idempotency_key"),
            attempts={str(k): int(v) for k, v in (data.get("attempts") or {}).items()},
            feedback_attempts=int(data.get("feedback_attempts", 0)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def path_for(run_root: Path) -> Path:
    return run_root / _FILENAME


def write(run_root: Path, record: RunStateRecord) -> None:
    """Atomically persist the run-state snapshot (tmp file + os.replace)."""
    record.updated_at = datetime.now(tz=timezone.utc).isoformat()
    if not record.created_at:
        record.created_at = record.updated_at
    target = path_for(run_root)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, target)


def read(run_root: Path) -> RunStateRecord | None:
    target = path_for(run_root)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "run_id" not in data:
        return None
    return RunStateRecord.from_dict(data)
