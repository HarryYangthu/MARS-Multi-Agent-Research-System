"""Raw syntax-repair context contracts; no successful model response is substituted."""
import json

import pytest

from app.harness.agent_loop.context import pack_context
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
    assert json.loads(messages[-2].content)["candidate"] == candidate
    assert "unresolved boundary issue" in messages[-1].content
    assert not manifest["omitted_history"]


def test_oversized_invalid_output_is_not_silently_dropped() -> None:
    message = invalid_output_context("Authored raw text" * 500)
    with pytest.raises(ValueError, match="nothing was silently dropped"):
        pack_context([message], [], "Fix JSON syntax", "", budget=1000, observation_chars=500)
