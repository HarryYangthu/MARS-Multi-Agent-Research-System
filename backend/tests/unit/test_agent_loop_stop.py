"""Pure stop contracts; no provider, tool, or execution substitute."""
from __future__ import annotations

from typing import Any

import pytest

from app.harness.agent_loop.stop import LoopStop, LoopStopView, evaluate_stop, stop_fingerprint


def state() -> dict[str, Any]:
    return {"next_phase": "act", "candidate": "", "history": [], "counts": {"model_requests": 0},
            "pending": None, "pending_batch": False, "validation_issues": []}


def stop(_view: LoopStopView) -> LoopStop:
    return LoopStop("evidence_unavailable", "No reading budget remains.", {"usable_as_evidence": False})


@pytest.mark.parametrize("status", ["passed", "running", "interrupted", "model_error", "", "bad status"])
def test_stop_cannot_be_a_success_or_resumable_pending_state(status: str) -> None:
    with pytest.raises(ValueError, match="negative terminal"):
        LoopStop(status, "Authored contract reason", {})


def test_stop_requires_bounded_reason_and_serializable_details() -> None:
    for reason in (" ", "x" * 2401):
        with pytest.raises(ValueError):
            LoopStop("evidence_unavailable", reason, {})
    with pytest.raises(ValueError):
        LoopStop("evidence_unavailable", "reason", {"not_finite": float("nan")})


def test_unconfigured_fingerprint_is_unchanged_but_configured_contract_is_bound() -> None:
    assert stop_fingerprint("original", None, None) == "original"
    assert stop_fingerprint("original", stop, "contract.v1") != "original"
    assert stop_fingerprint("original", stop, "contract.v1") != stop_fingerprint("original", stop, "contract.v2")
    for hook, contract in ((stop, None), (None, "contract.v1"), (stop, " ")):
        with pytest.raises(ValueError, match="stop_contract_id"):
            stop_fingerprint("original", hook, contract)


@pytest.mark.parametrize("update", [{"next_phase": "reflect"}, {"pending": "model"},
                                    {"pending": "tool"}, {"pending_batch": True}])
def test_hook_is_not_invoked_during_review_or_incomplete_actions(update: dict[str, Any]) -> None:
    def must_not_run(_view: LoopStopView) -> LoopStop:
        raise AssertionError("unsafe stop boundary")
    assert evaluate_stop(must_not_run, state() | update, stage="before_model") is None


def test_invalid_submission_cannot_take_after_validation_shortcut() -> None:
    assert evaluate_stop(stop, state() | {"validation_issues": ["missing evidence"]}, stage="after_validation") is None


def test_hook_views_do_not_mutate_loop_history_or_counts() -> None:
    current = state()
    def mutate_view(view: LoopStopView) -> LoopStop:
        view.counts["model_requests"] = 99
        view.observations.append({"authored": "isolated view mutation"})
        return stop(view)
    outcome = evaluate_stop(mutate_view, current, stage="before_model")
    assert outcome is not None and outcome.status == "evidence_unavailable"
    assert current["counts"]["model_requests"] == 0
    assert current["history"] == []


def test_validated_negative_document_can_stop_without_review() -> None:
    outcome = evaluate_stop(stop, state(), stage="after_validation")
    assert outcome is not None and outcome.status != "passed"
