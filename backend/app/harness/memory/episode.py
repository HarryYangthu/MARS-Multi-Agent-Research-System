"""Run-local episodic memory index."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.harness.kb.embedder import cosine, embed


def index_episode_event(
    *,
    run_root: Path,
    event: dict[str, Any],
) -> Path:
    path = run_root / "memory" / "episode_index.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _episode_text(event)
    row = {
        "schema": "episode_memory_index.v1",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "text": text,
        "embedding": embed(text).tolist(),
        "event": event,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return path


def search_episode_index(
    *,
    run_root: Path,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    path = run_root / "memory" / "episode_index.jsonl"
    if not path.exists() or not query.strip():
        return []
    q_vec = embed(query)
    hits: list[tuple[float, dict[str, Any]]] = []
    for row in _read_rows(path):
        emb = row.get("embedding")
        if not isinstance(emb, list):
            continue
        score = cosine(q_vec, np.array(emb, dtype=np.float32))
        hits.append((score, row))
    hits.sort(key=lambda item: item[0], reverse=True)
    return [{**row, "score": round(score, 6)} for score, row in hits[:top_k]]


def _episode_text(event: dict[str, Any]) -> str:
    fields = [
        str(event.get("target_agent", "") or ""),
        str(event.get("reason", "") or ""),
        str(event.get("expected_fix", "") or ""),
        str(event.get("failed_metrics", "") or ""),
        str(event.get("metric_snapshot", "") or ""),
    ]
    return " ".join(part for part in fields if part.strip()) or json.dumps(
        event, ensure_ascii=False, default=str
    )


def _read_rows(path: Path) -> list[dict[str, Any]]:
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
