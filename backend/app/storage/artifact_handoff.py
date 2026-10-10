"""Artifact requirements derived from the persisted dependency graph."""
from __future__ import annotations
from app.storage.run_store import RunHandle

def experiment_plan_required(run: RunHandle, node_key: str) -> bool:
    """Use the authoritative dependency graph, never infer a task type from a file."""
    from app.harness.runtime.state_machine import NodeState
    from app.storage.run_state_store import RunStateStore
    snapshot = RunStateStore(run).load()
    if snapshot is None or node_key not in snapshot.graph.nodes:
        return False
    pending = list(snapshot.graph.predecessors(node_key))
    seen: set[str] = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        if (snapshot.graph.metadata(key).get('stage') == 'experiment'
                and snapshot.graph.state(key) != NodeState.SKIPPED):
            return True
        pending.extend(snapshot.graph.predecessors(key))
    return False


def execution_delivery_required(run: RunHandle, node_key: str) -> bool:
    from app.harness.runtime.state_machine import NodeState
    from app.storage.run_state_store import RunStateStore
    snapshot = RunStateStore(run).load()
    if snapshot is None or node_key not in snapshot.graph.nodes:
        return False
    pending = list(snapshot.graph.successors(node_key))
    seen: set[str] = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        if (snapshot.graph.metadata(key).get('stage') == 'execution'
                and snapshot.graph.state(key) != NodeState.SKIPPED):
            return True
        pending.extend(snapshot.graph.successors(key))
    return False
