"""Pure authored structures and read-only request-4 replay; no simulated execution."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest
from jsonschema import Draft202012Validator

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.delivery import STRUCTURED_REFERENCE_GUIDANCE, delivery_errors, resolve_pointer
from app.agents.idea.protocol import protocol_errors
from app.harness.agent_loop.protocol import parse_action
from app.harness.agent_loop.trace import digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.schema.frontmatter_parser import parse


@pytest.mark.parametrize("pointer,expected", [
    ("/method_spec/section/value", 1),
    ("/method_spec/items/0", "authored item"),
    ("/method_spec/a~1b/~0key", "authored escaped key"),
])
def test_authored_nested_fields_arrays_and_escaped_keys_resolve(pointer: str, expected: Any) -> None:
    metadata = {"method_spec": {"section": {"value": 1}, "items": ["authored item"],
                                 "a/b": {"~key": "authored escaped key"}}}
    original = deepcopy(metadata)
    assert resolve_pointer(metadata, pointer) == expected
    assert metadata == original


def test_literal_full_path_key_gets_feedback_without_renaming_or_relaxing_resolution() -> None:
    metadata = {"method_spec": {"/method_spec/section": {"value": 1}}}
    original = deepcopy(metadata)
    with pytest.raises(ValueError, match="full path is a literal object key"):
        resolve_pointer(metadata, "/method_spec/section")
    # A literal slash-containing key remains addressable with explicit escaping.
    assert resolve_pointer(metadata, "/method_spec/~1method_spec~1section/value") == 1
    assert metadata == original


@pytest.mark.parametrize("pointer", ["/method_spec/missing", "/method_spec/items/01",
                                     "/method_spec/items/-1", "/method_spec/~2", "/method_spec/empty"])
def test_other_invalid_or_empty_references_still_fail(pointer: str) -> None:
    with pytest.raises(ValueError):
        resolve_pointer({"method_spec": {"items": [1], "empty": {}}}, pointer)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["native_tools", "json_actions"])
async def test_author_prompt_and_complete_schema_include_structure_only_guidance(protocol: str) -> None:
    config = get_agent_config("idea")
    agent = IdeaAgent(agent_config=replace(config, raw={
        **config.raw, "loop": {**config.raw["loop"], "protocol": protocol}}))
    request = RunRequest("pimc", "Human-authored structure contract input", extra={
        "context_sources": {"project_rules": False, "code_repositories": False},
        "idea_requirements": {"require_parameter_budget": True}})
    context = await agent.build_context(request)
    assert context.task.count(STRUCTURED_REFERENCE_GUIDANCE) == 1
    schema = agent.submission_schema(request)
    assert schema is not None
    Draft202012Validator.check_schema(schema)
    for field in ("method_spec", "decision_rule"):
        definition = schema["properties"][field]
        assert STRUCTURED_REFERENCE_GUIDANCE in definition["description"]
        assert {key: value for key, value in definition.items() if key != "description"} == {
            "type": "object", "minProperties": 1}
    # Guidance adds neither a canned method nor an automatic schema default.
    assert "default" not in schema["properties"]["method_spec"]
    assert schema["properties"]["alternatives"]["minItems"] == 2
    assert schema["properties"]["ablation_plan"]["minItems"] == 3


@pytest.fixture
def request4_events() -> Iterator[list[dict[str, Any]]]:
    configured = os.environ.get("MARS_TEST_IDEA_POINTER_EVENTS")
    if not configured:
        pytest.skip("requires the actual API run-17 lead events.jsonl")
    path = Path(configured)
    # Freeze the immutable event prefix, not the live checkpoint or appended requests.
    prefix = b""
    events: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for line in handle:
            event = json.loads(line)
            prefix += line
            events.append(event)
            if event.get("event_seq") == 28:
                break
    assert hashlib.sha256(prefix).hexdigest() == "5fa11418e05114a827bb118271a64e326e1fd74c5d2d9a8056e4b626e03847a2"
    yield events
    with path.open("rb") as handle:
        assert handle.read(len(prefix)) == prefix


def test_actual_request4_received_complete_schema_once(request4_events: list[dict[str, Any]]) -> None:
    request = next(event for event in request4_events
                   if event["kind"] == "model_request" and event["request"] == 4)
    assert digest(request["visible"]) == request["visible_sha256"]
    marker = "Complete final.metadata JSON Schema:\n"
    schema_messages = [message["content"] for message in request["visible"] if marker in message["content"]]
    assert len(schema_messages) == 1 and schema_messages[0].count(marker) == 1
    schema = json.loads(schema_messages[0].split(marker, 1)[1])
    assert {"method_spec", "decision_rule", "evaluation_protocol", "research_links"} <= set(schema["required"])
    assert schema["properties"]["method_spec"]["type"] == "object"


def test_actual_request4_bad_keys_remain_rejected_without_editing_candidate(
    request4_events: list[dict[str, Any]],
) -> None:
    response = next(event for event in request4_events
                    if event["kind"] == "model_response" and event["request"] == 4)
    assert digest(response["visible"]) == response["visible_sha256"]
    assert response["visible_sha256"] == "977d7cc085c471dccef882c8ca1abe39d19d7ec7ab281640bf1c2372f5ca9d23"
    document = parse(parse_action(response["visible"])["final"])
    metadata = document.metadata
    original = deepcopy(metadata)
    assert metadata == json.loads(response["visible"])["final"]["metadata"]
    assert all(key.startswith("/method_spec/") for key in metadata["method_spec"])
    for pointer in metadata["method_spec"]:
        with pytest.raises(ValueError, match="full path is a literal object key"):
            resolve_pointer(metadata, pointer)
    errors = delivery_errors(metadata, "method_proposal", body=document.body)
    assert any("full path is a literal object key" in error for error in errors)
    assert any("unresolved or empty reference /method_spec/" in error for error in protocol_errors(metadata))
    archived_validation = next(event for event in request4_events if event["event_seq"] == 28)
    assert archived_validation["kind"] == "validation" and archived_validation["valid"] is False
    assert metadata == original
