"""Shared filesystem primitives for V3 discovery stores.

Immutable record files are the source of truth.  Mutable indexes and latest
pointers are written with ``os.replace`` so a reader sees either the old or
the new complete document.  All multi-file mutations for one run share a
single advisory lock, which makes read-modify-write operations safe across
threads and worker processes on the supported POSIX deployment targets.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DiscoveryStoreError(RuntimeError):
    """Base error for durable discovery storage."""


class DiscoveryConflictError(DiscoveryStoreError):
    """Raised when an immutable id or idempotency key is reused differently."""


class InvalidDiscoveryTransition(DiscoveryStoreError):
    """Raised when a durable state transition is not allowed."""


class DiscoveryCorruptionError(DiscoveryStoreError):
    """Raised when persisted records fail validation or hash-chain checks."""


@dataclass(frozen=True)
class DiscoveryPaths:
    """Validated paths for one run's lazily-created discovery directory."""

    run_root: Path
    run_id: str

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")

    @property
    def root(self) -> Path:
        return self.run_root / "discovery"

    @property
    def lock_path(self) -> Path:
        return self.root / ".store.lock"


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def stable_key(value: str) -> str:
    """Map an external identifier to a traversal-safe stable filename key."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = canonical_json(payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryCorruptionError(f"invalid discovery record: {path}") from exc
    if not isinstance(raw, dict):
        raise DiscoveryCorruptionError(f"discovery record is not an object: {path}")
    return {str(key): value for key, value in raw.items()}


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace ``path`` with one complete JSON object."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@contextmanager
def discovery_lock(paths: DiscoveryPaths) -> Iterator[None]:
    """Acquire the run-wide discovery lock across threads and processes."""

    paths.root.mkdir(parents=True, exist_ok=True)
    lock_key = str(paths.lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(lock_key, threading.RLock())
    with thread_lock:
        with paths.lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def model_payload(model: Any) -> dict[str, Any]:
    """Serialize a frozen Pydantic model without coupling to its concrete type."""

    raw = model.model_dump(mode="json")
    if not isinstance(raw, dict):  # pragma: no cover - Pydantic models always dump objects
        raise TypeError("discovery model must serialize to an object")
    return {str(key): value for key, value in raw.items()}


def equivalent_model_payload(
    left: Any,
    right: Any,
    *,
    ignored_fields: frozenset[str] = frozenset(),
) -> bool:
    left_payload = model_payload(left)
    right_payload = model_payload(right)
    for field in ignored_fields:
        left_payload.pop(field, None)
        right_payload.pop(field, None)
    return left_payload == right_payload


def iter_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - best effort on unusual filesystems
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - some filesystems do not support it
        pass
    finally:
        os.close(descriptor)
