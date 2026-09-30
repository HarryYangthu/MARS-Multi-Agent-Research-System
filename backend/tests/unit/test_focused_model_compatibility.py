"""Serialize the real focused profile without sending requests or reading keys."""
from __future__ import annotations

import pytest

from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.harness.llm.openai_provider import ZhipuProvider
from app.harness.llm.provider_base import LLMConfig, Message


@pytest.mark.parametrize("role", ["author", "reviewer", "reflection"])
def test_actual_focused_glm_roles_have_compatible_request_settings(role: str) -> None:
    snapshot = FocusedIdeaAgent().service_profile_snapshot
    agent = snapshot["reviewer" if role == "reviewer" else "author"]
    model = agent["model"]
    thinking = model["thinking"]
    effort = model["reasoning_effort"]
    if role == "reflection":
        thinking = agent["loop"]["reflection_thinking_enabled"]
        effort = agent["loop"]["reflection_reasoning_effort"]
    config = LLMConfig(
        provider=model["provider"], model=model["name"],
        thinking_enabled=thinking, reasoning_effort=effort,
    )
    provider = ZhipuProvider(api_key="serializer-input-not-a-credential")
    request = provider._request_kwargs([Message("user", "检查实际研究配置")], config)
    assert request["extra_body"]["thinking"]["type"] == "enabled"
    assert request["reasoning_effort"] in {"low", "high", "max"}
    assert provider._client is None
