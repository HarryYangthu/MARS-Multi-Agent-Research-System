"""Backend contracts for governed KB storage."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

import numpy as np


@dataclass
class KBRecord:
    id: str
    zone: str
    text: str
    metadata: dict[str, Any]
    embedding: np.ndarray = field(repr=False)


class ZoneBackend(Protocol):
    zone: str

    def add(self, record: KBRecord) -> None: ...

    def upsert(self, record: KBRecord) -> None: ...

    def delete_by_source(self, source_path: str) -> int: ...

    def delete(self, record_id: str) -> bool: ...

    def update_metadata(self, record_id: str, patch: dict[str, Any]) -> bool: ...

    def query(
        self,
        *,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = True,
        exclude_mock: bool = True,
    ) -> list[tuple[float, KBRecord]]: ...

    def all(
        self,
        *,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = False,
        exclude_mock: bool = False,
    ) -> list[KBRecord]: ...

    def delete_all(self) -> None: ...


class MemoryBackend(Protocol):
    name: str
    base: Path

    def zone(self, name: str) -> ZoneBackend: ...

    def all_zones(self, *, include_quarantine: bool = False) -> Iterable[str]: ...

    def query_across(
        self,
        *,
        zones: Sequence[str],
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        exclude_superseded: bool = True,
        exclude_mock: bool = True,
    ) -> list[tuple[float, KBRecord]]: ...

    def upsert(self, record: KBRecord) -> None: ...

    def delete_by_source(self, source_path: str) -> int: ...

    def delete(self, zone: str, record_id: str) -> bool: ...

    def update_metadata(self, zone: str, record_id: str, patch: dict[str, Any]) -> bool: ...


class UnsupportedMemoryBackend(RuntimeError):
    """Raised when config selects a backend with no active adapter."""
