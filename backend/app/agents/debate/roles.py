"""Role prompts for multi-model debate."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DebateRole:
    name: str
    system_prompt: str


PROPOSER = DebateRole(
    name="proposer",
    system_prompt=(
        "You are the *proposer* in a multi-model debate. "
        "Argue strongly for a concrete, falsifiable hypothesis. "
        "Cite the strongest reasons it should hold."
    ),
)

CRITIC = DebateRole(
    name="critic",
    system_prompt=(
        "You are the *critic* in a multi-model debate. "
        "Identify weaknesses, hidden assumptions, and unstated risks "
        "in the previous turn. Be specific and falsifiable."
    ),
)

JUDGE = DebateRole(
    name="judge",
    system_prompt=(
        "You are the *judge* synthesizing the debate. "
        "Distill the strongest version of both sides; output a balanced, "
        "schema-conformant final artifact that incorporates the debate."
    ),
)

POSITIVE_REVIEWER = DebateRole(
    name="positive_reviewer",
    system_prompt=(
        "You are a positive reviewer. Highlight where the work is novel, "
        "rigorous, or convincing; suggest clarifications that would "
        "strengthen reception."
    ),
)


KNOWN_ROLES: dict[str, DebateRole] = {
    PROPOSER.name: PROPOSER,
    CRITIC.name: CRITIC,
    JUDGE.name: JUDGE,
    POSITIVE_REVIEWER.name: POSITIVE_REVIEWER,
}


def role_prompt(role: str) -> str:
    return KNOWN_ROLES.get(role, DebateRole(role, "")).system_prompt
