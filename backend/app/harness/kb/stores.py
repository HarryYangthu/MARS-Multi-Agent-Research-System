"""4-zone KB built atop a tiny in-memory vector store.

The DESIGN spec says ChromaDB. For Dev E2E we want zero network/model
dependencies, so V0 ships a pure-Python store with the same surface (add /
query). When the host has a real ChromaDB available, ``stores.py`` could be
swapped via the ``provider`` field in ``configs/knowledge.yaml`` — left as
V2 work.
"""
from __future__ import annotations

import json
import threading
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Sequence, cast

import numpy as np

from app.harness.kb.backends import (
    KBRecord as KBRecord,
    MemoryBackend,
    UnsupportedMemoryBackend,
    ZoneBackend,
)
from app.harness.kb.config import backend_store
from app.harness.kb.embedder import cosine, embed
from app.harness.kb.models import memory_from_kb_record
from app.settings import repo_root

MAIN_ZONES: tuple[str, ...] = (
    "literature",
    "methodology",
    "code_assets",
    "run_archive",
    "failure_memory",
)
QUARANTINE_ZONE = "quarantine"
ZONES: tuple[str, ...] = MAIN_ZONES + (QUARANTINE_ZONE,)


class FileZoneBackend:
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

    def upsert(self, record: KBRecord) -> None:
        with self._lock:
            self._records = [r for r in self._records if r.id != record.id]
            self._records.append(record)
            self._save()

    def delete_by_source(self, source_path: str) -> int:
        if not source_path:
            return 0
        with self._lock:
            before = len(self._records)
            self._records = [
                r for r in self._records
                if str(r.metadata.get("source_path", "")) != source_path
            ]
            deleted = before - len(self._records)
            if deleted:
                self._save()
            return deleted

    def delete(self, record_id: str) -> bool:
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records if r.id != record_id]
            deleted = len(self._records) != before
            if deleted:
                self._save()
            return deleted

    def update_metadata(self, record_id: str, patch: dict[str, Any]) -> bool:
        with self._lock:
            changed = False
            for record in self._records:
                if record.id != record_id:
                    continue
                record.metadata.update(patch)
                changed = True
                break
            if changed:
                self._save()
            return changed

    def query(
        self,
        *,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = True,
        exclude_mock: bool = True,
    ) -> list[tuple[float, KBRecord]]:
        q_vec = embed(query)
        with self._lock:
            candidates = [
                r for r in self._records
                if _record_matches(
                    r,
                    filters=filters,
                    exclude_superseded=exclude_superseded,
                    exclude_mock=exclude_mock,
                )
            ]
            scored = [(cosine(q_vec, r.embedding), r) for r in candidates]
        scored.sort(key=lambda t: t[0], reverse=True)
        return scored[:top_k]

    def all(
        self,
        *,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = False,
        exclude_mock: bool = False,
    ) -> list[KBRecord]:
        with self._lock:
            return [
                r for r in self._records
                if _record_matches(
                    r,
                    filters=filters,
                    exclude_superseded=exclude_superseded,
                    exclude_mock=exclude_mock,
                )
            ]

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


class FileMemoryBackend:
    """JSON file backend. One file per zone under ``knowledge/<zone>``."""

    name = "file"

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or (repo_root() / "knowledge")
        self._zones: dict[str, FileZoneBackend] = {
            zone: FileZoneBackend(zone, self.base / zone / "_index.json")
            for zone in ZONES
        }

    def zone(self, name: str) -> ZoneBackend:
        if name not in self._zones:
            raise KeyError(f"unknown zone '{name}'. valid: {ZONES}")
        return self._zones[name]

    def all_zones(self, *, include_quarantine: bool = False) -> Iterable[str]:
        return ZONES if include_quarantine else MAIN_ZONES

    def query_across(
        self,
        *,
        zones: Sequence[str],
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = True,
        exclude_mock: bool = True,
    ) -> list[tuple[float, KBRecord]]:
        out: list[tuple[float, KBRecord]] = []
        for z in zones:
            out.extend(
                self.zone(z).query(
                    query=query,
                    top_k=top_k,
                    filters=filters,
                    exclude_superseded=exclude_superseded,
                    exclude_mock=exclude_mock,
                )
            )
        out.sort(key=lambda t: t[0], reverse=True)
        return out[:top_k]

    def upsert(self, record: KBRecord) -> None:
        self.zone(record.zone).upsert(record)

    def delete_by_source(self, source_path: str) -> int:
        return sum(self.zone(zone).delete_by_source(source_path) for zone in ZONES)

    def delete(self, zone: str, record_id: str) -> bool:
        return self.zone(zone).delete(record_id)

    def update_metadata(self, zone: str, record_id: str, patch: dict[str, Any]) -> bool:
        return self.zone(zone).update_metadata(record_id, patch)


