"""Strict native argument parsing and opt-in replay of unmodified real failures."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.agent_loop.native_protocol import SUBMIT_DOCUMENT, native_decision
from app.harness.agent_loop.protocol import DuplicateJSONKeyError
from app.harness.llm.provider_base import Completion, ToolCall


def _parse_arguments(arguments: str) -> dict[str, Any]:
    """Authored parser inputs only; no provider or tool is executed."""
    call = ToolCall("parser-input", SUBMIT_DOCUMENT, arguments)
    return native_decision(Completion("", "parser", "parser", tool_calls=(call,)), (), structured_final=True)


def test_json_error_points_into_original_multiline_arguments() -> None:
    arguments = '{\n "metadata": {"x": 1},\n "body": "text"\n},\n "body": "extra"}'
    with pytest.raises(ValueError, match=r"^JSON Extra data at line 4, column 2$") as caught:
        _parse_arguments(arguments)
    cause = caught.value.__cause__
    assert isinstance(cause, json.JSONDecodeError)
    assert cause.doc == arguments
    assert arguments[cause.pos] == ","


@pytest.mark.parametrize("arguments", [
    '"legacy document text"', "null", "[]", "1", "true",
    '{}', '{"metadata":{"x":1}}', '{"body":"text"}',
    '{"metadata":{"x":1},"body":"text","decision_rule":{}}',
    '{"final":{"metadata":{"x":1},"body":"text"}}',
])
def test_native_document_requires_exact_root_fields(arguments: str) -> None:
    with pytest.raises(ValueError, match="^document arguments require exactly metadata and body$"):
        _parse_arguments(arguments)


@pytest.mark.parametrize("arguments, error", [
    ('{"metadata":{},"body":"text"}', "final.metadata"),
    ('{"metadata":[],"body":"text"}', "final.metadata"),
    ('{"metadata":{"x":1},"body":"  "}', "final.body"),
    ('{"metadata":{"x":1},"body":[]}', "final.body"),
])
def test_parsed_document_still_uses_existing_final_contract(arguments: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        _parse_arguments(arguments)


@pytest.mark.parametrize("arguments, key", [
    ('{"metadata":{"x":1},"body":"text","body":"text"}', "body"),
    ('{"metadata":{"insights":[{"page":3,"page":3}]},"body":"text"}', "page"),
])
def test_duplicate_keys_are_rejected_even_when_values_match(arguments: str, key: str) -> None:
    with pytest.raises(DuplicateJSONKeyError, match=f"^duplicate JSON key: {key}$"):
        _parse_arguments(arguments)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_numbers_are_rejected_before_serialization(number: str) -> None:
    arguments = '{"metadata":{"value":' + number + '},"body":"text"}'
    with pytest.raises(ValueError, match="non-finite|finite float"):
        _parse_arguments(arguments)


def _archived_call(relative_path: str, event_seq: int, sha256: str) -> tuple[Path, bytes, ToolCall]:
    configured = os.environ.get("MARS_TEST_NATIVE_FAILURE_ARCHIVE_ROOT")
    if not configured:
        pytest.skip("requires the actual run 11/12 archive root; no substitute records are generated")
    path = Path(configured) / relative_path
    original = path.read_bytes()
    events = [json.loads(line) for line in original.decode().splitlines()]
    event = next(item for item in events if item["event_seq"] == event_seq)
    assert event["kind"] == "model_response"
    calls = event["visible"]["tool_calls"]
    assert len(calls) == 1 and calls[0]["function"]["name"] == SUBMIT_DOCUMENT
    raw = calls[0]
    arguments = raw["function"]["arguments"]
    assert hashlib.sha256(arguments.encode()).hexdigest() == sha256
    return path, original, ToolCall(raw["id"], raw["function"]["name"], arguments)


@pytest.mark.parametrize("event_seq, sha256, column", [
    (29, "ee472988bcf082bb7ddcab1f0f6aba37e473c39e08ae2a098d9afc0b7c7440ee", 18668),
    (35, "b8e01c83566acbfa7c2493518701931568fb7e6d4a8561faf56eec6560d47d1b", 18069),
    (41, "437f18787e1729346ce9cdb40829be6f5db3331204892590a7643a8193abae10", 19035),
    (47, "797d2a4dfd24d17f20e8d333a361ccfb5d5cd2ff49d7b13218754066eedce48b", 19132),
    (53, "2d851f1bb330ae84678425cfba32d30aeb75bfb2e7347fef09dc9d7333019ee3", 19129),
])
def test_real_run11_malformed_submissions_are_rejected_at_original_position(
    event_seq: int, sha256: str, column: int,
) -> None:
    path, original, call = _archived_call(
        "idea_research_20260908T162510_d56c0a/agent_traces/idea/01be88073c5445aea5afc57070c3b4d0/events.jsonl",
        event_seq, sha256,
    )
    with pytest.raises(ValueError, match=f"^JSON Extra data at line 1, column {column}$") as caught:
        native_decision(Completion("", "archive", "archive", tool_calls=(call,)), (), structured_final=True)
    cause = caught.value.__cause__
    assert isinstance(cause, json.JSONDecodeError) and cause.doc == call.arguments
    assert path.read_bytes() == original


def test_real_run12_identical_duplicate_pages_remain_invalid() -> None:
    path, original, call = _archived_call(
        "idea_research_20260908T163806_d3e93e/agent_traces/idea_research/f2d58c4255e1495798158fc744e9f3d9/events.jsonl",
        38, "75a97f86e4e63e7703cda4b47ebb0f2bdf89585fd6800261987dcc5ebfa0842f",
    )
    with pytest.raises(DuplicateJSONKeyError, match="^duplicate JSON key: page$"):
        native_decision(Completion("", "archive", "archive", tool_calls=(call,)), (), structured_final=True)
    assert path.read_bytes() == original
