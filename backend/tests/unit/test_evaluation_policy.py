from __future__ import annotations

import pytest
from app.bridge.evaluation_policy import evaluate_artifact_summary, evaluate_scorecard, policy_for_task


def test_artifact_policy_escalates_low_score_to_revision() -> None:
    decision = evaluate_artifact_summary(
        {
            "decision": "pass",
            "blocking": False,
            "overall_score": 0.6,
            "top_findings": [],
            "reports": [{"decision": "pass", "overall_score": 0.6}],
        }
    )

    assert decision["gate"] == "revise"
    assert decision["action"] == "request_revision"
    assert decision["review_priority"] == "high"
    assert decision["auto_approval_allowed"] is False


def test_artifact_policy_blocks_on_blocker_finding() -> None:
    decision = evaluate_artifact_summary(
        {
            "decision": "warn",
            "blocking": False,
            "overall_score": 0.9,
            "top_findings": [{"severity": "blocker", "message": "bad schema"}],
            "reports": [{"decision": "warn", "overall_score": 0.9}],
        }
    )

    assert decision["gate"] == "block"
    assert decision["review_priority"] == "critical"
    assert "blocker finding present" in decision["reasons"]


def test_run_quality_gate_is_audit_only_by_default() -> None:
    gate = evaluate_scorecard(
        {
            "overall_decision": "block",
            "overall_score": 0.9,
            "reports": [{"blocking": True}],
            "top_findings": [],
        }
    )

    assert gate["gate"] == "block"
    assert gate["completion_allowed"] is True
    assert gate["enforcement_mode"] == "audit_only"
    assert gate["action"] == "complete_with_quality_exception"


def test_task_can_enforce_missing_evaluation_without_claiming_scientific_success() -> None:
    policy = policy_for_task({"run": {"completion_gate": {"mode": "enforce"}}})
    gate = evaluate_scorecard({}, policy=policy)
    assert gate["completion_allowed"] is False
    assert gate["quality_status"] == "missing"
    assert gate["scientific_validated"] is False
    with pytest.raises(ValueError, match="unknown"):
        policy_for_task({"shell": "arbitrary"})
    with pytest.raises(ValueError, match="finite"):
        policy_for_task({"run": {"pass_min_score": float("nan")}})
