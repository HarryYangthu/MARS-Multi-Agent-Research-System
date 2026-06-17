"""Durable run graph state for V1 recovery."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.harness.runtime.run_graph import RunGraph
from app.storage.run_store import RunHandle


@dataclass(frozen=True)
class RunStateSnapshot:
    run_id: str
    status: str
    graph: RunGraph
    request: dict[str, Any]
    updated_at: str


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
    ) -> None:
        payload = {
            "schema": "run_state.v1",
            "run_id": self.run.run_id,
            "project": self.run.project,
            "task": self.run.task,
            "entrypoint": self.run.entrypoint,
            "status": status,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "request": request,
            "graph": graph.to_dict(),
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load(self) -> RunStateSnapshot | None:
        if not self.path.exists():
            return None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        graph_raw = raw.get("graph", {})
        graph = RunGraph.from_dict(graph_raw if isinstance(graph_raw, dict) else {})
        request_raw = raw.get("request", {})
        request = request_raw if isinstance(request_raw, dict) else {}
        return RunStateSnapshot(
            run_id=str(raw.get("run_id", self.run.run_id)),
            status=str(raw.get("status", "unknown")),
            graph=graph,
            request={str(k): v for k, v in request.items()},
            updated_at=str(raw.get("updated_at", "")),
        )
