"""Authored declaration contracts and opt-in unchanged real failure archives."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.idea.research_delegate import (
    load_delegated_research, research_policy, research_review_errors, resumed_delegation_count,
)
from app.agents.idea.research_dossier import dossier_errors
from app.agents.idea.research_gap import (
    GAP_SCHEMA, actual_attempts, evidence_stop, failure_record, gap_errors, gap_schema,
    material_state, research_submission_schema,
)
from app.harness.agent_loop.protocol import parse_action
from app.harness.agent_loop.stop import LoopStopView
from app.harness.agent_loop.trace import digest
from app.harness.llm.model_registry import get_agent_config


def declaration() -> dict[str, Any]:
    # An authored negative document for schema testing, not a model response.
    return {"schema": GAP_SCHEMA, "project": "pimc", "human_summary": "目前材料不足，不能形成有证据的研究结论。",
            "reason": "无法在当前取证资源内完成方法页核对。", "remaining_gaps": ["需要核对方法的适用条件。"],
            "next_actions": ["可用取证条件变化后继续查阅实际方法页。"]}


def document(metadata: dict[str, Any]) -> str:
    return str(parse_action(json.dumps({"final": {"metadata": metadata, "body": metadata["human_summary"]}}))["final"])


def test_gap_is_an_explicit_submission_but_never_a_research_report() -> None:
    metadata = declaration()
    assert gap_errors(metadata, project="pimc") == []
    assert not list(Draft202012Validator(research_submission_schema()).iter_errors(metadata))
    assert dossier_errors(metadata, [], min_sources=2)
    assert load_delegated_research(Path("/unused"), [{"tool": "idea.research_delegate", "ok": False,
                                                    "output": metadata}]) == ([], [])


@pytest.mark.parametrize("update", [{"remaining_gaps": []}, {"reason": " "}, {"reason": "x"*801},
                                    {"next_actions": ["repeat"]*6}, {"schema": "research_report.v1"},
                                    {"sources": []}, {"scientific_validated": True}, {"project": "other"}])
def test_gap_rejects_empty_unbounded_or_success_shaped_declarations(update: dict[str, Any]) -> None:
    assert gap_errors(declaration() | update, project="pimc")


def test_gap_stop_is_negative_and_only_uses_validated_document() -> None:
    view = LoopStopView("after_validation", document(declaration()), [], {"tool_dispatches": 0}, "act")
    outcome = evidence_stop(view, min_sources=2, max_tool_steps=10, tools=("search.fetch_sources",), project="pimc")
    assert outcome is not None and outcome.status == "evidence_unavailable"
    assert outcome.details["origin"] == "researcher_gap_declaration"
    assert evidence_stop(replace(view, candidate="not a document"), min_sources=2, max_tool_steps=10,
                         tools=("search.fetch_sources",), project="pimc") is None


def test_no_receipts_and_no_acquisition_budget_stop_before_model_repairs() -> None:
    assert material_state([])["counts"]["distinct_read_publications"] == 0
    view = LoopStopView("before_model", "", [], {"tool_dispatches": 0}, "act")
    assert evidence_stop(view, min_sources=2, max_tool_steps=0, tools=("search.fetch_sources",), project="pimc")
    assert evidence_stop(view, min_sources=2, max_tool_steps=10, tools=(), project="pimc")
    assert evidence_stop(view, min_sources=2, max_tool_steps=10, tools=("search.fetch_sources",), project="pimc") is None


def test_formal_dossier_cannot_disable_research_reflection() -> None:
    config = get_agent_config("idea_research")
    changed = replace(config, raw={**config.raw, "loop": {**config.raw.get("loop", {}), "mode": "react"}})
    with pytest.raises(ValueError, match="react cannot bypass"):
        research_policy(changed, require_review=True)
    assert research_policy(changed, require_review=False).mode == "react"


def test_review_claim_contract_preserves_history_and_rejects_wrong_digest() -> None:
    # These are declaration inputs to a pure verifier, not runtime execution.
    text = "authored contract document"
    assert research_review_errors({}, {}, {}, text) == []
    assert research_review_errors({}, {"model_review_passed": True}, {}, text)
    flags = {"model_review_required": True, "model_review_passed": True}
    assert research_review_errors(flags, flags, {}, text)
    assert research_review_errors(flags, flags, {"reflection_accepted": True, "reviewed_candidate_sha": "other"}, text)
    assert research_review_errors(flags, flags, {"reflection_accepted": True, "reviewed_candidate_sha": digest(text)}, text) == []
    assert research_review_errors(flags, flags | {"model_review_required": False}, {}, text)


def test_actual_failed_run_stops_at_material_exhaustion_and_preserves_diagnostics() -> None:
    configured = os.environ.get("MARS_TEST_MATERIAL_FAILURE_RUN")
    if not configured:
        pytest.skip("requires an explicit unchanged real research failure run")
    root = Path(configured)
    blocked = 0
    for path in (root/"agent_traces"/"idea_research").glob("*/checkpoint.json"):
        before = path.read_bytes()
        checkpoint = json.loads(before)
        assert checkpoint["status"] != "passed"
        request = json.loads((root/"idea"/"research_delegations"/path.parent.name/"request.json").read_text())
        minimum = request["min_sources"]
        view = LoopStopView("before_model", checkpoint["candidate"], checkpoint["history"], checkpoint["counts"], "act")
        outcome = evidence_stop(view, min_sources=minimum, max_tool_steps=10,
                                tools=("search.fetch_sources",), project="pimc")
        if checkpoint["counts"]["tool_dispatches"] >= 10:
            assert outcome is not None and outcome.status == "evidence_unavailable"
            assert outcome.details["observed_read_publications"] == 0
            blocked += 1
        else:
            assert outcome is None  # A model connection failure does not exhaust reading tools.
        record = failure_record(delegation_id=path.parent.name, trace_ref=str(path.parent.relative_to(root)),
                                checkpoint=checkpoint, min_sources=minimum, max_tool_steps=10,
                                max_model_calls=12, gap=request["arguments"]["gap"], project="pimc")
        assert record["status"] == checkpoint["status"]  # No retrospective relabeling.
        assert record["usable_as_final_evidence"] is False
        assert record["read_sources"] == []
        assert record["remaining_gaps"]
        attempts = actual_attempts(checkpoint["history"])
        failed_rows = [row for a in attempts for row in a.get("source_results", []) if not row.get("ok")]
        assert failed_rows and all(row.get("error") for row in failed_rows)
        assert path.read_bytes() == before
    assert blocked >= 2
    parent_path = next((root/"agent_traces"/"idea").glob("*/checkpoint.json"))
    parent = json.loads(parent_path.read_text())
    assert resumed_delegation_count(root, parent["history"], run_id=root.name,
                                    parent_invocation=str(parent_path.parent)) == 3


def test_actual_pages_allow_report_repair_after_tool_budget_is_spent() -> None:
    configured = os.environ.get("MARS_TEST_MATERIAL_READ_CHECKPOINT")
    if not configured:
        pytest.skip("requires an actual checkpoint with archived PDF reads")
    path = Path(configured)
    before = path.read_bytes()
    checkpoint = json.loads(before)
    observed = material_state(checkpoint["history"])["counts"]["distinct_read_publications"]
    assert observed >= 2
    view = LoopStopView("before_model", checkpoint["candidate"], checkpoint["history"], checkpoint["counts"], "act")
    assert evidence_stop(view, min_sources=2, max_tool_steps=checkpoint["counts"]["tool_dispatches"],
                         tools=("search.fetch_sources",), project="pimc") is None
    assert path.read_bytes() == before


def test_gap_schema_is_standalone_valid() -> None:
    Draft202012Validator.check_schema(gap_schema())
    Draft202012Validator.check_schema(research_submission_schema())


def test_resume_ignores_refusals_that_never_started_a_child(tmp_path: Path) -> None:
    history = [{"tool": "idea.research_delegate", "ok": False, "output": {"available_context_refs": []}}]
    assert resumed_delegation_count(tmp_path, history, run_id="r", parent_invocation="p") == 0


def test_resume_requires_actual_request_and_refuses_unknown_started_child(tmp_path: Path) -> None:
    # Authored local request metadata exercises reconciliation; no child is executed.
    identifier = "a"*32
    args = {"gap": "authored reconciliation input"}
    folder = tmp_path/"idea"/"research_delegations"/identifier
    folder.mkdir(parents=True)
    (folder/"request.json").write_text(json.dumps({"delegation_id": identifier, "arguments": args,
        "parent_run_id": "r", "parent_invocation": "p"}))
    with pytest.raises(ValueError, match="automatic replay forbidden"):
        resumed_delegation_count(tmp_path, [], run_id="r", parent_invocation="p")
    history = [{"tool": "idea.research_delegate", "args": args, "ok": False,
                "output": {"delegation_id": identifier}}]
    assert resumed_delegation_count(tmp_path, history, run_id="r", parent_invocation="p") == 1
    assert resumed_delegation_count(tmp_path, history*2, run_id="r", parent_invocation="p") == 1
    with pytest.raises(ValueError, match="does not match"):
        resumed_delegation_count(tmp_path, [{**history[0], "args": {}}], run_id="r", parent_invocation="p")
    with pytest.raises(ValueError, match="different parent"):
        resumed_delegation_count(tmp_path, history, run_id="r", parent_invocation="other")
