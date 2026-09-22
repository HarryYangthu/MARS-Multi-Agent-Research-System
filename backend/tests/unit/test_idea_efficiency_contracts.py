"""Parser, arithmetic, real-file and PDF-window regressions; no service substitutes."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.agents.idea.performance_contract import performance_errors
from app.bridge.idea_input_context import IdeaRequirements
from app.harness.agent_loop.context import pack_context, reading_coverage_index, source_receipt_index
from app.harness.agent_loop.document_revision import apply_document_revision, REVISE_DOCUMENT
from app.harness.agent_loop.executor import missing_review_evidence
from app.harness.agent_loop.native_protocol import native_decision, native_specs
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import parse_action
from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import Completion, Message, ToolCall
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.code import repo_reader_tool
from app.harness.tools.registry import ToolContext
from app.harness.tools.search.source_fetch import extract_pdf


def candidate() -> str:
    return str(parse_action(json.dumps({"final": {"metadata": {
        "schema": "proposal.v1", "method_spec": {"formula": "original", "a/b~c": [1, 2]},
        "decision_rule": {"max_degradation": 0}, "human_summary": "Original parser input."},
        "body": "Original parser input."}}))["final"])


def test_direct_metadata_submission_copies_the_authored_summary_without_inventing_body() -> None:
    from app.harness.agent_loop.native_protocol import SUBMIT_DOCUMENT
    metadata = parse(candidate()).metadata
    spec = native_specs([], {"type": "object", "required": ["human_summary"]}, body_field="human_summary")[0]
    assert spec["function"]["parameters"]["required"] == ["human_summary"]
    completion = Completion("", "parser", "parser", tool_calls=(
        ToolCall("parser-input", SUBMIT_DOCUMENT, json.dumps(metadata)),))
    result = native_decision(completion, (), structured_final=True, body_field="human_summary")
    assert parse(result["final"]).metadata == metadata
    assert parse(result["final"]).body == metadata["human_summary"]
    for value in [{"metadata": metadata, "body": "redundant"}, {**metadata, "human_summary": ""}]:
        bad = Completion("", "parser", "parser", tool_calls=(ToolCall("parser-input", SUBMIT_DOCUMENT, json.dumps(value)),))
        with pytest.raises(ValueError, match="directly"):
            native_decision(bad, (), structured_final=True, body_field="human_summary")
    assert "submission_body_field" not in AgentLoopPolicy().fingerprint_data()
    with pytest.raises(ValueError, match="submission_body_field"):
        AgentLoopPolicy(submission_body_field="human_summary")


def test_revision_preserves_untouched_data_and_serializes_exact_model_edits() -> None:
    original = candidate()
    edits = {"base_sha256": digest(original), "operations": [
        {"op": "set", "path": "/metadata/method_spec/formula", "value": "f(x): x^2"},
        {"op": "set", "path": "/metadata/method_spec/a~1b~0c/1", "value": 3},
        {"op": "remove", "path": "/metadata/method_spec/a~1b~0c/0"},
        {"op": "set", "path": "/metadata/method_spec/evidence", "value": "exact source excerpt"},
    ]}
    call = ToolCall("parser-input", REVISE_DOCUMENT, json.dumps(edits))
    result = native_decision(Completion("", "parser", "parser", tool_calls=(call,)), (),
                             structured_final=True, allow_revisions=True, candidate=original)
    revised = parse(result["final"])
    assert revised.metadata["method_spec"] == {"formula": "f(x): x^2", "a/b~c": [3], "evidence": "exact source excerpt"}
    assert revised.metadata["decision_rule"] == parse(original).metadata["decision_rule"]
    assert revised.body == parse(original).body
    assert result["revision"] == edits
    assert "tool" not in result


@pytest.mark.parametrize("operation", [
    {"op": "set", "path": "/metadata/missing/child", "value": 1},
    {"op": "set", "path": "/metadata/method_spec/a~1b~0c/-1", "value": 1},
    {"op": "set", "path": "/metadata/method_spec/a~1b~0c/02", "value": 1},
    {"op": "set", "path": "/metadata/method_spec/a~1b~0c/2", "value": 1},
    {"op": "set", "path": "/metadata/invalid~2key", "value": 1},
    {"op": "remove", "path": "/metadata/absent"},
    {"op": "remove", "path": "/body"},
    {"op": "set", "path": "/metadata", "value": {}},
    {"op": "set", "path": "/metadata/value", "value": float("nan")},
])
def test_invalid_revision_is_atomic(operation: dict[str, Any]) -> None:
    original = candidate()
    edits = {"base_sha256": digest(original), "operations": [
        {"op": "set", "path": "/metadata/method_spec/formula", "value": "must not commit"}, operation]}
    saved = deepcopy(edits)
    with pytest.raises(ValueError):
        apply_document_revision(original, edits)
    assert edits == saved
    assert original == candidate()


def test_revision_cannot_apply_to_another_candidate_or_bypass_opt_in() -> None:
    original = candidate()
    edits = {"base_sha256": "0" * 64, "operations": [{"op": "set", "path": "/body", "value": "changed"}]}
    with pytest.raises(ValueError, match="stale"):
        apply_document_revision(original, edits)
    call = ToolCall("parser-input", REVISE_DOCUMENT, json.dumps(edits))
    with pytest.raises(ValueError, match="unknown"):
        native_decision(Completion("", "parser", "parser", tool_calls=(call,)), (), structured_final=True)
    with pytest.raises(ValueError, match="alone"):
        native_decision(Completion("", "parser", "parser", tool_calls=(call, call)), (),
                        structured_final=True, allow_revisions=True, candidate=original)
    assert REVISE_DOCUMENT not in [s["function"]["name"] for s in native_specs([], {"type": "object"})]
    with pytest.raises(ValueError, match="requires native_tools"):
        AgentLoopPolicy(document_revisions_enabled=True)


@pytest.mark.asyncio
async def test_review_keeps_complete_real_code_once_and_preserves_receipts(tmp_path: Path) -> None:
    content = "# a real local source file, not a provider response\n" * 100 + "def final_definition(): return 42\n"
    (tmp_path / "libs").mkdir()
    (tmp_path / "libs/source.py").write_text(content)
    args = {"path": "libs/source.py", "max_chars": 20000}
    result = await repo_reader_tool(args, ToolContext("file-read", "pimc", "idea", project_repo_root=str(tmp_path)))
    assert result.ok and result.output["content"] == content
    history = [{"tool": "code.repo_reader", "args": args, "ok": result.ok, "output": result.output}]
    original = deepcopy(history)
    pinned = [Message("user", "The exact supplied baseline follows:\n" + content)]
    messages, manifest = pack_context(pinned, history, "", "authored candidate", budget=12000,
        observation_chars=512, reviewing=True, required_review_tools=("code.repo_reader",), deduplicate_evidence=True)
    assert sum(content in m.content for m in messages) == 1
    assert "duplicate_of_included_text_sha256" in "\n".join(m.content for m in messages)
    assert manifest["deduplicated_review_history"] == [0]
    assert not missing_review_evidence(history, manifest, ("code.repo_reader",), observation_chars=512)
    assert history == original
    # Without an included copy, the whole required source must remain visible.
    messages, manifest = pack_context([Message("system", "task")], history, "", "candidate", budget=15000,
        observation_chars=512, reviewing=True, required_review_tools=("code.repo_reader",), deduplicate_evidence=True)
    assert any(json.dumps(content, ensure_ascii=False) in m.content for m in messages)
    assert manifest["deduplicated_review_history"] == []


def test_actual_pdf_offsets_merge_without_claiming_a_tail_is_a_whole_page(tmp_path: Path) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 700 Td (Complete method steps and assumptions.) Tj ET")
    page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    path = tmp_path / "actual.pdf"
    path.write_bytes(output.getvalue())
    # Real extractor results supplied to pure bookkeeping functions, not a
    # simulated network response or claimed model/tool execution.
    def reading(start: int, length: int) -> dict[str, Any]:
        extracted = extract_pdf(path.read_bytes(), start_page=1, max_pages=1, max_chars=length, char_offset=start)
        return {"ok": True, "output": {"sources": [{**extracted, "ok": True, "archive_complete": True,
            "source_id": "local-pdf-parser-input", "sha256": "a" * 64, "read_receipt": str(path),
            "url": "https://example.org/parser-input", "title": "Local PDF parser input"}]}}
    tail = reading(10, 1000)
    assert source_receipt_index([tail])[0]["page_text_visibility"][0]["text_window"] == "partial"
    assert reading_coverage_index([tail])[0]["pages"][0]["missing_intervals"] == [[0, 10]]
    complete = reading_coverage_index([reading(0, 10), tail])[0]["pages"][0]
    assert complete["complete_extracted_text"] and complete["missing_intervals"] == []


def test_performance_gate_rejects_relaxation_historical_baseline_and_test_selection() -> None:
    gate = {"metric": "RES", "direction": "minimize", "max_degradation": 0.0, "unit": "dB", "baseline": "matched_run"}
    requirements = IdeaRequirements(performance_requirement=gate).model_dump(exclude_none=True)
    rule = {**gate, "selection_split": "validation", "report_split": "held_out_test",
            "status": "pending_experiment", "acceptance_expression": "candidate_RES <= baseline_RES"}
    assert not performance_errors({"decision_rule": {"performance": rule}}, requirements)
    for key, value in [("max_degradation", 0.3), ("baseline", "historical_3.890"),
                       ("selection_split", "held_out_test"), ("status", "passed")]:
        assert performance_errors({"decision_rule": {"performance": {**rule, key: value}}}, requirements)
    with pytest.raises(ValidationError):
        IdeaRequirements(performance_requirement={**gate, "max_degradation": float("inf")})
    final_gate = IdeaRequirements(performance_requirement={**gate, "acceptance_split": "held_out_test"}).model_dump(exclude_none=True)
    assert performance_errors({"decision_rule": {"performance": rule}}, final_gate)
    assert not performance_errors({"decision_rule": {"performance": {**rule, "acceptance_split": "held_out_test"}}}, final_gate)
