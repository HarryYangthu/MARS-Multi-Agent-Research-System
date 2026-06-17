"""Conversation-level state machine for the Commander (master Agent).

This is the UPPER state machine. It models the dialogue session between the
user and the Commander, and is deliberately separate from the per-node
pipeline state machine in ``state_machine.py`` (the LOWER machine that tracks
idea/experiment/coding/execution/writing node states).

    idle ─► clarifying ─► planning ─► awaiting_confirm ─► executing
      ▲          │            │              │               │
      │          └────────────┴──────────────┘               ▼
      │                                              awaiting_review
      │                                                      │
      └──────────────── reporting ◄────────────────────────-┘

The Commander drives transitions based on the LLM's chosen action plus the
underlying run's pipeline state. Transitions are validated here so the API,
the Commander, and the frontend all agree on what is legal.

This module is agent-agnostic: it knows nothing about idea/coding/etc. It only
knows the shape of a conversation. (harness/ must not import bridge/agents.)
"""
from __future__ import annotations

from enum import Enum


class ConversationState(str, Enum):
    IDLE = "idle"                       # waiting for the user to say something
    CLARIFYING = "clarifying"           # Commander is asking follow-up questions
    PLANNING = "planning"               # Commander is shaping a pipeline/plan
    AWAITING_CONFIRM = "awaiting_confirm"  # plan ready, waiting for user "go"
    EXECUTING = "executing"             # a run is in flight, Commander monitors
    AWAITING_REVIEW = "awaiting_review"  # a node hit HITL / feedback decision pending
    REPORTING = "reporting"             # Commander is summarizing results


# Legal forward transitions. Every state may also return to IDLE (reset/cancel)
# and may stay on itself (multi-turn within the same phase).
_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    ConversationState.IDLE: frozenset(
        {ConversationState.CLARIFYING, ConversationState.PLANNING,
         ConversationState.EXECUTING, ConversationState.REPORTING}
    ),
    ConversationState.CLARIFYING: frozenset(
        {ConversationState.PLANNING, ConversationState.EXECUTING}
    ),
    ConversationState.PLANNING: frozenset(
        {ConversationState.AWAITING_CONFIRM, ConversationState.EXECUTING,
         ConversationState.CLARIFYING}
    ),
    ConversationState.AWAITING_CONFIRM: frozenset(
        {ConversationState.EXECUTING, ConversationState.PLANNING,
         ConversationState.CLARIFYING}
    ),
    ConversationState.EXECUTING: frozenset(
        {ConversationState.AWAITING_REVIEW, ConversationState.REPORTING,
         ConversationState.EXECUTING}
    ),
    ConversationState.AWAITING_REVIEW: frozenset(
        {ConversationState.EXECUTING, ConversationState.REPORTING}
    ),
    ConversationState.REPORTING: frozenset(
        {ConversationState.PLANNING, ConversationState.CLARIFYING,
         ConversationState.EXECUTING}
    ),
}


class IllegalConversationTransition(ValueError):
    pass


def can_transition(src: ConversationState, dst: ConversationState) -> bool:
    if src == dst:
        return True
    if dst == ConversationState.IDLE:
        return True  # cancel/reset is always allowed
    return dst in _TRANSITIONS[src]


def assert_transition(src: ConversationState, dst: ConversationState) -> None:
    if not can_transition(src, dst):
        raise IllegalConversationTransition(
            f"conversation {src.value} -> {dst.value} is not allowed"
        )


# Human-readable labels (zh + en) for the frontend status pill.
LABELS_ZH: dict[ConversationState, str] = {
    ConversationState.IDLE: "待命",
    ConversationState.CLARIFYING: "澄清需求",
    ConversationState.PLANNING: "规划中",
    ConversationState.AWAITING_CONFIRM: "等待确认",
    ConversationState.EXECUTING: "执行中",
    ConversationState.AWAITING_REVIEW: "等待审核",
    ConversationState.REPORTING: "汇报中",
}

LABELS_EN: dict[ConversationState, str] = {
    ConversationState.IDLE: "idle",
    ConversationState.CLARIFYING: "clarifying",
    ConversationState.PLANNING: "planning",
    ConversationState.AWAITING_CONFIRM: "awaiting confirm",
    ConversationState.EXECUTING: "executing",
    ConversationState.AWAITING_REVIEW: "awaiting review",
    ConversationState.REPORTING: "reporting",
}
