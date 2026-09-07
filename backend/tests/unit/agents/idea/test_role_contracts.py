"""Authored JSON contract examples, not model answers or simulated role execution."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.idea.discovery.backend import (
    DiscoveryProtocolError, _role_prompt, _task_input,
    _validate_reflection_ids, _validate_role_response,
)
from app.agents.idea.discovery.contracts import role_schema
from app.agents.idea.discovery.models import (
    DiscoveryContext, HypothesisDraft, MetaReviewDraft, PairwiseDecision, ReflectionDraft,
)


AUTHORED_HYPOTHESIS: dict[str, Any] = {
    "mechanism": "Human-authored record for checking JSON field requirements.",
    "statement": "This record is contract data and makes no scientific claim.",
    "testable_predictions": ["A manually authored nonempty contract item."],
    "evidence_refs": [], "constraints": [], "uncertainty": "No research has been performed.",
}
AUTHORED_REFLECTION: dict[str, Any] = {
    "hypothesis_id": "authored-a", "correctness": "Not scientifically reviewed.",
    "novelty": "Not established.", "falsifiability": "Not evaluated by a model.",
    "assumptions": [], "failure_modes": [], "evidence_refs": [], "blockers": [],
}


@pytest.mark.parametrize("role,container,record_type,extra", [
    ("generation", "hypotheses", HypothesisDraft, set()),
    ("evolution", "children", HypothesisDraft, set()),
    ("reflection", "reflections", ReflectionDraft, {"hypothesis_id"}),
    ("pairwise_judge", "decisions", PairwiseDecision, set()),
    ("meta_review", None, MetaReviewDraft, set()),
])
def test_required_schema_fields_cover_each_typed_record(
    role: str, container: str | None, record_type: Any, extra: set[str],
) -> None:
    schema = role_schema(role)
    Draft202012Validator.check_schema(schema)
    record = schema["properties"][container]["items"] if container else schema
    assert set(record["required"]) == {field.name for field in fields(record_type)} | extra
    assert set(record["properties"]) == set(record["required"])
    assert record["additionalProperties"] is False
    context = DiscoveryContext(run_id="contract", project="synthetic_regression",
        research_question="Unmodified caller task", evidence_refs=("task://contract",),
        constraints=("Unmodified caller constraint",), context_hash="authored-context")
    prompt = _role_prompt(_task_input(context), schema=schema)
    shown = json.loads(prompt.split("\n", 1)[1])
    assert shown["response_schema"] == schema
    assert shown["task"] == context.research_question
    assert shown["constraints"] == list(context.constraints)
    assert shown["evidence_refs"] == list(context.evidence_refs)
    assert shown["project"] == context.project


def test_missing_statement_is_rejected_without_alias_substitution() -> None:
    record = deepcopy(AUTHORED_HYPOTHESIS)
    record["title"] = record.pop("statement")
    payload = {"hypotheses": [record]}
    original = deepcopy(payload)
    with pytest.raises(DiscoveryProtocolError, match="statement"):
        _validate_role_response(json.dumps(payload), role="generation", schema=role_schema("generation"))
    assert payload == original and "statement" not in record


@pytest.mark.parametrize("role,payload", [
    ("generation", {"hypotheses": [AUTHORED_HYPOTHESIS]}),
    ("evolution", {"children": [AUTHORED_HYPOTHESIS]}),
    ("reflection", {"reflections": [AUTHORED_REFLECTION]}),
    ("pairwise_judge", {"decisions": [{"outcome": "draw", "reason": "No preference claimed.", "evidence_refs": []}]}),
    ("meta_review", {"recurring_errors": [], "successful_patterns": [], "evidence_gaps": [],
                     "unexplored_regions": [], "next_round_guidance": []}),
])
def test_complete_authored_records_pass_without_rewriting(role: str, payload: dict[str, Any]) -> None:
    assert _validate_role_response(json.dumps(payload), role=role, schema=role_schema(role)) == payload


@pytest.mark.parametrize("field", ["correctness", "novelty", "falsifiability"])
@pytest.mark.parametrize("value", [None, "", " \n\t"])
def test_reflection_requires_present_nonblank_judgments(field: str, value: str | None) -> None:
    record = deepcopy(AUTHORED_REFLECTION)
    if value is None:
        del record[field]
    else:
        record[field] = value
    with pytest.raises(DiscoveryProtocolError, match=field):
        _validate_role_response(json.dumps({"reflections": [record]}), role="reflection",
                                schema=role_schema("reflection"))


@pytest.mark.parametrize("rows,message", [
    ([{"hypothesis_id": "unknown"}], "unknown hypothesis_id"),
    ([{"hypothesis_id": "a"}, {"hypothesis_id": "a"}], "duplicate hypothesis_id"),
    ([{"hypothesis_id": "a"}], "omitted hypotheses"),
])
def test_reflection_id_errors_are_explicit_before_dictionary_insertion(
    rows: list[dict[str, str]], message: str,
) -> None:
    with pytest.raises(DiscoveryProtocolError, match=message):
        _validate_reflection_ids(rows, expected=("a", "b"))
    _validate_reflection_ids([{"hypothesis_id": "b"}, {"hypothesis_id": "a"}], expected=("a", "b"))


def test_pairwise_reason_and_hypothesis_lists_are_not_silently_coerced() -> None:
    with pytest.raises(DiscoveryProtocolError, match="reason"):
        _validate_role_response('{"decisions":[{"outcome":"draw","reason":" ","evidence_refs":[]}]}',
                                role="pairwise_judge", schema=role_schema("pairwise_judge"))
    record = deepcopy(AUTHORED_HYPOTHESIS)
    record["testable_predictions"] = "wrong type; not an array"
    with pytest.raises(DiscoveryProtocolError, match="testable_predictions"):
        _validate_role_response(json.dumps({"hypotheses": [record]}), role="generation",
                                schema=role_schema("generation"))


def test_contract_instances_do_not_share_mutable_schema_state() -> None:
    schema = role_schema("generation")
    schema["properties"]["hypotheses"]["items"]["required"].remove("statement")
    assert "statement" in role_schema("generation")["properties"]["hypotheses"]["items"]["required"]
