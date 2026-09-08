"""Opt-in negative terminal decisions at safe loop boundaries."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Literal

from app.harness.agent_loop.trace import canonical, digest


@dataclass(frozen=True)
class LoopStopView:
    stage: Literal["before_model", "after_validation"]
    candidate: str
    observations: list[dict[str, Any]]
    counts: dict[str, int]
    phase: str


@dataclass(frozen=True)
class LoopStop:
    status: str
    reason: str
    details: dict[str, Any]

    def __post_init__(self) -> None:
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.status)
                or self.status in {"passed", "running", "interrupted", "model_error"}):
            raise ValueError("stop status must be a distinct negative terminal status")
        if not self.reason.strip() or len(self.reason) > 2400:
            raise ValueError("stop reason must be nonempty and bounded")
        canonical(self.details)


StopCondition = Callable[[LoopStopView], LoopStop | None]


def stop_fingerprint(base: str, condition: StopCondition | None, contract_id: str | None) -> str:
    """Unconfigured historical loops retain their existing fingerprint."""
    if condition is None and contract_id is None:
        return base
    if condition is None or not isinstance(contract_id, str) or not contract_id.strip() or len(contract_id) > 128:
        raise ValueError("stop_condition requires a stable nonempty stop_contract_id")
    return digest({"base": base, "stop_contract_id": contract_id})


def evaluate_stop(condition: StopCondition | None, state: dict[str, Any], *,
                  stage: Literal["before_model", "after_validation"]) -> LoopStop | None:
    """Never run a host stop hook inside reflection or an unfinished action."""
    if (condition is None or state["next_phase"] != "act" or state.get("pending")
            or state.get("pending_batch")):
        return None
    if stage == "after_validation" and state.get("validation_issues"):
        return None
    view = LoopStopView(stage, state["candidate"], deepcopy(state["history"]),
                        dict(state["counts"]), state["next_phase"])
    result = condition(view)
    if result is not None and not isinstance(result, LoopStop):
        raise TypeError("stop_condition must return LoopStop or None")
    return result
