"""Audit log for memories injected into Context V2."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.kb.selector import MemoryHit


@dataclass(frozen=True)
class MemoryUsageRecord:
    record_id: str
    zone: str
    memory_type: str
    score: float
    similarity: float
    source_path: str
    run_id: str
    agent: str
    segment_id: str


def append_memory_usage(
    *,
    run_root: Path | None,
    agent: str,
    node_key: str,
    purpose: str,
    hits: list[MemoryHit],
    segment_ids: list[str],
) -> Path | None:
    if run_root is None or not hits:
        return None
    created = datetime.now(tz=timezone.utc).isoformat()
    path = run_root / "context" / "agents" / _safe_id(agent) / "memory" / "memory_usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for hit, segment_id in zip(hits, segment_ids, strict=False):
        rows.append(
            {
                "schema": "memory_usage.v1",
                "created_at": created,
                "agent": agent,
                "node_key": node_key,
                "purpose": purpose,
                "record_id": hit.memory.record_id,
                "zone": hit.memory.zone,
                "memory_type": hit.memory.memory_type,
                "score": round(hit.score, 6),
                "similarity": round(hit.similarity, 6),
                "source_path": hit.memory.source_path,
                "memory_run_id": hit.memory.run_id,
                "memory_agent": hit.memory.agent,
                "segment_id": segment_id,
                "approved": hit.memory.approved,
                "is_mock": hit.memory.is_mock,
                "superseded_by": hit.memory.superseded_by,
            }
        )
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return path


def read_memory_usage(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def _safe_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value.strip())
    return cleaned.strip("._") or "agent"