class ChromaZoneBackend:
    def __init__(self, zone: str, collection: Any) -> None:
        self.zone = zone
        self._collection = collection

    def add(self, record: KBRecord) -> None:
        self._collection.add(
            ids=[record.id],
            documents=[record.text],
            embeddings=[_embedding_to_list(record.embedding)],
            metadatas=[_chroma_metadata(record)],
        )

    def upsert(self, record: KBRecord) -> None:
        self._collection.upsert(
            ids=[record.id],
            documents=[record.text],
            embeddings=[_embedding_to_list(record.embedding)],
            metadatas=[_chroma_metadata(record)],
        )

    def delete_by_source(self, source_path: str) -> int:
        if not source_path:
            return 0
        ids = [
            record.id
            for record in self.all(exclude_mock=False, exclude_superseded=False)
            if str(record.metadata.get("source_path", "")) == source_path
        ]
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def delete(self, record_id: str) -> bool:
        existing = self._get_record(record_id)
        if existing is None:
            return False
        self._collection.delete(ids=[record_id])
        return True

    def update_metadata(self, record_id: str, patch: dict[str, Any]) -> bool:
        record = self._get_record(record_id)
        if record is None:
            return False
        metadata = dict(record.metadata)
        metadata.update(patch)
        self.upsert(
            KBRecord(
                id=record.id,
                zone=record.zone,
                text=record.text,
                metadata=metadata,
                embedding=record.embedding,
            )
        )
        return True

    def query(
        self,
        *,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = True,
        exclude_mock: bool = True,
    ) -> list[tuple[float, KBRecord]]:
        q_vec = embed(query)
        candidates = self.all(
            filters=filters,
            exclude_superseded=exclude_superseded,
            exclude_mock=exclude_mock,
        )
        scored = [(cosine(q_vec, record.embedding), record) for record in candidates]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:top_k]

    def all(
        self,
        *,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = False,
        exclude_mock: bool = False,
    ) -> list[KBRecord]:
        result = self._collection.get(include=["documents", "metadatas", "embeddings"])
        records = _records_from_chroma_result(zone=self.zone, result=result)
        return [
            record
            for record in records
            if _record_matches(
                record,
                filters=filters,
                exclude_superseded=exclude_superseded,
                exclude_mock=exclude_mock,
            )
        ]

    def delete_all(self) -> None:
        result = self._collection.get(include=[])
        ids = [str(item) for item in result.get("ids", [])]
        if ids:
            self._collection.delete(ids=ids)

    def _get_record(self, record_id: str) -> KBRecord | None:
        result = self._collection.get(
            ids=[record_id],
            include=["documents", "metadatas", "embeddings"],
        )
        records = _records_from_chroma_result(zone=self.zone, result=result)
        return records[0] if records else None


