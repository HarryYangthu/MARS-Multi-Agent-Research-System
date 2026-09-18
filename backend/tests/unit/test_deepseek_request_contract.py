"""Real request serialization only: no HTTP or model execution is substituted."""
from copy import deepcopy

import pytest

from app.harness.agent_loop.native_protocol import SUBMIT_DOCUMENT, native_specs
from app.harness.llm.openai_provider import DeepSeekProvider, OpenAIProvider
from app.harness.llm.provider_base import LLMConfig, Message, ReasoningEffort


@pytest.mark.parametrize("effort", [None, "low", "medium", "high", "max"])
def test_explicit_non_thinking_overrides_provider_and_call_effort(effort: ReasoningEffort | None) -> None:
    provider = DeepSeekProvider(api_key="request-contract-not-a-credential",
                                default_thinking_enabled=True, default_reasoning_effort="high")
    config = LLMConfig(provider="deepseek", model="request-contract", thinking_enabled=False,
                       reasoning_effort=effort)
    wire = provider._request_kwargs([Message("user", "Authored request contract")], config)
    assert wire["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in wire
    assert wire["temperature"] == config.temperature and wire["top_p"] == config.top_p
    assert config.thinking_enabled is False and config.reasoning_effort == effort
    assert provider._client is None


@pytest.mark.parametrize("stream", [False, True])
def test_explicit_non_thinking_single_submission_is_forced(stream: bool) -> None:
    tools = native_specs([], {"type": "object"})
    originals = deepcopy(tools)
    provider = DeepSeekProvider(api_key="request-contract-not-a-credential",
                                default_thinking_enabled=True, default_reasoning_effort="high")
    config = LLMConfig(provider="deepseek", model="request-contract", thinking_enabled=False, tools=tools)
    wire = provider._request_kwargs([], config, stream=stream)
    assert wire["tool_choice"] == {"type": "function", "function": {"name": SUBMIT_DOCUMENT}}
    assert wire["tools"] == list(originals) and wire["parallel_tool_calls"] is False
    assert wire["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in wire
    assert wire.get("stream", False) is stream
    assert config.tools == originals and provider._client is None


def test_thinking_submission_is_not_forced() -> None:
    provider = DeepSeekProvider(api_key="request-contract-not-a-credential", default_reasoning_effort="high")
    config = LLMConfig(provider="deepseek", model="request-contract", thinking_enabled=True,
                       tools=native_specs([], {"type": "object"}), extra={"native_observation_history": True})
    wire = provider._request_kwargs([], config)
    assert "tool_choice" not in wire
    assert wire["reasoning_effort"] == "high"
    assert wire["extra_body"] == {"thinking": {"type": "enabled"}}
    assert provider._client is None


@pytest.mark.parametrize("thinking", [False, True])
def test_research_with_multiple_tools_keeps_model_choice(thinking: bool) -> None:
    tools = native_specs([
        {"name": "search.query", "description": "Search documents", "args_schema": {"type": "object"}},
        {"name": "file.read", "description": "Read project files", "args_schema": {"type": "object"}},
    ], {"type": "object"})
    provider = DeepSeekProvider(api_key="request-contract-not-a-credential", default_reasoning_effort="high")
    config = LLMConfig(provider="deepseek", model="request-contract", thinking_enabled=thinking,
                       tools=tools, extra={"native_observation_history": True})
    wire = provider._request_kwargs([], config)
    assert "tool_choice" not in wire and wire["tools"] == list(tools)
    assert ("reasoning_effort" in wire) is thinking
    assert provider._client is None


@pytest.mark.parametrize("include_read_tool", [False, True])
def test_non_submission_requests_do_not_force_a_tool(include_read_tool: bool) -> None:
    tools = native_specs([{"name": "file.read", "description": "Read files", "args_schema": {"type": "object"}}]
                         if include_read_tool else [])
    provider = DeepSeekProvider(api_key="request-contract-not-a-credential")
    wire = provider._request_kwargs([], LLMConfig(provider="deepseek", model="request-contract",
                                                 thinking_enabled=False, tools=tools))
    assert "tool_choice" not in wire and provider._client is None


def test_provider_default_non_thinking_is_consistent_but_does_not_force_submission() -> None:
    provider = DeepSeekProvider(api_key="request-contract-not-a-credential",
                                default_thinking_enabled=False, default_reasoning_effort="high")
    wire = provider._request_kwargs([], LLMConfig(provider="deepseek", model="request-contract",
                                                 tools=native_specs([], {"type": "object"})))
    assert wire["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in wire and "tool_choice" not in wire
    assert provider._client is None


def test_other_provider_keeps_effort_and_submission_choice() -> None:
    provider = OpenAIProvider(api_key="request-contract-not-a-credential")
    config = LLMConfig(provider="openai", model="request-contract", thinking_enabled=False,
                       reasoning_effort="high", tools=native_specs([], {"type": "object"}))
    wire = provider._request_kwargs([], config)
    assert wire["reasoning_effort"] == "high"
    assert "tool_choice" not in wire and "extra_body" not in wire
    assert wire["tools"] == list(config.tools) and provider._client is None
