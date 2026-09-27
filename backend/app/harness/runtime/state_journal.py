"""SQLite authority for a RunGraph snapshot and its durable transition outbox.

This is persistence only: RunGraph remains the state machine and the bridge
remains the scheduler. Delivery is at least once; consumers deduplicate event_id.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.harness.persistence import fsync_directory


class RunStateConflictError(ValueError):
    """The durable revision differs from the caller's observed revision."""


class RunStateIntegrityError(ValueError):
    """The authority is unavailable or does not match its declared identity."""


class StateJournal:
    def __init__(self, path: Path, *, run_id: str, journal_id: str) -> None:
        self.path = path.resolve()
        self.run_id = run_id
        self.journal_id = journal_id

    @classmethod
    def from_authority(cls, root: Path, *, run_id: str) -> StateJournal | None:
        """Open a declared authority for harness readers; None means legacy.

        A leftover SQLite projection or database also prevents legacy fallback
        if the authority marker has been lost.
        """
        marker_path = root / "run_state.authority.json"
        database_path = root / "run_state.sqlite3"
        if not marker_path.exists():
            if database_path.exists():
                raise RunStateIntegrityError("run state database has no authority marker")
            try:
                projection = json.loads((root / "run_state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                projection = None
            if isinstance(projection, dict) and projection.get("authority") == "sqlite":
                raise RunStateIntegrityError("declared run state authority is missing")
            return None
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RunStateIntegrityError("invalid run state authority marker") from exc
        if (not isinstance(marker, dict) or marker.get("schema") != "run_state_authority.v1"
                or marker.get("run_id") != run_id or not isinstance(marker.get("journal_id"), str)
                or not marker["journal_id"]):
            raise RunStateIntegrityError("invalid run state authority marker")
        return cls(database_path, run_id=run_id, journal_id=marker["journal_id"])

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # mode=rw is deliberate: a missing authority must never create an empty DB.
        try:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                row = connection.execute("SELECT run_id,journal_id,schema_version FROM identity WHERE id=1").fetchone()
                if row != (self.run_id, self.journal_id, 1):
                    raise RunStateIntegrityError("run state journal identity/schema mismatch")
                yield connection
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise RunStateIntegrityError("run state journal unavailable or corrupt") from exc

    def initialize(self, payload: dict[str, Any]) -> None:
        """Install a complete initial DB; caller holds the state/artifact lock."""
        if self.path.exists():
            raise RunStateIntegrityError("run state journal already exists")
        descriptor, name = tempfile.mkstemp(prefix=".run-state-", suffix=".sqlite3", dir=self.path.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            connection = sqlite3.connect(temporary)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript("""
                    CREATE TABLE identity (id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT NOT NULL,
                        journal_id TEXT NOT NULL, schema_version INTEGER NOT NULL);
                    CREATE TABLE run_state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                        payload TEXT NOT NULL);
                    CREATE TABLE state_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL,
                        payload TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0,1)));
                """)
                with connection:
                    connection.execute("INSERT INTO identity VALUES (1,?,?,1)", (self.run_id, self.journal_id))
                    connection.execute("INSERT INTO run_state VALUES (1,?,?)",
                                       (payload["revision"], self._encode(payload)))
            finally:
                connection.close()
            os.replace(temporary, self.path)
            fsync_directory(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)

    @staticmethod
    def _read(connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute("SELECT revision,payload FROM run_state WHERE id=1").fetchone()
        if row is None:
            raise RunStateIntegrityError("run state journal has no committed snapshot")
        try:
            payload = json.loads(row[1])
        except (ValueError, TypeError) as exc:
            raise RunStateIntegrityError("invalid run state snapshot JSON") from exc
        if not isinstance(payload, dict) or payload.get("revision") != row[0]:
            raise RunStateIntegrityError("run state revision/payload mismatch")
        return payload

    def read(self) -> dict[str, Any]:
        with self.connection() as connection:
            return self._read(connection)

    def commit(self, payload: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self.connection() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._read(connection)
            revision = previous["revision"]
            if revision != expected_revision:
                raise RunStateConflictError(f"run state revision is {revision}, expected {expected_revision}")
            committed = {**payload, "revision": revision + 1}
            old_states = {node["key"]: node["state"] for node in previous["graph"]["nodes"]}
            for node in committed["graph"]["nodes"]:
                before = old_states.get(node["key"])
                if before is None or before == node["state"]:
                    continue
                event_id = str(uuid4())
                event = {"event": "agent_state", "event_id": event_id, "revision": revision + 1,
                         "run_id": self.run_id, "agent": node["key"], "from_state": before,
                         "to_state": node["state"], "timestamp": committed["updated_at"]}
                if "termination" in node["metadata"]:
                    event["termination"] = node["metadata"]["termination"]
                connection.execute("INSERT INTO state_events(event_id,revision,payload) VALUES (?,?,?)",
                                   (event_id, revision + 1, self._encode(event)))
            connection.execute("UPDATE run_state SET revision=?,payload=? WHERE id=1",
                               (revision + 1, self._encode(committed)))
        return committed

    def pending_events(self) -> list[dict[str, Any]]:
        return self._events(pending_only=True)

    def all_events(self) -> list[dict[str, Any]]:
        """Return committed transitions, independently of projection/delivery."""
        return self._events(pending_only=False)

    def _events(self, *, pending_only: bool) -> list[dict[str, Any]]:
        with self.connection() as connection:
            # Read snapshot revision and event rows from one committed view.
            connection.execute("BEGIN")
            revision = self._read(connection)["revision"]
            query = "SELECT event_id,revision,payload FROM state_events"
            if pending_only:
                query += " WHERE published=0"
            rows = connection.execute(query + " ORDER BY sequence").fetchall()
        events: list[dict[str, Any]] = []
        for identifier, event_revision, encoded in rows:
            try:
                event = json.loads(encoded)
                if (not isinstance(event, dict) or event.get("run_id") != self.run_id
                        or event.get("event_id") != identifier or not isinstance(identifier, str) or not identifier
                        or type(event.get("revision")) is not int or event["revision"] != event_revision
                        or not 0 < event_revision <= revision or event.get("event") != "agent_state"
                        or not isinstance(event.get("agent"), str) or not event["agent"]):
                    raise ValueError("invalid event identity/revision")
                events.append(event)
            except (ValueError, TypeError) as exc:
                raise RunStateIntegrityError("invalid run state outbox event") from exc
        return events

    def mark_published(self, event_id: str) -> None:
        with self.connection() as connection, connection:
            cursor = connection.execute("UPDATE state_events SET published=1 WHERE event_id=?", (event_id,))
            if cursor.rowcount != 1:
                raise RunStateIntegrityError("unknown run state outbox event")