class ChromaMemoryBackend:
    """ChromaDB-backed storage using MARS-managed deterministic embeddings."""

    name = "chroma"

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or (repo_root() / "knowledge")
        self.path = self.base / ".chromadb"
        self.path.mkdir(parents=True, exist_ok=True)
        chromadb = _load_chromadb()
        client_cls = getattr(chromadb, "PersistentClient")
        self._client = client_cls(path=str(self.path))
        self._zones: dict[str, ChromaZoneBackend] = {
            zone: ChromaZoneBackend(zone, self._collection(zone))
            for zone in ZONES
        }

    def zone(self, name: str) -> ZoneBackend:
        if name not in self._zones:
            raise KeyError(f"unknown zone '{name}'. valid: {ZONES}")
        return self._zones[name]

    def all_zones(self, *, include_quarantine: bool = False) -> Iterable[str]:
        return ZONES if include_quarantine else MAIN_ZONES

    def query_across(
        self,
        *,
        zones: Sequence[str],
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = True,
        exclude_mock: bool = True,
    ) -> list[tuple[float, KBRecord]]:
        out: list[tuple[float, KBRecord]] = []
        for zone in zones:
            out.extend(
                self.zone(zone).query(
                    query=query,
                    top_k=top_k,
                    filters=filters,
                    exclude_superseded=exclude_superseded,
                    exclude_mock=exclude_mock,
                )
            )
        out.sort(key=lambda item: item[0], reverse=True)
        return out[:top_k]

    def upsert(self, record: KBRecord) -> None:
        self.zone(record.zone).upsert(record)

    def delete_by_source(self, source_path: str) -> int:
        return sum(self.zone(zone).delete_by_source(source_path) for zone in ZONES)

    def delete(self, zone: str, record_id: str) -> bool:
        return self.zone(zone).delete(record_id)

    def update_metadata(self, zone: str, record_id: str, patch: dict[str, Any]) -> bool:
        return self.zone(zone).update_metadata(record_id, patch)

    def _collection(self, zone: str) -> Any:
        return self._client.get_or_create_collection(
            name=f"mars_memory_{zone}",
            metadata={"hnsw:space": "cosine"},
        )


class KBStores:
    """Stable KB facade used by selector, sedimentation, tools, and APIs."""

    def __init__(
        self,
        base: Path | None = None,
        *,
        store: str = "file",
        backend: MemoryBackend | None = None,
    ) -> None:
        self.base = base or (repo_root() / "knowledge")
        self._backend = backend or _build_backend(store=store, base=self.base)

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def zone(self, name: str) -> ZoneBackend:
        return self._backend.zone(name)

    def all_zones(self, *, include_quarantine: bool = False) -> Iterable[str]:
        return self._backend.all_zones(include_quarantine=include_quarantine)

    def query_across(
        self,
        *,
        zones: Sequence[str],
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = True,
        exclude_mock: bool = True,
    ) -> list[tuple[float, KBRecord]]:
        return self._backend.query_across(
            zones=zones,
            query=query,
            top_k=top_k,
            filters=filters,
            exclude_superseded=exclude_superseded,
            exclude_mock=exclude_mock,
        )

    def upsert(self, record: KBRecord) -> None:
        self._backend.upsert(record)

    def delete_by_source(self, source_path: str) -> int:
        return self._backend.delete_by_source(source_path)

    def delete(self, zone: str, record_id: str) -> bool:
        return self._backend.delete(zone, record_id)

    def update_metadata(self, zone: str, record_id: str, patch: dict[str, Any]) -> bool:
        return self._backend.update_metadata(zone, record_id, patch)


def _build_backend(*, store: str, base: Path) -> MemoryBackend:
    normalized = store.strip().lower()
    if normalized in {"file", "json"}:
        return FileMemoryBackend(base)
    if normalized == "chroma":
        return ChromaMemoryBackend(base)
    if normalized == "sqlite_vss":
        raise UnsupportedMemoryBackend(
            f"memory backend '{normalized}' is configured but no adapter is enabled yet"
        )
    raise UnsupportedMemoryBackend(f"unknown memory backend '{store}'")


def _load_chromadb() -> ModuleType:
    try:
        return import_module("chromadb")
    except ImportError as exc:
        raise UnsupportedMemoryBackend(
            "memory backend 'chroma' requires the chromadb package"
        ) from exc


