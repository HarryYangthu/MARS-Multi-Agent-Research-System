"""Actual serializer, schema and disk-ledger contracts; no provider/service doubles."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate

from app.harness.agent_loop.trace import LoopTrace
from app.harness.llm.openai_provider import OpenAIProvider, ZhipuProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.tools.config import tool_config


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
    with pytest.raises(ValidationError):
        validate(args, tool_config(tool).input_schema)


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
