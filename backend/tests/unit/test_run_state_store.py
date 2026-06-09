"""Durable run-state record + graph rehydration + new state machines."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import (
    IllegalTransition,
    NodeState,
    ReviewState,
    RunState,
    review_can_transition,
    run_assert_transition,
    run_can_transition,
    run_is_terminal,
)
from app.storage import run_state_store
from app.storage.run_state_store import RunStateRecord


def test_run_state_record_round_trip(tmp_path: Path) -> None:
    rec = RunStateRecord(
        run_id="r1",
        run_status=RunState.RUNNING.value,
        graph={"nodes": [], "edges": [], "entrypoints": []},
        idempotency_key="k1",
        attempts={"idea": 2},
        feedback_attempts=1,
    )
    run_state_store.write(tmp_path, rec)
    loaded = run_state_store.read(tmp_path)
    assert loaded is not None
    assert loaded.run_id == "r1"
    assert loaded.run_status == "running"
    assert loaded.attempts == {"idea": 2}
    assert loaded.feedback_attempts == 1
    assert loaded.updated_at  # stamped on write


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert run_state_store.read(tmp_path) is None


def test_graph_to_dict_from_dict_preserves_states() -> None:
    g = RunGraph()
    for k in ("idea", "experiment", "coding"):
        g.add_node(k)
    g.add_edge("idea", "experiment")
    g.add_edge("experiment", "coding")
    g.set_entrypoint("idea")
    g.transition("idea", NodeState.RUNNING)
    g.transition("idea", NodeState.DONE)

    rebuilt = RunGraph.from_dict(g.to_dict())
    assert rebuilt.state("idea") == NodeState.DONE
    assert rebuilt.state("experiment") == NodeState.PENDING
    assert rebuilt.successors("idea") == {"experiment"}
    assert rebuilt.entrypoints == ["idea"]
    # ready_nodes still works on the rehydrated graph
    assert rebuilt.ready_nodes() == ["experiment"]


def test_run_state_transitions() -> None:
    assert run_can_transition(RunState.CREATED, RunState.RUNNING)
    assert run_can_transition(RunState.RUNNING, RunState.COMPLETED)
    assert run_can_transition(RunState.FAILED, RunState.RUNNING)  # retry
    assert not run_can_transition(RunState.COMPLETED, RunState.RUNNING)
    assert run_is_terminal(RunState.COMPLETED)
    assert not run_is_terminal(RunState.WAITING_HUMAN)
    with pytest.raises(IllegalTransition):
        run_assert_transition(RunState.COMPLETED, RunState.RUNNING)


def test_review_state_transitions() -> None:
    assert review_can_transition(ReviewState.PENDING, ReviewState.APPROVED)
    assert review_can_transition(ReviewState.EDITED, ReviewState.APPROVED)
    assert not review_can_transition(ReviewState.APPROVED, ReviewState.REJECTED)


def test_node_failed_can_reset_to_pending() -> None:
    """Retry path resets a failed node so the scheduler reruns it."""
    from app.harness.runtime.state_machine import can_transition

    assert can_transition(NodeState.FAILED, NodeState.PENDING)
    assert can_transition(NodeState.FAILED, NodeState.RUNNING)
