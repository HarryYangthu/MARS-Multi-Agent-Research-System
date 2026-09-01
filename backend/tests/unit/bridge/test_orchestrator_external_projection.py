from __future__ import annotations

import json
from pathlib import Path

from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunStore


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_model_discovery_recovers_as_read_only_completed_projection(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "runs")
    run = store.create(
        task="external-discovery",
        project="synthetic_regression",
        entrypoint="model_discovery",
        user_request="verify read-only recovery",
    )
    _write_json(
        run.root / "discovery" / "checkpoints" / "latest.json",
        {
            "schema_id": "discovery_checkpoint.v1",
            "status": "completed",
            "state": {"lifecycle": "completed"},
        },
    )

    session = Orchestrator(
        run_store=store,
        bus=InProcessEventBus(),
    ).session(run.run_id)

    assert session.read_only is True
    assert session.graph.all_states() == {"model_discovery": NodeState.DONE}
    node = session.graph.to_dict()["nodes"][0]
    assert node["kind"] == "external_service"
    assert node["metadata"]["lifecycle"] == "completed"


def test_iteration_projection_uses_parent_checkpoint_status(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    parent = store.create(
        task="external-parent",
        project="synthetic_regression",
        entrypoint="model_discovery",
        user_request="parent",
    )
    child = store.create(
        task="external-child",
        project="synthetic_regression",
        entrypoint="discovery_iteration",
        user_request="child",
    )
    _write_json(
        child.root / "discovery" / "iteration_link.json",
        {
            "schema_id": "discovery_iteration_link.v1",
            "parent_run_id": parent.run_id,
            "child_run_id": child.run_id,
            "status": "running",
        },
    )
    _write_json(
        parent.root / "discovery" / "checkpoints" / "latest.json",
        {
            "schema_id": "discovery_checkpoint.v1",
            "status": "completed",
            "state": {
                "lifecycle": "completed",
                "iteration_nodes": [
                    {"child_run_id": child.run_id, "status": "completed"}
                ],
            },
        },
    )

    session = Orchestrator(
        run_store=store,
        bus=InProcessEventBus(),
    ).session(child.run_id)

    assert session.read_only is True
    assert session.graph.all_states() == {"discovery_iteration": NodeState.DONE}
    node = session.graph.to_dict()["nodes"][0]
    assert node["metadata"]["lifecycle"] == "completed"
