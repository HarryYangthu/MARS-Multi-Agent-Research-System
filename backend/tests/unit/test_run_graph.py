from __future__ import annotations

import pytest

from app.harness.runtime.run_graph import GraphError, RunGraph
from app.harness.runtime.state_machine import NodeState


def test_empty_graph_is_complete() -> None:
    g = RunGraph()
    assert g.is_complete() is True
    assert g.topological_order() == []


def test_single_node_linear() -> None:
    g = RunGraph()
    g.add_node("a")
    g.set_entrypoint("a")
    assert g.entrypoints == ["a"]
    assert g.ready_nodes() == ["a"]
    g.transition("a", NodeState.RUNNING)
    assert g.ready_nodes() == []  # 'a' is no longer pending
    g.transition("a", NodeState.WAITING_REVIEW)
    g.transition("a", NodeState.APPROVED)
    g.transition("a", NodeState.DONE)
    assert g.is_complete()


def test_five_node_linear_topology() -> None:
    g = RunGraph()
    for k in ["idea", "experiment", "coding", "execution", "writing"]:
        g.add_node(k)
    for s, d in [("idea", "experiment"), ("experiment", "coding"), ("coding", "execution"), ("execution", "writing")]:
        g.add_edge(s, d)
    assert g.topological_order() == ["idea", "experiment", "coding", "execution", "writing"]
    assert g.ready_nodes() == ["idea"]


def test_skip_propagates_to_downstream() -> None:
    g = RunGraph()
    for k in ["a", "b", "c"]:
        g.add_node(k)
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.skip("a")
    # b is now ready because predecessor 'a' is SKIPPED
    assert g.ready_nodes() == ["b"]


def test_cycle_rejected() -> None:
    g = RunGraph()
    g.add_node("a")
    g.add_node("b")
    g.add_edge("a", "b")
    with pytest.raises(GraphError):
        g.add_edge("b", "a")


def test_unknown_edge_rejected() -> None:
    g = RunGraph()
    g.add_node("a")
    with pytest.raises(GraphError):
        g.add_edge("a", "b")


def test_self_loop_rejected() -> None:
    g = RunGraph()
    g.add_node("a")
    with pytest.raises(GraphError):
        g.add_edge("a", "a")


def test_to_dict_round_trip_keys() -> None:
    g = RunGraph()
    g.add_node("a")
    g.add_node("b")
    g.add_edge("a", "b")
    g.set_entrypoint("a")
    out = g.to_dict()
    assert {n["key"] for n in out["nodes"]} == {"a", "b"}
    assert out["edges"] == [{"src": "a", "dst": "b"}]
    assert out["entrypoints"] == ["a"]
