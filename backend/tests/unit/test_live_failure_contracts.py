"""Actual serializer, schema and disk-ledger contracts; no provider/service doubles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError, validate

from app.harness.agent_loop.trace import LoopTrace
from app.harness.agent_loop.protocol import ReviewConflictError, parse_review
from app.agents.idea.research import material_errors
from app.bridge.agent_runner import _write_patch_diff
from app.storage.run_store import RunStore
from app.harness.llm.openai_provider import OpenAIProvider, ZhipuProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.tools.config import tool_config
from app.harness.agent_loop.executor import truncation_recovery


@pytest.mark.parametrize("phase_effort", [None, "low", "high", "max"])
def test_output_truncation_has_bounded_visible_error_repair(phase_effort: str | None) -> None:
    error = {"code": "output_truncated", "empty_final": True, "finish_reason": "length"}
    plan = truncation_recovery(error, repairs=0, limit=2, effort=phase_effort)
    assert plan is not None
    assert plan["reasoning_effort"] == ("low" if phase_effort in {"high", "max"} else phase_effort)
    assert "observations remain valid" in plan["feedback"]
    assert truncation_recovery(error, repairs=2, limit=2, effort=phase_effort) is None
    assert truncation_recovery({"code": "authentication"}, repairs=0, limit=2, effort=phase_effort) is None


def test_failed_attempt_keeps_usage_incomplete_after_success(tmp_path: Path) -> None:
    trace = LoopTrace(tmp_path, "full")
    state = {"status": "running", "counts": {"sdk_attempts": 0, "model_requests": 1},
             "usage": {}, "usage_complete": True, "fingerprint": "ledger-contract", "pending": "model"}
    trace.record_attempt(state, "sdk_attempt_started", {"attempt": 1})
    trace.record_attempt(state, "sdk_attempt_failed", {"attempt": 1, "error": "timeout"})
    trace.record_attempt(state, "sdk_attempt_started", {"attempt": 2})
    trace.record_attempt(state, "sdk_attempt_succeeded", {"attempt": 2})
    saved = json.loads((tmp_path / "facts.json").read_text())
    assert saved["counts"]["sdk_attempts"] == 2
    assert saved["usage_complete"] is False


@pytest.mark.parametrize("tool", ["search.arxiv_search", "search.web_search"])
@pytest.mark.parametrize("args", [{"query": "LUT", "table": ""}, {"query": "LUT", "q": "different"}, {}, {"query": ""}])
def test_search_rejects_ambiguous_args_before_network(tool: str, args: dict[str, str]) -> None:
    schema = tool_config(tool).input_schema
    assert schema is not None
    with pytest.raises(ValidationError):
        validate(args, schema)


def test_zhipu_serializes_explicit_thinking_modes_without_network() -> None:
    provider = ZhipuProvider(api_key="serializer-only-not-a-credential")
    messages = [Message("user", "contract")]
    disabled = provider._request_kwargs(messages, LLMConfig(provider="zhipu", model="glm-5.2", thinking_enabled=False))
    assert disabled["extra_body"] == {"thinking": {"type": "disabled"}}
    forced = provider._request_kwargs(messages, LLMConfig(provider="zhipu", model="glm-5.3", reasoning_effort="low"))
    assert forced["extra_body"] == {"thinking": {"type": "enabled"}}
    assert provider._client is None
    for config in (LLMConfig(provider="zhipu", model="glm-5.3", thinking_enabled=False),
                   LLMConfig(provider="zhipu", model="glm-5.3", reasoning_effort="medium")):
        with pytest.raises(ValueError, match="GLM-5.3"):
            provider._request_kwargs(messages, config)
    generic = OpenAIProvider(api_key="serializer-only-not-a-credential")
    assert "extra_body" not in generic._request_kwargs(messages, LLMConfig(provider="openai", model="configured-model"))


def test_review_conflict_preserves_issues_for_mandatory_candidate_revision() -> None:
    review = {"accept": True, "issues": ["node recurrence can exceed its fixed endpoint"], "rationale": "needs correction"}
    with pytest.raises(ReviewConflictError) as caught:
        parse_review(json.dumps(review))
    assert caught.value.review == review


def test_proposal_cannot_claim_debate_without_executed_debate() -> None:
    errors = material_errors({"debate_summary": {"rounds": 2}}, [], min_sources=0, min_pdfs=0,
                             require_budget=False, max_ratio=1.2)
    assert any("/debate_summary/rounds" in error for error in errors)


def test_patch_export_copies_only_the_actual_proposed_diff(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="patch-export-contract", project="pimc", entrypoint="coding", user_request="export")
    _write_patch_diff(run=run, version="v1", artifact_text="# Specification without a patch")
    assert not (run.subdir("coding") / "patch.v1.diff").exists()
    # A manually authored diff is parser input, not a simulated Coding Agent output.
    diff = "--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-old\n+new\n"
    _write_patch_diff(run=run, version="v2", artifact_text="```diff\n" + diff + "```\n")
    assert (run.subdir("coding") / "patch.v2.diff").read_text() == diff
    assert not (tmp_path / "example.py").exists()


def test_complex_tensor_ledger_cannot_report_only_its_element_count() -> None:
    from app.agents.idea.research import parameter_errors
    # Authored arithmetic inputs reproduce the live count-unit failure; no agent output is supplied.
    raw: dict[str, Any] = {"unit": "real_scalar", "variables": {"N": 17, "C": 18},
           "baseline_formula": "N*N", "baseline_parameters": 289,
           "candidate_formula": "C*C", "candidate_parameters": 324,
           "baseline_components": [{"name": "controls", "formula": "N*N", "dtype": "complex", "shape": ["N", "N"]}],
           "candidate_components": [{"name": "controls", "formula": "C*C", "dtype": "complex", "shape": ["C", "C"]}]}
    assert len(parameter_errors(raw, max_ratio=1.2)) == 2
    for label, dim in (("baseline", "N"), ("candidate", "C")):
        raw[label + "_formula"] = f"2*{dim}*{dim}"
        raw[label + "_components"][0]["formula"] = f"2*{dim}*{dim}"
        raw[label + "_parameters"] *= 2
    assert parameter_errors(raw, max_ratio=1.2) == []


@pytest.mark.parametrize("shape", [[0], [-1], [True], [1.5], [10**1000], ["1/0"], ["unknown"], [2]*9])
def test_invalid_parameter_shapes_fail_closed(shape: list[object]) -> None:
    from app.agents.idea.research import count_component
    with pytest.raises(ValueError):
        count_component({"dtype": "complex", "shape": shape}, {})


def test_real_knot_coordinates_do_not_double_with_complex_output() -> None:
    from app.agents.idea.research import count_component
    values = {"N": 18, "K": 15}
    complex_controls = count_component({"dtype": "complex", "shape": ["N", "N"]}, values)
    real_knots = count_component({"dtype": "real", "shape": [2, "K"]}, values)
    assert complex_controls == 648 and real_knots == 30
    assert (complex_controls + real_knots) / (2 * 17 * 17) < 1.2
