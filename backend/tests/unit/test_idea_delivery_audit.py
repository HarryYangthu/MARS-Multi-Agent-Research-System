"""Audit real delivery files authored for contract tests; no Agent execution is claimed."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import Artifact, RunRequest
from app.agents.idea.delivery import write_delivery
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.schema.frontmatter_parser import dumps
from scripts.audit_idea_run import audit_delivery, recorded_delivery, render_report


def _metadata() -> dict[str, Any]:
    return {"schema": "proposal.v1", "project": "pimc", "agent": "idea",
            "research_question": "How should the two authored methods be compared?",
            "hypothesis": "This authored method requires actual research and validation.",
            "novelty": "Novelty has not been established.",
            "human_summary": "比较两种方法，判断哪个值得开展后续实验。",
            "method_spec": {"candidate": {"definition": "Human-authored serializer input"}},
            "decision_rule": {"comparison": "A human-authored comparison definition"},
            "handoff": {"version": "idea.handoff.v1", "target_agent": "experiment", "scope": "method_proposal",
                        "next_step": "Prepare the comparison after the actual prerequisites are supplied.",
                        "changes": [{"target": "component", "operation": "modify", "spec_ref": "/method_spec/candidate", "preserve": []}],
                        "verification_requirements": [{"id": "V1", "question": "Which method?", "comparison": "A vs B",
                                                       "metric": "error", "decision_rule_ref": "/decision_rule"}],
                        "required_context": [{"kind": kind, "description": "caller input", "reason": "required for execution", "blocks_execution": True}
                                             for kind in ("baseline_code", "data_description")]}}


def _write_authored_delivery(root: Path) -> tuple[str, dict[str, Any], Path]:
    metadata = _metadata()
    text = dumps(metadata, str(metadata["human_summary"]))
    request = RunRequest("pimc", "Human-authored serialization input", extra={"run_root": str(root), "scope": "method_proposal"})
    delivery = write_delivery(Artifact(text, "proposal.v1", metadata, str(metadata["human_summary"])),
                              request, invocation="invocation-a", reviewed=False)
    atomic_json(root / "idea/validation/receipt.json", {
        "candidate_sha256": digest(text), "scope": "method_proposal",
        "delivery_contract_version": "idea.handoff.v1", "body_policy": "summary_only",
    })
    summary = {"delivery_root": str(delivery), "human_summary": metadata["human_summary"], "handoff": metadata["handoff"]}
    return text, summary, delivery


def _audit(root: Path, summary: dict[str, Any], text: str) -> dict[str, Any]:
    return audit_delivery(root, summary, text, invocation="invocation-a", scope="method_proposal", model_review_passed=False)


def test_audit_rechecks_receipt_and_all_actual_export_files(tmp_path: Path) -> None:
    text, summary, delivery = _write_authored_delivery(tmp_path)
    report = _audit(tmp_path, summary, text)
    assert report["errors"] == []
    assert report["delivery_contract_valid"] is True
    assert report["delivery_bundle_valid"] is True
    assert report["validation_receipts"] == ["idea/validation/receipt.json"]
    assert report["delivery_root"] == str(delivery)
    summary["delivery_root"] = delivery.relative_to(tmp_path).as_posix()
    assert _audit(tmp_path, summary, text)["errors"] == []
    assert not list(tmp_path.rglob("checkpoint.json"))


@pytest.mark.parametrize("filename", ["proposal.md", "proposal.json", "summary.txt", "acceptance.json"])
def test_audit_rejects_changed_or_missing_export_files(tmp_path: Path, filename: str) -> None:
    text, summary, delivery = _write_authored_delivery(tmp_path)
    path = delivery / filename
    path.write_text("Changed contract content", encoding="utf-8")
    report = _audit(tmp_path, summary, text)
    assert report["delivery_bundle_valid"] is False
    assert any(filename in error for error in report["errors"])
    path.unlink()
    assert any(filename in error for error in _audit(tmp_path, summary, text)["errors"])


@pytest.mark.parametrize("field,value", [
    ("proposal_sha256", "another candidate"), ("model_review_passed", True),
    ("simulation_executed", True), ("scientific_validated", True),
    ("execution_requires_context", False), ("scope", "project_proposal"),
])
def test_acceptance_file_cannot_override_audited_facts(tmp_path: Path, field: str, value: object) -> None:
    text, summary, delivery = _write_authored_delivery(tmp_path)
    path = delivery / "acceptance.json"
    acceptance = json.loads(path.read_text(encoding="utf-8"))
    acceptance[field] = value
    atomic_json(path, acceptance)
    report = _audit(tmp_path, summary, text)
    assert report["delivery_bundle_valid"] is False
    assert any("acceptance.json" in error for error in report["errors"])


@pytest.mark.parametrize("path", ["../other-run/idea/deliveries/invocation-a/export", "idea/proposal.md", "idea/deliveries/other-invocation/export", ""])
def test_recorded_delivery_must_belong_to_this_run_and_invocation(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError, match="delivery"):
        recorded_delivery(tmp_path, {"delivery_root": path}, invocation="invocation-a")


def test_audit_does_not_fall_back_to_a_sibling_export(tmp_path: Path) -> None:
    text, summary, delivery = _write_authored_delivery(tmp_path)
    summary["delivery_root"] = str(delivery.parent / "missing-export")
    report = _audit(tmp_path, summary, text)
    assert report["delivery_bundle_valid"] is False
    assert "missing-export" in report["delivery_root"]


def test_runner_summary_must_match_the_accepted_human_and_machine_outputs(tmp_path: Path) -> None:
    text, summary, _ = _write_authored_delivery(tmp_path)
    summary["human_summary"] = "A different candidate summary"
    summary["handoff"] = {"different": "handoff"}
    errors = _audit(tmp_path, summary, text)["errors"]
    assert any("runner summary human_summary" in error for error in errors)
    assert any("runner summary handoff" in error for error in errors)


def test_audit_rejects_receipts_for_a_different_candidate(tmp_path: Path) -> None:
    text, summary, _ = _write_authored_delivery(tmp_path)
    path = tmp_path / "idea/validation/receipt.json"
    record = json.loads(path.read_text())
    record["candidate_sha256"] = digest("another authored candidate")
    atomic_json(path, record)
    assert any("exact-candidate" in error for error in _audit(tmp_path, summary, text)["errors"])
    path.unlink()
    assert any("exact-candidate" in error for error in _audit(tmp_path, summary, text)["errors"])


def test_receipts_of_other_candidates_cannot_change_the_selected_contract(tmp_path: Path) -> None:
    text, summary, _ = _write_authored_delivery(tmp_path)
    atomic_json(tmp_path / "idea/validation/other.json", {
        "candidate_sha256": digest("another candidate"), "delivery_contract_version": "unsupported",
        "scope": "other", "body_policy": "summary_only",
    })
    assert _audit(tmp_path, summary, text)["errors"] == []


def test_audit_rechecks_summary_only_policy_from_the_exact_receipt(tmp_path: Path) -> None:
    _, summary, _ = _write_authored_delivery(tmp_path)
    metadata = _metadata()
    text = dumps(metadata, str(metadata["human_summary"]) + "\n\nA contradictory second method definition.")
    atomic_json(tmp_path / "idea/validation/receipt.json", {
        "candidate_sha256": digest(text), "scope": "method_proposal",
        "delivery_contract_version": "idea.handoff.v1", "body_policy": "summary_only",
    })
    report = _audit(tmp_path, summary, text)
    assert report["delivery_contract_valid"] is False
    assert any("/body" in error for error in report["errors"])


def test_legacy_receipts_do_not_require_new_handoff_or_export(tmp_path: Path) -> None:
    metadata = {key: value for key, value in _metadata().items()
                if key in {"schema", "project", "agent", "research_question", "hypothesis", "novelty"}}
    text = dumps(metadata, "A legacy human-authored narrative.")
    atomic_json(tmp_path / "idea/validation/legacy.json", {"candidate_sha256": digest(text), "scope": "method_proposal"})
    report = _audit(tmp_path, {}, text)
    assert report["errors"] == []
    assert report["delivery_contract_valid"] is None
    assert report["delivery_bundle_valid"] is None


def test_report_shows_both_outputs_and_separates_experiments_from_model_review() -> None:
    metadata = _metadata()
    # A report-rendering input with zero executions is not an Agent result.
    report: dict[str, Any] = {
        "run_id": "human-authored-render-input", "source_commit": "unexecuted", "source_tree": "unexecuted",
        "audit_passed": False, "recorded_status": "unexecuted", "schema_valid": True, "material_valid": False,
        "reflection_accepted": False, "model_review_passed": False, "trace_consistent": False,
        "delivery_contract_valid": None, "delivery_bundle_valid": None, "simulation_executed": False,
        "scientific_validated": False, "human_summary": metadata["human_summary"], "handoff": metadata["handoff"],
        "counts": {key: 0 for key in ("model_requests", "model_responses", "sdk_attempts", "tool_dispatches", "observations",
                                     "protocol_repairs", "validation_repairs", "reflections")},
        "usage": {}, "usage_complete": False, "tools": [], "evidence_counts": {}, "downloaded_sources": [],
        "read_windows": [], "errors": ["No execution occurred"], "limits": [],
    }
    rendered = render_report(report)
    assert metadata["human_summary"] in rendered
    assert '"version": "idea.handoff.v1"' in rendered
    assert "模型审查通过：False" in rendered
    assert "实验执行：False；科学验证：False" in rendered
