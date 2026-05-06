"""Per-node state machine.

State diagram (per ACCEPTANCE §11 Phase 2):

    pending  ─► running ─► waiting_review ─► approved ─► done
       │           │              │              │
       │           ▼              ▼              ▼
       │         failed       rejected         (final)
       │           │              │
       └─►  skipped (entrypoint bypass)

Transitions are centralized so the runtime, the bridge, and the API all agree.
"""
from __future__ import annotations

from enum import Enum


class NodeState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    APPROVED = "approved"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


_TERMINAL: frozenset[NodeState] = frozenset(
    {NodeState.DONE, NodeState.FAILED, NodeState.SKIPPED}
)

# transitions: src -> allowed targets
_TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    NodeState.PENDING: frozenset(
        {NodeState.RUNNING, NodeState.SKIPPED, NodeState.FAILED}
    ),
    NodeState.RUNNING: frozenset(
        {NodeState.WAITING_REVIEW, NodeState.FAILED, NodeState.DONE}
    ),
    NodeState.WAITING_REVIEW: frozenset(
        {NodeState.APPROVED, NodeState.RUNNING, NodeState.FAILED}
    ),
    NodeState.APPROVED: frozenset({NodeState.DONE, NodeState.RUNNING}),
    NodeState.DONE: frozenset(),
    NodeState.FAILED: frozenset({NodeState.RUNNING}),  # rerun allowed
    NodeState.SKIPPED: frozenset(),
}


class IllegalTransition(ValueError):
    pass


def can_transition(src: NodeState, dst: NodeState) -> bool:
    if src == dst:
        return True
    return dst in _TRANSITIONS[src]


def assert_transition(src: NodeState, dst: NodeState) -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(f"{src.value} -> {dst.value} is not allowed")


def is_terminal(state: NodeState) -> bool:
    return state in _TERMINAL
