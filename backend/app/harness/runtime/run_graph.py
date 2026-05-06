"""Generic DAG / RunGraph.

This module is intentionally agnostic of any specific Agent. Linear topology
(``Idea → Experiment → Coding → Execution → Writing``) is constructed in
``bridge/workflow_service.py`` — never written here.

Per CLAUDE.md hard constraint:
    ❌ 在 harness/runtime/run_graph.py 写死 "Idea→Experiment→..." 线性拓扑
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from app.harness.runtime.state_machine import (
    NodeState,
    assert_transition,
    is_terminal,
)


@dataclass
class GraphNode:
    """A single node in a RunGraph.

    `key` is the stable id used by event_bus channels (typically the agent
    name in the V0 linear pipeline). `kind` is a free-form label so a graph
    can also contain non-Agent nodes (e.g. gates, fan-outs).
    """

    key: str
    kind: str = "agent"
    state: NodeState = NodeState.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    src: str
    dst: str


class GraphError(ValueError):
    pass


class RunGraph:
    """In-memory DAG with topological scheduling.

    Not thread-safe; a higher layer (queue_manager / orchestrator) is the
    only mutator.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._out: dict[str, set[str]] = defaultdict(set)
        self._in: dict[str, set[str]] = defaultdict(set)
        self._entrypoints: list[str] = []

    # ------------------------------------------------------------ build phase

    def add_node(
        self,
        key: str,
        *,
        kind: str = "agent",
        metadata: dict[str, Any] | None = None,
    ) -> GraphNode:
        if key in self._nodes:
            raise GraphError(f"duplicate node key '{key}'")
        node = GraphNode(key=key, kind=kind, metadata=dict(metadata or {}))
        self._nodes[key] = node
        return node

    def add_edge(self, src: str, dst: str) -> None:
        if src not in self._nodes or dst not in self._nodes:
            raise GraphError(f"edge {src}->{dst} references unknown node")
        if src == dst:
            raise GraphError("self-loop disallowed")
        self._out[src].add(dst)
        self._in[dst].add(src)
        self._check_acyclic()

    def set_entrypoint(self, key: str) -> None:
        if key not in self._nodes:
            raise GraphError(f"unknown entrypoint '{key}'")
        if key not in self._entrypoints:
            self._entrypoints.append(key)

    def skip(self, key: str) -> None:
        """Mark a node as skipped *before* the run begins (entrypoint bypass).

        Useful when the user enters the pipeline mid-stream (e.g. starts at
        the Coding Agent with a hand-written experiment_plan).
        """
        if key not in self._nodes:
            raise GraphError(f"unknown node '{key}'")
        node = self._nodes[key]
        if node.state != NodeState.PENDING:
            raise GraphError(f"can only skip pending nodes (got {node.state})")
        node.state = NodeState.SKIPPED

    # --------------------------------------------------------------- queries

    @property
    def nodes(self) -> dict[str, GraphNode]:
        return dict(self._nodes)

    @property
    def edges(self) -> list[GraphEdge]:
        return [GraphEdge(src=s, dst=d) for s, ds in self._out.items() for d in ds]

    @property
    def entrypoints(self) -> list[str]:
        return list(self._entrypoints)

    def predecessors(self, key: str) -> set[str]:
        return set(self._in.get(key, set()))

    def successors(self, key: str) -> set[str]:
        return set(self._out.get(key, set()))

    def state(self, key: str) -> NodeState:
        return self._nodes[key].state

    def all_states(self) -> dict[str, NodeState]:
        return {k: n.state for k, n in self._nodes.items()}

    # ------------------------------------------------------------- mutation

    def transition(self, key: str, new_state: NodeState) -> None:
        node = self._nodes[key]
        assert_transition(node.state, new_state)
        node.state = new_state

    # --------------------------------------------------------- scheduling

    def topological_order(self) -> list[str]:
        """Kahn's algorithm. Raises if cyclic."""
        indeg = {k: len(self._in.get(k, ())) for k in self._nodes}
        q: deque[str] = deque(k for k, d in indeg.items() if d == 0)
        out: list[str] = []
        while q:
            cur = q.popleft()
            out.append(cur)
            for nxt in sorted(self._out.get(cur, set())):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        if len(out) != len(self._nodes):
            raise GraphError("graph contains a cycle")
        return out

    def ready_nodes(self) -> list[str]:
        """Pending nodes whose every predecessor is in {DONE, SKIPPED, APPROVED}.

        APPROVED counts as ready-for-downstream because some flows transition
        approved → done lazily after the *next* node consumes the artifact.
        """
        ready_states: frozenset[NodeState] = frozenset(
            {NodeState.DONE, NodeState.SKIPPED, NodeState.APPROVED}
        )
        out: list[str] = []
        for key, node in self._nodes.items():
            if node.state != NodeState.PENDING:
                continue
            preds = self._in.get(key, set())
            if all(self._nodes[p].state in ready_states for p in preds):
                out.append(key)
        return out

    def is_complete(self) -> bool:
        return all(is_terminal(n.state) for n in self._nodes.values())

    # --------------------------------------------------------- introspection

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "key": k,
                    "kind": n.kind,
                    "state": n.state.value,
                    "metadata": dict(n.metadata),
                }
                for k, n in self._nodes.items()
            ],
            "edges": [{"src": e.src, "dst": e.dst} for e in self.edges],
            "entrypoints": list(self._entrypoints),
        }

    # --------------------------------------------------------------- private

    def _check_acyclic(self) -> None:
        # quick cycle check via topo
        try:
            self.topological_order()
        except GraphError:
            raise
