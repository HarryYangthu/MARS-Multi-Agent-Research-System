"""4-zone KB built atop a tiny in-memory vector store.

The DESIGN spec says ChromaDB. For Dev E2E we want zero network/model
dependencies, so V0 ships a pure-Python store with the same surface (add /
query). When the host has a real ChromaDB available, ``stores.py`` could be
swapped via the ``provider`` field in ``configs/knowledge.yaml`` — left as
V1 work.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from app.harness.kb.embedder import cosine, embed
from app.settings import repo_root

ZONES: tuple[str, ...] = (
    "literature",
    "methodology",
    "code_assets",
    "run_archive",
)


@dataclass
class KBRecord:
    id: str
    zone: str
    text: str
    metadata: dict[str, Any]
    embedding: np.ndarray = field(repr=False)


class _ZoneStore:
    def __init__(self, zone: str, persist_path: Path) -> None:
        self.zone = zone
        self.path = persist_path
        self._records: list[KBRecord] = []
        self._lock = threading.Lock()
        self._load()

    def add(self, record: KBRecord) -> None:
        with self._lock:
            self._records.append(record)
            self._save()

    def query(self, *, query: str, top_k: int = 5) -> list[tuple[float, KBRecord]]:
        q_vec = embed(query)
        with self._lock:
            scored = [(cosine(q_vec, r.embedding), r) for r in self._records]
        scored.sort(key=lambda t: t[0], reverse=True)
        return scored[:top_k]

    def all(self) -> list[KBRecord]:
        with self._lock:
            return list(self._records)

    def delete_all(self) -> None:
        with self._lock:
            self._records.clear()
            self._save()

    # ------------------------------------------------------------ persist

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        for raw in data:
            self._records.append(
                KBRecord(
                    id=raw["id"],
                    zone=raw["zone"],
                    text=raw["text"],
                    metadata=raw.get("metadata", {}),
                    embedding=np.array(raw["embedding"], dtype=np.float32),
                )
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "id": r.id,
                "zone": r.zone,
                "text": r.text,
                "metadata": r.metadata,
                "embedding": r.embedding.tolist(),
            }
            for r in self._records
        ]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )


class KBStores:
    """Fan-out store. One JSON file per zone under
    ``knowledge/<zone>/_index.json``."""

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or (repo_root() / "knowledge")
        self._zones: dict[str, _ZoneStore] = {
            zone: _ZoneStore(zone, self.base / zone / "_index.json")
            for zone in ZONES
        }

    def zone(self, name: str) -> _ZoneStore:
        if name not in self._zones:
            raise KeyError(f"unknown zone '{name}'. valid: {ZONES}")
        return self._zones[name]

    def all_zones(self) -> Iterable[str]:
        return ZONES

    def query_across(
        self, *, zones: Sequence[str], query: str, top_k: int = 5
    ) -> list[tuple[float, KBRecord]]:
        out: list[tuple[float, KBRecord]] = []
        for z in zones:
            out.extend(self.zone(z).query(query=query, top_k=top_k))
        out.sort(key=lambda t: t[0], reverse=True)
        return out[:top_k]


_default_stores: KBStores | None = None


def get_stores() -> KBStores:
    global _default_stores
    if _default_stores is None:
        _default_stores = KBStores()
    return _default_stores


def reset_for_tests(base: Path | None = None) -> KBStores:
    global _default_stores
    _default_stores = KBStores(base=base)
    return _default_stores
