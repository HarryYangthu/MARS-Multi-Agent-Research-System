"""Run state access: SQLite authority, JSON compatibility projection.

Legacy JSON requires an explicit offline migration. Missing/corrupt declared
SQLite authority is an error, never a reason to infer executable state.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock, Timeout
from loguru import logger

from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.harness.runtime.state_journal import (
    RunStateConflictError as RunStateConflictError,
    RunStateIntegrityError as RunStateIntegrityError,
    StateJournal,
)
from app.harness.persistence import atomic_write_json, atomic_write_text, path_lock
from app.storage.run_store import RunHandle


@dataclass(frozen=True)
class RunStateSnapshot:
    run_id: str
    status: str
    graph: RunGraph
    request: dict[str, Any]
    updated_at: str
    failed_nodes: tuple[str, ...] = ()
    failure_summary: str | None = None
    termination: dict[str, Any] | None = None
    revision: int = 0
    migration_required: bool = False


class RunStateMigrationRequired(ValueError):
    """A legacy snapshot needs explicit offline validation/migration."""


class RunStateStore:
    def __init__(self, run: RunHandle) -> None:
        self.run = run
        self.path = run.root / "run_state.json"
        self.database_path = run.root / "run_state.sqlite3"
        self.authority_path = run.root / "run_state.authority.json"
        self.lock_path = run.root / ".state-artifacts.lock"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RunStateIntegrityError("run state JSON unavailable or corrupt") from exc
        if not isinstance(raw, dict):
            raise RunStateIntegrityError("run state must be an object")
        return raw

    def _journal(self) -> StateJournal | None:
        if not self.authority_path.exists():
            if self.database_path.exists():
                raise RunStateIntegrityError("run state database has no authority marker")
            return None
        marker = self._read_json(self.authority_path)
        if (marker.get("schema") != "run_state_authority.v1" or marker.get("run_id") != self.run.run_id
                or not isinstance(marker.get("journal_id"), str) or not marker["journal_id"]):
            raise RunStateIntegrityError("invalid run state authority marker")
        return StateJournal(self.database_path, run_id=self.run.run_id, journal_id=marker["journal_id"])

    def _validate(self, raw: dict[str, Any]) -> None:
        if (raw.get("schema") != "run_state.v1" or raw.get("run_id") != self.run.run_id
                or raw.get("project") != self.run.project or raw.get("task") != self.run.task
                or raw.get("entrypoint") != self.run.entrypoint):
            raise RunStateIntegrityError("run state identity/schema mismatch")
        if (type(raw.get("revision", 0)) is not int or raw.get("revision", 0) < 0
                or not isinstance(raw.get("request"), dict)
                or not isinstance(raw.get("status"), str) or not raw["status"]
                or not isinstance(raw.get("updated_at"), str)):
            raise RunStateIntegrityError("invalid run state fields")
        if raw.get("termination") is not None and not isinstance(raw["termination"], dict):
            raise RunStateIntegrityError("invalid run termination record")
        for key in ("task", "project", "entrypoint"):
            if key in raw["request"] and raw["request"][key] != raw[key]:
                raise RunStateIntegrityError("run request identity mismatch")
        request = raw["request"]
        if ("review_mode" in request and request["review_mode"] not in ("manual", "commander")
                or "review_generation" in request and (type(request["review_generation"]) is not int or request["review_generation"] < 0)):
            raise RunStateIntegrityError("invalid reviewer preference")
        graph = raw.get("graph")
        if not isinstance(graph, dict) or any(not isinstance(graph.get(key), list) for key in ("nodes", "edges", "entrypoints")):
            raise RunStateIntegrityError("invalid run graph structure")
        keys: set[str] = set()
        if not graph["nodes"]:
            raise RunStateIntegrityError("empty run graph cannot be recovered")
        for node in graph["nodes"]:
            if (not isinstance(node, dict) or not isinstance(node.get("key"), str) or not node["key"]
                    or node["key"] in keys or not isinstance(node.get("kind"), str)
                    or not isinstance(node.get("metadata"), dict) or not isinstance(node.get("state"), str)
                    or node["state"] not in {state.value for state in NodeState}):
                raise RunStateIntegrityError("invalid run graph node")
            keys.add(node["key"])
        for edge in graph["edges"]:
            if (not isinstance(edge, dict) or not isinstance(edge.get("src"), str) or not isinstance(edge.get("dst"), str)
                    or edge["src"] not in keys or edge["dst"] not in keys):
                raise RunStateIntegrityError("invalid run graph edge")
        if any(not isinstance(key, str) or key not in keys for key in graph["entrypoints"]):
            raise RunStateIntegrityError("invalid run graph entrypoint")
        try:
            RunGraph.from_dict(graph).topological_order()
        except ValueError as exc:
            raise RunStateIntegrityError("invalid run graph topology") from exc

    def _snapshot(self, raw: dict[str, Any], *, legacy: bool = False) -> RunStateSnapshot:
        self._validate(raw)
        return RunStateSnapshot(
            run_id=self.run.run_id, status=raw["status"], graph=RunGraph.from_dict(raw["graph"]),
            request=raw["request"], updated_at=raw["updated_at"],
            failed_nodes=tuple(key for key, state in RunGraph.from_dict(raw["graph"]).all_states().items()
                               if state == NodeState.FAILED),
            failure_summary=raw.get("failure_summary"),
            termination=raw.get("termination"), revision=raw.get("revision", 0), migration_required=legacy,
        )

    def _projection(self, raw: dict[str, Any]) -> None:
        # SQLite already committed. A projection failure must not report a failed
        # transaction or provoke a duplicate state transition.
        try:
            atomic_write_json(self.path, {**raw, "authority": "sqlite"})
        except OSError as exc:
            logger.warning("Run state committed; JSON projection unavailable: run={} type={}",
                           self.run.run_id, type(exc).__name__)

    def _initialize(self, payload: dict[str, Any], *, migration: dict[str, Any] | None = None) -> StateJournal:
        journal_id = str(uuid4())
        marker = {"schema": "run_state_authority.v1", "run_id": self.run.run_id,
                  "journal_id": journal_id, "migration": migration}
        # Marker first is fail-closed even if the process dies during DB creation.
        atomic_write_json(self.authority_path, marker)
        journal = StateJournal(self.database_path, run_id=self.run.run_id, journal_id=journal_id)
        journal.initialize(payload)
        self._projection(payload)
        return journal

    def write(self, *, graph: RunGraph, request: dict[str, Any], status: str,
              termination: dict[str, Any] | None = None, expected_revision: int | None = None) -> int:
        failed_nodes = sorted(key for key, state in graph.all_states().items() if state == NodeState.FAILED)
        payload: dict[str, Any] = {
            "schema": "run_state.v1", "run_id": self.run.run_id, "project": self.run.project,
            "task": self.run.task, "entrypoint": self.run.entrypoint, "status": status,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(), "request": request,
            "graph": graph.to_dict(), "failed_nodes": failed_nodes,
            "failure_summary": f"{len(failed_nodes)} run node(s) failed" if failed_nodes else None,
        }
        if termination is not None:
            payload["termination"] = dict(termination)
        self._validate(payload)
        with path_lock(self.lock_path):
            from app.storage.artifact_store import ArtifactStore
            journal = self._journal()
            if journal is None:
                if self.path.exists():
                    raise RunStateMigrationRequired("legacy run state requires explicit migration")
                if expected_revision not in (None, 0):
                    raise RunStateIntegrityError("expected run state authority is missing")
                ArtifactStore(self.run).recover_approvals()
                payload["revision"] = 1
                self._initialize(payload)
                return 1
            previous = journal.read()
            self._validate(previous)
            ArtifactStore(self.run).recover_approvals()
            committed = journal.commit(payload, expected_revision=previous["revision"] if expected_revision is None else expected_revision)
            self._projection(committed)
            return int(committed["revision"])

    def load(self) -> RunStateSnapshot | None:
        # Historical artifact-only runs are display-only; inspecting them must
        # not create a lock file or silently manufacture state.
        if not any(path.exists() for path in (self.path, self.database_path, self.authority_path)):
            return None
        with path_lock(self.lock_path):
            journal = self._journal()
            if journal is None:
                if not self.path.exists():
                    return None
                raw = self._read_json(self.path)
                if raw.get("authority") == "sqlite":
                    raise RunStateIntegrityError("declared run state authority is missing")
                return self._snapshot(raw, legacy=True)
            from app.storage.artifact_store import ArtifactStore
            raw = journal.read()
            snapshot = self._snapshot(raw)
            ArtifactStore(self.run).recover_approvals()
            return snapshot

    def pending_events(self) -> list[dict[str, Any]]:
        journal = self._journal()
        return [] if journal is None else journal.pending_events()

    def mark_published(self, event_id: str) -> None:
        journal = self._journal()
        if journal is None:
            raise RunStateIntegrityError("run state event authority is missing")
        journal.mark_published(event_id)

    def migrate_legacy(self) -> RunStateSnapshot:
        """Explicit offline import. State/artifacts validated, budget untouched."""
        self._validate_migration_paths()
        try:
            with ExitStack() as stack:
                stack.enter_context(FileLock(self.run.root / "runtime.driver.lock", timeout=0))
                # Same lock order as accounting: hold budget lock before probing
                # request leases, so no request can be reserved during migration.
                stack.enter_context(path_lock(self.run.root / "resources/.model_budget.v1.json.lock"))
                for lease in sorted((self.run.root / "resources/requests").glob("*.lock")):
                    stack.enter_context(FileLock(lease, timeout=0))
                stack.enter_context(path_lock(self.lock_path))
                self._validate_migration_paths()
                if self._journal() is not None:
                    snapshot = self.load()
                    if snapshot is None:
                        raise RunStateIntegrityError("run state authority has no snapshot")
                    return snapshot
                if not self.path.exists():
                    raise RunStateMigrationRequired("legacy run state is missing")
                source_bytes = self.path.read_bytes()
                source = source_bytes.decode("utf-8")
                raw = self._read_json(self.path)
                if raw.get("authority") == "sqlite":
                    raise RunStateIntegrityError("declared run state authority is missing")
                snapshot = self._snapshot(raw, legacy=True)
                self._validate_migration_artifacts(snapshot)
                backup = self.run.root / "run_state.legacy.json"
                if backup.exists() and backup.read_bytes() != source_bytes:
                    raise RunStateIntegrityError("legacy backup already contains a different snapshot")
                atomic_write_text(backup, source)
                raw["revision"] = snapshot.revision
                self._initialize(raw, migration={"source": backup.name,
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "migrated_at": datetime.now(tz=timezone.utc).isoformat()})
                return self._snapshot(raw)
        except Timeout as exc:
            raise RunStateMigrationRequired("migration blocked by an active run driver or model request lease") from exc

    def _validate_migration_paths(self) -> None:
        """Reject linked inputs/targets before locks or approval recovery touch them."""
        from app.storage.run_store import RUN_SUBDIRS
        if self.run.root.is_symlink():
            raise RunStateMigrationRequired("legacy migration run root must not contain symbolic links")
        root = self.run.root.resolve()

        def owned(path: Path) -> None:
            current = root
            for component in path.relative_to(root).parts:
                current = current / component
                if current.is_symlink():
                    raise RunStateMigrationRequired("legacy migration paths must not contain symbolic links")
            if not path.resolve().is_relative_to(root):
                raise RunStateMigrationRequired("legacy migration path escapes the run root")

        for relative in ("run_state.json", "run_state.authority.json", "run_state.sqlite3", "run_state.legacy.json",
                         "runtime.driver.lock", ".state-artifacts.lock", "resources/model_budget.v1.json",
                         "resources/.model_budget.v1.json.lock", "resources/requests"):
            owned(root / relative)
        for lease in (root / "resources/requests").glob("*.lock"):
            owned(lease)
        # Match the scope that ArtifactStore.recover_approvals can inspect when
        # the migrated state is first loaded, including legacy custom stems.
        for stage in RUN_SUBDIRS:
            directory = root / stage
            owned(directory)
            approvals = directory / ".approvals"
            owned(approvals)
            for path in directory.glob("*.md"):
                owned(path)
            if approvals.exists():
                for records in approvals.iterdir():
                    owned(records)
                    if records.is_dir():
                        for path in records.glob("*.json"):
                            owned(path)

    def _validate_migration_artifacts(self, snapshot: RunStateSnapshot) -> None:
        from app.storage.artifact_store import ArtifactStore, SCHEMA_TO_AGENT
        from app.harness.schema.validator import get_schema, validate_document
        artifacts = ArtifactStore(self.run)
        # latest() below recovers only the checked schema/stem. A global repair
        # would inspect unrelated approval directories before ownership checks.
        for key, node in snapshot.graph.nodes.items():
            if node.state not in {NodeState.WAITING_REVIEW, NodeState.APPROVED, NodeState.DONE}:
                continue
            # The persisted product stage is metadata when available; legacy
            # dynamic attempt names use the existing documented suffix convention.
            stage = str(node.metadata.get("stage") or key.split("_attempt_", 1)[0])
            matches = [(schema, stem) for schema, (agent, stem) in SCHEMA_TO_AGENT.items() if agent == stage]
            if not matches:
                raise RunStateMigrationRequired(f"cannot validate artifact reference for legacy node {key}")
            valid = False
            for schema, stem in matches:
                ref = artifacts.latest(agent_dir=stage, stem=stem)
                if ref is None or (node.state in {NodeState.APPROVED, NodeState.DONE} and ref.version != "approved"):
                    continue
                result = validate_document(ref.path.read_text(encoding="utf-8"), expected_schema=schema)
                # Schema validation binds the Agent identity to its stage, but
                # project/run IDs are free strings and need this run's identity.
                if not result.valid or result.metadata.get("project") != self.run.project:
                    continue
                if ("run_id" in get_schema(schema).get("properties", {})
                        and result.metadata.get("run_id") != self.run.run_id):
                    continue
                valid = True
            if not valid:
                raise RunStateMigrationRequired(f"legacy node {key} requires a valid {'approved ' if node.state != NodeState.WAITING_REVIEW else ''}artifact for this run/project")
