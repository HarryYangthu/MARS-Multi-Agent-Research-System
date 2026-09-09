"""Raw syntax-repair context contracts; no successful model response is substituted."""
import json
import os
from pathlib import Path

import pytest

from app.harness.agent_loop.context import pack_context, token_upper_bound
from app.harness.agent_loop.protocol import invalid_output_context, parse_action
from app.harness.llm.provider_base import Message


def test_unparsed_output_is_preserved_as_data_with_candidate_and_review() -> None:
    raw = '{"final":{"metadata":{"authored_text":"quoted\\value"},"body":"unterminated'
    message = invalid_output_context(raw)
    assert message.role == "user"
    assert json.loads(message.content.split("\n", 1)[1])["invalid_output"] == raw
    with pytest.raises(ValueError):
        parse_action(raw)
    with pytest.raises(ValueError):
        parse_action(message.content)
    candidate = "Human-authored previous candidate for a context serialization test"
    messages, manifest = pack_context([Message("system", "Schema contract"), message], [],
                                      "Keep the unresolved boundary issue", candidate,
                                      budget=5000, observation_chars=1000)
    assert messages[1] == message
    assert messages[-2].role == "user"
    assert json.loads(messages[-2].content.split("\n", 1)[1])["candidate"] == candidate
    assert "unresolved boundary issue" in messages[-1].content
    assert not manifest["omitted_history"]


def test_oversized_invalid_output_is_not_silently_dropped() -> None:
    message = invalid_output_context("Authored raw text" * 500)
    with pytest.raises(ValueError, match="nothing was silently dropped"):
        pack_context([message], [], "Fix JSON syntax", "", budget=1000, observation_chars=500)


def test_native_repair_preserves_invalid_arguments_without_replaying_long_commentary() -> None:
    # Authored serialization input, not an executed tool or provider substitute.
    arguments = '{"metadata":{}} trailing invalid data'
    calls = [{"id": "authored-invalid-call", "type": "function",
              "function": {"name": "mars_submit_document", "arguments": arguments}}]
    raw = json.dumps({"text": "Authored commentary " * 1000, "tool_calls": calls})
    message = invalid_output_context(raw, native=True)
    payload = json.loads(message.content.split("\n", 1)[1])
    assert payload["invalid_native_output"]["text"]["truncated"] is True
    assert payload["invalid_native_output"]["tool_calls"] == calls
    with pytest.raises(ValueError):
        json.loads(payload["invalid_native_output"]["tool_calls"][0]["function"]["arguments"])
    messages, manifest = pack_context([message], [], "Fix JSON syntax", "current complete candidate",
                                      native=True, budget=3000, observation_chars=500,
                                      review_issues=["unresolved method issue"],
                                      validation_issues=["/parameters: exceeds budget"])
    assert manifest["validation_issues_visible"] and manifest["prior_review_issues_visible"]
    assert any(m.content.endswith("current complete candidate") for m in messages)


@pytest.mark.parametrize("native", [False, True])
def test_review_or_proposal_text_without_calls_is_kept_complete(native: bool) -> None:
    raw = json.dumps({"text": "Authored review issue " * 1000, "tool_calls": []})
    message = invalid_output_context(raw, native=native)
    assert json.loads(message.content.split("\n", 1)[1])["invalid_output"] == raw


def test_actual_native_protocol_failure_fits_repair_context_without_model_call() -> None:
    raw_path = os.environ.get("MARS_TEST_PROTOCOL_CHECKPOINT")
    if not raw_path:
        pytest.skip("requires actual failed native protocol checkpoint and original events")
    from app.harness.agent_loop.executor import budget_message
    from app.harness.agent_loop.policy import AgentLoopPolicy

    path = Path(raw_path)
    before = path.read_bytes()
    state = json.loads(before)
    assert state["protocol_output"] and not state["candidate"]
    events = [json.loads(line) for line in (path.parent / "events.jsonl").read_text().splitlines()]
    first = next(event for event in events if event["kind"] == "model_request")
    original = next(event for event in events if event["kind"] == "context_packed")
    initial = json.loads((path.parents[3] / "input/request.json").read_text())
    policy = AgentLoopPolicy.from_mapping(initial["scenario"]["loop"])
    pinned = [Message(**item) for item in first["visible"][:-1]]
    assert "remaining" in first["visible"][-1]["content"]
    pinned.append(budget_message(policy, state["counts"]))
    kwargs = {"budget": original["budget"], "observation_chars": policy.observation_chars, "native": True}
    with pytest.raises(ValueError, match="nothing was silently dropped"):
        pack_context(pinned + [invalid_output_context(state["protocol_output"])],
                     state["history"], state["feedback"], "", **kwargs)
    repair = invalid_output_context(state["protocol_output"], native=True)
    messages, manifest = pack_context(pinned + [repair], state["history"], state["feedback"], "", **kwargs)
    payload = json.loads(repair.content.split("\n", 1)[1])
    assert payload["invalid_native_output"]["tool_calls"] == json.loads(state["protocol_output"])["tool_calls"]
    assert token_upper_bound(messages) <= original["budget"]
    assert not manifest["omitted_history"] and not manifest["compressed_history"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("feedback", ["Protocol error: extra data", "", "Tool budget exhausted"])
def test_validation_errors_survive_transient_feedback(feedback: str) -> None:
    messages, manifest = pack_context([], [], feedback, "current candidate", budget=5000,
                                      observation_chars=1000,
                                      validation_issues=["/parameters: exceeds host budget"])
    assert any("/parameters: exceeds host budget" in m.content for m in messages)
    assert manifest["validation_issues_visible"] is True
    payload = json.loads(messages[0].content.split("\n", 1)[1])
    from app.harness.agent_loop.trace import digest
    assert payload["candidate_sha256"] == digest("current candidate")


def test_fresh_review_does_not_receive_old_validation_errors() -> None:
    messages, manifest = pack_context([], [], "", "revised candidate", budget=5000,
                                      observation_chars=1000, reviewing=True,
                                      validation_issues=["previous blocker"])
    assert all("previous blocker" not in m.content for m in messages)
    assert manifest["validation_issues_visible"] is False


def test_validation_errors_are_never_silently_compressed() -> None:
    with pytest.raises(ValueError, match="nothing was silently dropped"):
        pack_context([], [], "", "candidate", budget=500, observation_chars=10,
                     validation_issues=["unresolved validation issue " * 100])