def _embedding_to_list(value: np.ndarray) -> list[float]:
    raw = value.astype(np.float32).tolist()
    return [float(item) for item in raw]


def _chroma_metadata(record: KBRecord) -> dict[str, str | int | float | bool]:
    memory = memory_from_kb_record(
        record_id=record.id,
        zone=record.zone,
        text=record.text,
        metadata=record.metadata,
    )
    metadata: dict[str, str | int | float | bool] = {
        "_mars_metadata_json": json.dumps(
            record.metadata,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
        "zone": record.zone,
        "source_path": memory.source_path,
        "project": str(record.metadata.get("project", "")),
        "agent": memory.agent,
        "run_id": memory.run_id,
        "memory_type": memory.memory_type,
        "schema": memory.schema,
        "is_mock": memory.is_mock,
        "approved": memory.approved,
        "superseded_by": memory.superseded_by or "",
    }
    return metadata


def _records_from_chroma_result(*, zone: str, result: dict[str, Any]) -> list[KBRecord]:
    ids = [str(item) for item in result.get("ids", [])]
    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])
    embeddings = result.get("embeddings", [])
    records: list[KBRecord] = []
    for index, record_id in enumerate(ids):
        text = _sequence_item(documents, index)
        if not isinstance(text, str):
            text = ""
        metadata = _metadata_from_chroma(_sequence_item(metadatas, index))
        records.append(
            KBRecord(
                id=record_id,
                zone=zone,
                text=text,
                metadata=metadata,
                embedding=_embedding_from_chroma(_sequence_item(embeddings, index), text),
            )
        )
    return records


def _metadata_from_chroma(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    sidecar = raw.get("_mars_metadata_json")
    if isinstance(sidecar, str):
        try:
            parsed = json.loads(sidecar)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return dict(parsed)
    return {str(key): value for key, value in raw.items() if key != "_mars_metadata_json"}


def _embedding_from_chroma(raw: object, text: str) -> np.ndarray:
    try:
        arr = np.array(raw, dtype=np.float32)
    except (TypeError, ValueError):
        return embed(text)
    if arr.ndim != 1 or arr.size == 0:
        return embed(text)
    return cast(np.ndarray, arr)


def _sequence_item(value: object, index: int) -> object:
    if isinstance(value, np.ndarray):
        if index < value.shape[0]:
            return value[index]
        return None
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return value[index] if index < len(value) else None
    return None


def _record_matches(
    record: KBRecord,
    *,
    filters: dict[str, Any] | None,
    exclude_superseded: bool,
    exclude_mock: bool,
) -> bool:
    memory = memory_from_kb_record(
        record_id=record.id,
        zone=record.zone,
        text=record.text,
        metadata=record.metadata,
    )
    if exclude_superseded and memory.superseded_by:
        return False
    if exclude_mock and memory.is_mock:
        return False
    if not filters:
        return True
    for key, value in filters.items():
        if value is None:
            continue
        if key == "memory_type" and memory.memory_type != str(value):
            return False
        if key == "project" and str(record.metadata.get("project", "")) != str(value):
            return False
        if key == "agent" and memory.agent != str(value):
            return False
        if key == "run_id" and memory.run_id != str(value):
            return False
        if key == "approved" and memory.approved != bool(value):
            return False
        if key == "source_path" and memory.source_path != str(value):
            return False
    return True


_default_stores: KBStores | None = None


def get_stores() -> KBStores:
    global _default_stores
    if _default_stores is None:
        _default_stores = KBStores(store=backend_store())
    return _default_stores


def reset_for_tests(base: Path | None = None, *, store: str = "file") -> KBStores:
    global _default_stores
    _default_stores = KBStores(base=base, store=store)
    return _default_stores


__all__ = [
    "KBRecord",
    "KBStores",
    "MAIN_ZONES",
    "QUARANTINE_ZONE",
    "ZONES",
    "get_stores",
    "reset_for_tests",
]
