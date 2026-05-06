from __future__ import annotations

import pytest

from app.harness.runtime.state_machine import (
    IllegalTransition,
    NodeState,
    assert_transition,
    can_transition,
    is_terminal,
)


def test_pending_to_running_allowed() -> None:
    assert can_transition(NodeState.PENDING, NodeState.RUNNING)


def test_pending_to_done_disallowed() -> None:
    assert not can_transition(NodeState.PENDING, NodeState.DONE)
    with pytest.raises(IllegalTransition):
        assert_transition(NodeState.PENDING, NodeState.DONE)


def test_done_terminal() -> None:
    assert is_terminal(NodeState.DONE)
    assert is_terminal(NodeState.SKIPPED)
    assert not is_terminal(NodeState.RUNNING)


def test_failed_can_rerun() -> None:
    assert can_transition(NodeState.FAILED, NodeState.RUNNING)


def test_self_transition_idempotent() -> None:
    assert can_transition(NodeState.RUNNING, NodeState.RUNNING)
