"""State machines for the run lifecycle.

Three layers, all centralized so the runtime, the bridge, and the API agree:

* ``NodeState``   — per-pipeline-node (one Agent's work).
* ``RunState``    — the whole run's status (drives recovery + UI top-level badge).
* ``ReviewState`` — a single HITL review decision on one artifact version.

Node diagram (per ACCEPTANCE §11 Phase 2):

    pending  ─► running ─► waiting_review ─► approved ─► done
       │  ▲        │              │              │
       │  │        ▼              ▼              ▼
       │  └──── failed        rejected         (final)
       │  (retry)  │
       └─►  skipped (entrypoint bypass)
"""
from __future__ import annotations

from enum import Enum
from typing import Any


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
    # rerun allowed (RUNNING) or full reset for retry/re-route (PENDING)
    NodeState.FAILED: frozenset({NodeState.RUNNING, NodeState.PENDING}),
    NodeState.SKIPPED: frozenset(),
}


class RunState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    REPAIRING = "repairing"  # self-heal feedback loop in flight (Phase D)
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_RUN_TERMINAL: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
)

_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(
        {
            RunState.WAITING_HUMAN,
            RunState.REPAIRING,
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.WAITING_HUMAN: frozenset(
        {RunState.RUNNING, RunState.REPAIRING, RunState.CANCELLED}
    ),
    RunState.REPAIRING: frozenset(
        {RunState.RUNNING, RunState.WAITING_HUMAN, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset({RunState.RUNNING}),  # retry a failed run
    RunState.CANCELLED: frozenset(),
}


class ReviewState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"  # human edited the artifact, still awaiting approve/reject


_REVIEW_TRANSITIONS: dict[ReviewState, frozenset[ReviewState]] = {
    ReviewState.PENDING: frozenset(
        {ReviewState.APPROVED, ReviewState.REJECTED, ReviewState.EDITED}
    ),
    ReviewState.EDITED: frozenset(
        {ReviewState.APPROVED, ReviewState.REJECTED, ReviewState.EDITED}
    ),
    ReviewState.APPROVED: frozenset(),
    ReviewState.REJECTED: frozenset(),
}


class IllegalTransition(ValueError):
    pass


def _allowed(table: dict[Any, frozenset[Any]], src: Any, dst: Any) -> bool:
    return src == dst or dst in table[src]


def can_transition(src: NodeState, dst: NodeState) -> bool:
    return _allowed(_TRANSITIONS, src, dst)


def assert_transition(src: NodeState, dst: NodeState) -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(f"node: {src.value} -> {dst.value} is not allowed")


def is_terminal(state: NodeState) -> bool:
    return state in _TERMINAL


def run_can_transition(src: RunState, dst: RunState) -> bool:
    return _allowed(_RUN_TRANSITIONS, src, dst)


def run_assert_transition(src: RunState, dst: RunState) -> None:
    if not run_can_transition(src, dst):
        raise IllegalTransition(f"run: {src.value} -> {dst.value} is not allowed")


def run_is_terminal(state: RunState) -> bool:
    return state in _RUN_TERMINAL


def review_can_transition(src: ReviewState, dst: ReviewState) -> bool:
    return _allowed(_REVIEW_TRANSITIONS, src, dst)


def review_assert_transition(src: ReviewState, dst: ReviewState) -> None:
    if not review_can_transition(src, dst):
        raise IllegalTransition(f"review: {src.value} -> {dst.value} is not allowed")
