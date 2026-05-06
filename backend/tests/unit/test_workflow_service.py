from __future__ import annotations

import pytest

from app.bridge.workflow_service import LINEAR_STAGES, build_pipeline, build_standalone
from app.harness.runtime.state_machine import NodeState


def test_pipeline_full_chain() -> None:
    g = build_pipeline("pipeline")
    assert list(g.nodes) == list(LINEAR_STAGES)
    assert g.entrypoints == ["idea"]
    assert all(g.state(k) == NodeState.PENDING for k in LINEAR_STAGES)


def test_pipeline_entrypoint_skips_upstream() -> None:
    g = build_pipeline("coding")
    assert g.entrypoints == ["coding"]
    assert g.state("idea") == NodeState.SKIPPED
    assert g.state("experiment") == NodeState.SKIPPED
    assert g.state("coding") == NodeState.PENDING
    assert g.state("execution") == NodeState.PENDING
    assert g.state("writing") == NodeState.PENDING
    assert "coding" in g.ready_nodes()


def test_standalone_single_node() -> None:
    g = build_standalone("idea")
    assert list(g.nodes) == ["idea"]
    assert g.nodes["idea"].metadata["standalone"] is True


def test_unknown_entrypoint_rejected() -> None:
    with pytest.raises(ValueError):
        build_pipeline("nonexistent")  # type: ignore[arg-type]
