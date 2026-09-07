"""Pure contracts and actual filesystem checks. No model/service/transport doubles."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.idea.research import canonical_source, evaluate_formula, parameter_errors
from app.harness.agent_loop.context import compact, pack_context, token_upper_bound
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import parse_action, parse_review
from app.harness.agent_loop.trace import LoopTrace, atomic_json
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Message
from app.settings import Settings


@pytest.mark.parametrize("text", ['{}', '[]', '{"tool":"x","args":{}}',
                                      '{"tool":"x","args":[],"reason":"query"}',
                                      '{"final":"x","tool":"x"}', '{"final":""}',
                                      'prose before {"final":"x"}', '{"tool_calls":[]}'])
def test_protocol_rejects_ambiguous_actions(text: str) -> None:
    with pytest.raises(ValueError):
        parse_action(text)


def test_flat_protocol_preserves_arguments() -> None:
    action = {"tool": "search.arxiv_search", "args": {"q": "spline LUT"}, "reason": "find interpolation methods"}
    assert parse_action(json.dumps(action)) == action


@pytest.mark.parametrize("text", [
    '{"accept":"yes","issues":[],"rationale":"ok"}',
    '{"accept":true,"issues":["unresolved"],"rationale":"ok"}',
    '{"accept":false,"issues":[],"rationale":"no"}',
])
def test_review_rejects_contradictory_decisions(text: str) -> None:
    with pytest.raises(ValueError):
        parse_review(text)


@pytest.mark.parametrize("formula", ["__import__('os')", "x.__class__", "[x for x in []]",
                                          "2**100000", "1/0", "missing+1", "True", "1e309"])
def test_arithmetic_refuses_code_or_unbounded_work(formula: str) -> None:
    with pytest.raises((ValueError, SyntaxError)):
        evaluate_formula(formula, {"x": 8})


def test_parameter_arithmetic_and_component_conservation() -> None:
    raw = {
        "unit": "real_scalar", "variables": {"N": 16, "M": 16, "r": 2},
        "baseline_formula": "2*N*M", "candidate_formula": "2*N*M+2*r*(N+M)",
        "baseline_parameters": 512, "candidate_parameters": 640,
        "baseline_components": [{"name": "complex coefficients", "formula": "2*N*M"}],
        "candidate_components": [{"name": "coefficients", "formula": "2*N*M"},
                                 {"name": "factors", "formula": "2*r*(N+M)"}],
    }
    assert not parameter_errors(raw, max_ratio=1.25)
    assert parameter_errors(raw, max_ratio=1.2)
    raw["candidate_parameters"] = 600
    assert parameter_errors(raw, max_ratio=2)


def test_structural_compaction_preserves_valid_json_and_error() -> None:
    data = {"long_text": "甲" * 5000, "error": "real timeout", "url": "https://arxiv.org/abs/1"}
    packed = compact(data, 512)
    assert json.loads(json.dumps(packed)) == packed
    assert packed["long_text"]["truncated"]
    assert packed["error"] == data["error"]
    assert packed["url"] == data["url"]


def test_context_never_silently_truncates_pinned_task() -> None:
    with pytest.raises(ValueError, match="pinned"):
        pack_context([Message("user", "甲" * 3000)], [], "", "", budget=4000, observation_chars=512)


def test_context_estimate_is_bounded() -> None:
    messages, manifest = pack_context([Message("system", "rules"), Message("user", "task")],
                                     [], "", "", budget=4000, observation_chars=512)
    assert manifest["estimated_upper_bound_tokens"] == token_upper_bound(messages) <= 4000


def test_real_disk_atomic_write_and_event_sequence(tmp_path: Path) -> None:
    atomic_json(tmp_path / "value.json", {"value": 1})
    atomic_json(tmp_path / "value.json", {"value": 2})
    assert json.loads((tmp_path / "value.json").read_text()) == {"value": 2}
    trace = LoopTrace(tmp_path / "trace", "full")
    trace.emit("contract_check", {"ok": True})
    trace.emit("contract_check", {"ok": False})
    rows = [json.loads(x) for x in trace.events.read_text().splitlines()]
    assert [r["event_seq"] for r in rows] == [1, 2]
    assert LoopTrace(trace.root, "full", resume=True).seq == 2
    with pytest.raises(ValueError, match="already exists"):
        LoopTrace(trace.root, "full")


@pytest.mark.parametrize("mode", ["always", "auto"])
def test_old_simulation_configuration_is_rejected(mode: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, mars_mock_mode=mode)


def test_policy_and_source_identity() -> None:
    assert AgentLoopPolicy().mode == "react"
    with pytest.raises(ValueError):
        AgentLoopPolicy.from_mapping({"max_model_calls": True})
    with pytest.raises(ValueError):
        AgentLoopPolicy.from_mapping({"unknown_setting": 1})
    assert canonical_source("https://arxiv.org/abs/1907.02350v4") == canonical_source("https://arxiv.org/pdf/1907.02350.pdf")


def test_missing_explicit_provider_never_returns_a_sample() -> None:
    from dataclasses import replace
    config = replace(get_agent_config("idea"), model_provider="unconfigured", api_key_env="", base_url="")
    with pytest.raises(RuntimeError, match="not configured"):
        select_provider(config)
