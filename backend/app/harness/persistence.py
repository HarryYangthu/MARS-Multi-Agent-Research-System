"""Durable filesystem writes and portable, reentrant process locks.

The same lock object is shared by local callers. FileLock supplies a Windows
or POSIX OS lock, while thread-local ownership prevents unrelated threads
from treating one another as nested lock holders.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock

_LOCKS: dict[str, FileLock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def path_lock(path: Path) -> Iterator[None]:
    """Serialize a logical transaction, including across worker processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, FileLock(key))
    with lock:
        yield


def fsync_directory(directory: Path) -> None:
    """Persist a replaced directory entry where the platform supports it."""
    if os.name == "nt":
        return  # Windows does not expose POSIX directory fsync via os.open.
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, text: str) -> None:
    """Publish only complete UTF-8 contents; callers lock multi-step writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(dict(payload), indent=2, ensure_ascii=False,
                                     sort_keys=True, allow_nan=False) + "\n")


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    """Persist one complete event under a shared per-stream lock."""
    encoded = json.dumps(dict(payload), ensure_ascii=False, allow_nan=False) + "\n"
    with path_lock(path.with_name(f".{path.name}.lock")):
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
