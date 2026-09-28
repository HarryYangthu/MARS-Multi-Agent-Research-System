"""Durable creation identities; never a second authority for run state or budgets."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Literal

from filelock import FileLock, Timeout

from app.harness.persistence import fsync_directory, path_lock

REQUEST_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$"


class CreationRequestConflict(ValueError):
    """An immutable request ID already belongs to another declaration."""


class CreationCatalogIntegrityError(ValueError):
    """Creation evidence is unavailable; automatic creation is unsafe."""


def validate_request_id(request_id: str) -> str:
    if re.fullmatch(REQUEST_ID_PATTERN, request_id) is None:
        raise ValueError("Invalid creation request ID")
    return request_id


def binding_sha256(name: str, task_sha256: str) -> str:
    return hashlib.sha256(json.dumps({"name": name, "task_sha256": task_sha256},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CreationIntent:
    request_id: str
    name: str
    task_sha256: str
    binding_sha256: str
    run_id: str | None
    phase: Literal["pending", "created", "unknown", "rejected"]


class ResearchCreationStore:
    """Explicit POST initialization; lookup never creates directories or files."""

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root.resolve()
        self.root = self.runs_root / ".creation_requests"
        self.path = self.root / "catalog.sqlite3"
        self.identity = hashlib.sha256(str(self.runs_root).encode()).hexdigest()

    def _paths(self) -> None:
        for path in (self.root, self.path, self.root / "leases", self.root / ".initialize.lock",
                     self.path.with_name("catalog.sqlite3-journal"), self.path.with_name("catalog.sqlite3-wal"),
                     self.path.with_name("catalog.sqlite3-shm")):
            if path.is_symlink():
                raise CreationCatalogIntegrityError("Creation catalog paths cannot be symbolic links")
        if self.path.exists() and (not self.path.is_file() or self.path.stat().st_nlink != 1):
            raise CreationCatalogIntegrityError("Creation catalog must be an owned ordinary file")
        initialization_lock = self.root / ".initialize.lock"
        if initialization_lock.exists() and (not initialization_lock.is_file() or initialization_lock.stat().st_nlink != 1):
            raise CreationCatalogIntegrityError("Creation initialization lease must be an owned ordinary file")

    def initialize(self) -> None:
        self._paths()
        self.root.mkdir(exist_ok=True)
        with path_lock(self.root / ".initialize.lock"):
            if not self.path.exists():
                # Existing leases are evidence of previous accepted requests.
                # Losing their catalog is not permission to allocate replacements.
                if (self.root / "leases").exists():
                    raise CreationCatalogIntegrityError("Creation catalog is missing")
                connection = sqlite3.connect(self.path)
                try:
                    connection.execute("PRAGMA synchronous=FULL")
                    with connection:
                        connection.execute("CREATE TABLE identity (id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, root_sha256 TEXT NOT NULL)")
                        connection.execute("INSERT INTO identity VALUES (1,1,?)", (self.identity,))
                        connection.execute("""CREATE TABLE creation_intents (
                            request_id TEXT PRIMARY KEY, name TEXT NOT NULL, task_sha256 TEXT NOT NULL,
                            binding_sha256 TEXT NOT NULL, run_id TEXT UNIQUE,
                            phase TEXT NOT NULL CHECK(phase IN ('pending','created','unknown','rejected')))""")
                finally:
                    connection.close()
                fsync_directory(self.root)
            with self.connection():
                pass
            (self.root / "leases").mkdir(exist_ok=True)

    @contextmanager
    def connection(self, *, writable: bool = False) -> Iterator[sqlite3.Connection]:
        self._paths()
        try:
            connection = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if writable else "?mode=ro"),
                                         uri=True, timeout=10)
            try:
                if writable:
                    connection.execute("PRAGMA synchronous=FULL")
                else:
                    connection.execute("PRAGMA query_only=ON")
                if connection.execute("SELECT schema_version,root_sha256 FROM identity WHERE id=1").fetchone() != (1, self.identity):
                    raise CreationCatalogIntegrityError("Creation catalog identity mismatch")
                yield connection
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise CreationCatalogIntegrityError("Creation catalog unavailable or corrupt") from exc

    @staticmethod
    def _decode(row: tuple[object, ...]) -> CreationIntent:
        request_id, name, task_sha256, binding, run_id, phase = row
        if (not isinstance(request_id, str) or re.fullmatch(REQUEST_ID_PATTERN, request_id) is None
                or not isinstance(name, str) or not name.strip()
                or not isinstance(task_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", task_sha256) is None
                or binding != binding_sha256(name, task_sha256)
                or run_id is not None and (not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9_.-]+", run_id) is None or run_id in {".", ".."})
                or phase not in {"pending", "created", "unknown", "rejected"}
                or phase == "rejected" and run_id is not None):
            raise CreationCatalogIntegrityError("Invalid creation intent identity")
        assert isinstance(binding, str) and (run_id is None or isinstance(run_id, str))
        assert phase in ("pending", "created", "unknown", "rejected")
        return CreationIntent(request_id, name, task_sha256, binding, run_id, phase)

    def get(self, request_id: str) -> CreationIntent | None:
        validate_request_id(request_id)
        self._paths()
        if not self.root.exists():
            return None
        with self.connection() as connection:
            row = connection.execute("SELECT request_id,name,task_sha256,binding_sha256,run_id,phase FROM creation_intents WHERE request_id=?", (request_id,)).fetchone()
        return None if row is None else self._decode(row)

    def reserve(self, request_id: str, *, name: str, task_sha256: str,
                rejected: bool = False) -> tuple[CreationIntent, bool]:
        validate_request_id(request_id)
        binding = binding_sha256(name, task_sha256)
        with self.connection(writable=True) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT request_id,name,task_sha256,binding_sha256,run_id,phase FROM creation_intents WHERE request_id=?", (request_id,)).fetchone()
            if row is not None:
                intent = self._decode(row)
                if intent.binding_sha256 != binding:
                    raise CreationRequestConflict("Creation request identity already bound")
                return intent, False
            connection.execute("INSERT INTO creation_intents VALUES (?,?,?,?,NULL,?)", (request_id, name, task_sha256, binding, "rejected" if rejected else "pending"))
        return CreationIntent(request_id, name, task_sha256, binding, None, "rejected" if rejected else "pending"), True

    def bind_run(self, request_id: str, run_id: str) -> None:
        with self.connection(writable=True) as connection, connection:
            cursor = connection.execute("UPDATE creation_intents SET run_id=? WHERE request_id=? AND run_id IS NULL AND phase='pending'", (run_id, request_id))
            if cursor.rowcount != 1:
                raise CreationCatalogIntegrityError("Creation allocation identity is already bound")

    def finish(self, request_id: str, *, created: bool) -> None:
        with self.connection(writable=True) as connection, connection:
            cursor = connection.execute("UPDATE creation_intents SET phase=? WHERE request_id=? AND phase='pending'", ("created" if created else "unknown", request_id))
            if cursor.rowcount != 1:
                raise CreationCatalogIntegrityError("Creation intent is not pending")

    def _lease_path(self, request_id: str) -> Path:
        validate_request_id(request_id)
        path = self.root / "leases" / (hashlib.sha256(request_id.encode()).hexdigest() + ".lock")
        self._paths()
        if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise CreationCatalogIntegrityError("Creation lease must be an owned ordinary file")
        return path

    @contextmanager
    def lease(self, request_id: str) -> Iterator[bool]:
        lock = FileLock(self._lease_path(request_id), timeout=0)
        try:
            lock.acquire()
        except Timeout:
            yield False
            return
        try:
            yield True
        finally:
            lock.release()

    def owner_active(self, request_id: str) -> bool:
        """Probe an already existing OS lease without creating or truncating it."""
        path = self._lease_path(request_id)
        if not path.exists():
            return False
        descriptor = os.open(path, (os.O_RDWR if os.name == "nt" else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0))
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError:
                    return True
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        finally:
            os.close(descriptor)
