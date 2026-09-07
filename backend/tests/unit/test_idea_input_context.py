"""Real archive round trips and pure payload validation; no substituted services."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.runs import CreateRunPayload
from app.bridge.agent_runner import load_agent_handoff_context
from app.bridge.idea_input_context import validate_idea_extra
from app.bridge.orchestrator import Orchestrator
from app.storage.run_store import RunStore


def test_payload_defaults_preserve_existing_behavior() -> None:
    payload = CreateRunPayload(task="Research", project="pimc")
    assert payload.idea_request_extra() == {}
    assert payload.auto_approve is False


def test_exact_context_survives_real_archive_and_idea_handoff(tmp_path: Path) -> None:
    context = {"background": "  PIMC 背景\r\n", "baseline_code": "def forward(x):\r\n    return x\r\n",
               "data_description": " Inputs and targets\n", "analysis_results": "Observed issue\n",
               "metric_definition": "NMSE definition\n", "literature_notes": "User notes\n"}
    payload = CreateRunPayload.model_validate({"task": "Research", "project": "pimc", "idea_context": context,
        "idea_scope": "project_proposal", "idea_requirements": {"min_sources": 2, "min_pdfs": 1,
        "max_parameter_ratio": 1.2, "require_parameter_budget": True, "require_evaluation_protocol": True}})
    run = RunStore(tmp_path).create(task="Research", project="pimc", entrypoint="idea")
    Orchestrator._persist_request_extra(run, payload.idea_request_extra())
    upstream, feedback = load_agent_handoff_context(run, "idea")
    assert feedback is None
    assert upstream == context
    assert upstream["baseline_code"].encode() == context["baseline_code"].encode()
    saved = json.loads((run.subdir("input") / "run_request_options.v1.json").read_text())
    assert saved["extra"]["scope"] == "project_proposal"
    assert saved["extra"]["idea_requirements"]["require_evaluation_protocol"] is True
    downstream, _ = load_agent_handoff_context(run, "experiment")
    assert not set(context).intersection(downstream)


def test_partial_requirements_do_not_inject_defaults() -> None:
    payload = CreateRunPayload.model_validate({"task": "Research", "project": "pimc",
                                             "idea_requirements": {"min_sources": 3}})
    assert payload.idea_request_extra() == {"idea_requirements": {"min_sources": 3}}


@pytest.mark.parametrize("context", [{"unknown": "text"}, {"background": 42}, {"background": False},
                                     {"background": " \n"}, {"background": None}, [], "text"])
def test_invalid_public_context_rejected(context: Any) -> None:
    with pytest.raises(ValidationError):
        CreateRunPayload.model_validate({"task": "Research", "project": "pimc", "idea_context": context})


@pytest.mark.parametrize("requirements", [{"min_sources": -1}, {"min_sources": 101}, {"min_sources": True},
    {"min_pdfs": "1"}, {"min_pdfs": 101}, {"max_parameter_ratio": 0}, {"max_parameter_ratio": float("nan")},
    {"max_parameter_ratio": float("inf")}, {"max_parameter_ratio": 101}, {"require_parameter_budget": "true"},
    {"require_evaluation_protocol": 1}, {"unknown": True}])
def test_invalid_public_requirements_rejected(requirements: Any) -> None:
    with pytest.raises(ValidationError):
        CreateRunPayload.model_validate({"task": "Research", "project": "pimc", "idea_requirements": requirements})


@pytest.mark.parametrize("extra", [{"idea_context": None}, {"idea_context": {"baseline_code": 3}},
                                   {"scope": "unsupported"}, {"idea_requirements": {"min_sources": "2"}},
                                   {"idea_requirements": {"min_sources": None}},
                                   {"idea_requirements": {"max_parameter_ratio": None}}])
def test_internal_options_are_not_stringified(extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_idea_extra(extra)


@pytest.mark.parametrize("contents", ["not-json", "[]", '{"schema_id":"wrong","extra":{}}',
    '{"schema_id":"run_request_options.v1","extra":[]}',
    '{"schema_id":"run_request_options.v1","extra":{"idea_context":{"background":8}}}'])
def test_corrupt_idea_archives_fail_explicitly(tmp_path: Path, contents: str) -> None:
    run = RunStore(tmp_path).create(task="Research", project="pimc", entrypoint="idea")
    (run.subdir("input") / "run_request_options.v1.json").write_text(contents)
    with pytest.raises(ValueError):
        load_agent_handoff_context(run, "idea")


def test_legacy_run_without_options_remains_supported(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="Research", project="pimc", entrypoint="idea")
    assert load_agent_handoff_context(run, "idea") == ({}, None)
