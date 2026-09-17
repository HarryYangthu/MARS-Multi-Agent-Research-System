"""Durable run graph state for V2 recovery."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.harness.runtime.run_graph import RunGraph
from app.harness.persistence import atomic_write_json, path_lock
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


class RunStateConflictError(ValueError):
    """A caller tried to replace a state revision it has not observed."""


class RunStateStore:
    def __init__(self, run: RunHandle) -> None:
        self.run = run
        self.path = run.root / "run_state.json"

    def write(
        self,
        *,
        graph: RunGraph,
        request: dict[str, Any],
        status: str,
        termination: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> int:
        failed_nodes = tuple(
            sorted(
                key
                for key, state in graph.all_states().items()
                if state.value == "failed"
            )
        )
        payload: dict[str, Any] = {
            "schema": "run_state.v1",
            "run_id": self.run.run_id,
            "project": self.run.project,
            "task": self.run.task,
            "entrypoint": self.run.entrypoint,
            "status": status,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "request": request,
            "graph": graph.to_dict(),
            "failed_nodes": list(failed_nodes),
            "failure_summary": (
                f"{len(failed_nodes)} run node(s) failed"
                if failed_nodes
                else None
            ),
        }
        if termination is not None:
            payload["termination"] = dict(termination)
        with path_lock(self.run.root / ".state-artifacts.lock"):
            previous = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            revision = int(previous.get("revision", 0))
            if expected_revision is not None and revision != expected_revision:
                raise RunStateConflictError(f"run state revision is {revision}, expected {expected_revision}")
            # Approval receipts commit before the graph can publish advancement.
            # Repair an interrupted pointer write before advancing or recovering.
            from app.storage.artifact_store import ArtifactStore

            ArtifactStore(self.run).recover_approvals()
            payload["revision"] = revision + 1
            atomic_write_json(self.path, payload)
            return revision + 1

    def load(self) -> RunStateSnapshot | None:
        if not self.path.exists():
            return None
        with path_lock(self.run.root / ".state-artifacts.lock"):
            from app.storage.artifact_store import ArtifactStore

            ArtifactStore(self.run).recover_approvals()
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("run_id") != self.run.run_id:
            raise ValueError("run state identity mismatch")
        graph_raw = raw.get("graph", {})
        graph = RunGraph.from_dict(graph_raw if isinstance(graph_raw, dict) else {})
        request_raw = raw.get("request", {})
        request = request_raw if isinstance(request_raw, dict) else {}
        failed_nodes_raw = raw.get("failed_nodes", [])
        failed_nodes = (
            tuple(str(item) for item in failed_nodes_raw)
            if isinstance(failed_nodes_raw, list)
            else ()
        )
        failure_summary_raw = raw.get("failure_summary")
        return RunStateSnapshot(
            run_id=str(raw.get("run_id", self.run.run_id)),
            status=str(raw.get("status", "unknown")),
            graph=graph,
            request={str(k): v for k, v in request.items()},
            updated_at=str(raw.get("updated_at", "")),
            failed_nodes=failed_nodes,
            failure_summary=(
                str(failure_summary_raw)
                if isinstance(failure_summary_raw, str)
                else None
            ),
            termination=dict(raw["termination"]) if isinstance(raw.get("termination"), dict) else None,
            revision=int(raw.get("revision", 0)),
        )
