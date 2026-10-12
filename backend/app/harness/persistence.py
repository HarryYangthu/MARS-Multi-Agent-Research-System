"""Durable filesystem writes and portable, reentrant process locks.

Local RLock ownership serializes threads, while one shared FileLock context
supplies Windows/POSIX cross-process exclusion and nested acquisition.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

_LOCKS: dict[str, tuple[threading.RLock, FileLock]] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def path_lock(path: Path, *, timeout: float = -1) -> Iterator[None]:
    """Serialize a logical transaction, including across worker processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(path.resolve()))
    if os.name == "nt":
        # realpath may retain the extended prefix when the file is locked.
        # Both spellings refer to the same OS file and must share one RLock.
        if key.startswith("\\\\?\\unc\\"):
            key = "\\\\" + key[8:]
        elif key.startswith("\\\\?\\"):
            key = key[4:]
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            # Thread ownership is enforced by RLock, while one shared OS-lock
            # context handles nested acquisitions and cross-process exclusion.
            # Never let different worker contexts reacquire our own OS handle.
            _LOCKS[key] = (threading.RLock(), FileLock(key, thread_local=False))
        local, lock = _LOCKS[key]
    started = time.monotonic()
    if not local.acquire(timeout=timeout):
        raise Timeout(key)
    try:
        remaining = max(0.0, timeout - (time.monotonic() - started)) if timeout >= 0 else timeout
        with lock.acquire(timeout=remaining):
            yield
    finally:
        local.release()


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
