"""Critic-style judge synthesis. Used when an agent wants to combine
multiple debate turns into a single artifact."""
from __future__ import annotations

from app.agents.debate.debate_runner import DebateResult


def synthesize(result: DebateResult) -> str:
    """Return a markdown-formatted synthesis of the debate.

    For V0 we lean on the runner having already picked the judge's last
    turn as ``result.final_artifact``; this helper just exposes a stable
    name so external callers can route the artifact without poking
    internals.
    """
    if result.final_artifact is None:
        return ""
    return result.final_artifact.text
