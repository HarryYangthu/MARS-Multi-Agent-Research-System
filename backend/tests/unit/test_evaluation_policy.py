from __future__ import annotations

from app.bridge.evaluation_policy import evaluate_artifact_summary, evaluate_scorecard


def test_artifact_policy_escalates_low_score_to_revision() -> None:
    decision = evaluate_artifact_summary(
        {
            "decision": "pass",
            "blocking": False,
            "overall_score": 0.6,
            "top_findings": [],
            "reports": [],
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
            "reports": [],
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
