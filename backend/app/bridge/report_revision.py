"""Append an explicit report correction without reopening completed experiments."""
from __future__ import annotations

from app.bridge.node_key import attempt_key, parse_node_key
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState


def append_report_revision(graph: RunGraph, previous: str, reason: str) -> tuple[RunGraph, str]:
    if not reason.strip():
        raise ValueError('报告修订必须说明具体原因')
    if parse_node_key(previous).stage != 'writing' or graph.state(previous) != NodeState.DONE:
        raise ValueError('仅允许追加已完成报告的修订')
    if graph.successors(previous) or any(
        state not in {NodeState.DONE, NodeState.SKIPPED} for state in graph.all_states().values()
    ):
        raise ValueError('报告仍有下游或其他未完成阶段，不能追加完成后修订')
    result = RunGraph.from_dict(graph.to_dict())
    attempt = max(parse_node_key(key).attempt for key in graph.nodes
                  if parse_node_key(key).stage == 'writing') + 1
    node = attempt_key('writing', attempt)
    result.add_node(node, metadata={'stage': 'writing', 'attempt': attempt,
        'report_revision': {'previous_node': previous, 'reason': reason.strip()}})
    result.add_edge(previous, node)
    return result, node
