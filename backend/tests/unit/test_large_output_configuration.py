"""Validate large model output settings without invoking a model or service."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.config import AgentLlmUpdateRow
from app.agents.idea.runtime_profile import _Author
from app.harness.context.runtime_policy import input_budget


def test_settings_accept_model_output_ceiling_above_old_global_cap() -> None:
    row = AgentLlmUpdateRow(agent="idea_reviewer", provider="deepseek",
                            model="deepseek-flash", max_tokens=393216)
    restored = AgentLlmUpdateRow.model_validate_json(row.model_dump_json())
    assert restored.max_tokens == 393216


def test_explicit_research_profile_accepts_large_model_output() -> None:
    model = _Author(provider="deepseek", name="deepseek-v4-pro", max_tokens=393216,
                    temperature=0.2, top_p=1.0, thinking=True, reasoning_effort="low",
                    timeout_seconds=360.0, max_retries=0, retry_base_delay_seconds=1.0,
                    api_key_env="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com/v1",
                    base_url_env="")
    assert model.max_tokens == 393216


@pytest.mark.parametrize("limit", [0, -1])
def test_settings_still_reject_nonpositive_output(limit: int) -> None:
    with pytest.raises(ValidationError):
        AgentLlmUpdateRow(agent="idea", provider="deepseek",
                          model="deepseek-flash", max_tokens=limit)


def test_large_output_reserves_room_inside_verified_model_window() -> None:
    policy = {"input_budget": 128000, "safety_margin": 4096}
    budget = input_budget(policy, 128000, output_reserve=393216, model_window=1048576)
    assert budget == 651264
    assert budget + 393216 + policy["safety_margin"] == 1048576
    with pytest.raises(ValueError, match="no input space"):
        input_budget(policy, 128000, output_reserve=393216, model_window=128000)
