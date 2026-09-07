"""Actual debate configuration and pure document boundaries; no fabricated role execution."""
from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.debate.debate_runner import (
    DebateMode, DebateRoleOutputError, _artifact_from_text, _auto_mode,
    _resolve_roles, _role_llm_config, _validate_role_completion, _write_debate_manifest, run_debate,
)
from app.agents.debate.roles import role_prompt
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.schema.validator import validate_document


@pytest.mark.parametrize("mode", ["mock_debate", "mock", "fake"])
def test_removed_debate_modes_cannot_be_selected(mode: str) -> None:
    with pytest.raises(ValueError):
        DebateMode(mode)


def test_unconfigured_primary_cannot_silently_degrade_to_a_successful_debate() -> None:
    config = replace(get_agent_config("idea"), model_provider="unconfigured")
    with pytest.raises(RuntimeError, match="provider"):
        _auto_mode(config)


def test_missing_participant_is_not_replaced_by_an_available_primary() -> None:
    config = replace(get_agent_config("idea"), model_provider="local_vllm",
                     api_key_env="", base_url_env="", base_url="http://127.0.0.1:1/v1",
                     debate_participants=({"role": "critic", "provider": "unconfigured", "model": "none"},))
    with pytest.raises(RuntimeError, match="providers missing"):
        _auto_mode(config)


def test_declared_local_endpoints_select_real_mode_without_claiming_a_connection() -> None:
    config = replace(get_agent_config("idea"), model_provider="local_vllm",
                     api_key_env="", base_url_env="", base_url="http://127.0.0.1:1/v1",
                     debate_participants=({"role": "critic", "provider": "local_vllm", "model": "local"},))
    assert _auto_mode(config) is DebateMode.REAL_MULTI_MODEL


def test_role_config_preserves_generation_limits_json_mode_and_attempt_observer() -> None:
    def observe(_kind: str, _row: dict[str, object]) -> None:
        pass
    base = LLMConfig(provider="local_vllm", model="configured", max_tokens=16384, temperature=0.25,
                     top_p=0.9, response_schema="proposal.v1", thinking_enabled=True, reasoning_effort="high",
                     request_timeout_seconds=321, max_retries=2, retry_base_delay_seconds=1.5,
                     json_mode=True, attempt_observer=observe, extra={"trace": "source"})
    role = _role_llm_config(base, provider="local_vllm", model="other-configured-model")
    assert asdict(role) == {**asdict(base), "model": "other-configured-model"}
    assert role.extra is not base.extra and role.attempt_observer is observe


def test_role_order_is_unique_and_includes_judge() -> None:
    assert _resolve_roles(()) == ["proposer", "critic", "judge"]
    assert _resolve_roles(({"role": "critic"}, {"role": "critic"}, {"role": "proposer"})) == ["critic", "proposer", "judge"]


def test_role_prompts_forbid_tools_and_require_one_judge_document() -> None:
    assert "不允许调用任何工具" in role_prompt("proposer", output_schema="proposal.v1")
    judge = role_prompt("judge", output_schema="proposal.v1")
    assert "完整的 proposal.v1 文档" in judge and "不得附加前言" in judge


@pytest.mark.parametrize("text,code", [(" ", "debate_empty_final"),
                                      ("<tool_calls>{}</tool_calls>", "debate_tool_call_forbidden")])
def test_role_validator_rejects_empty_or_tool_envelope(text: str, code: str) -> None:
    with pytest.raises(DebateRoleOutputError) as caught:
        _validate_role_completion(role="judge", text=text, output_schema="proposal.v1")
    assert caught.value.reason["code"] == code


def test_schema_invalid_authored_document_is_preserved_for_validation_without_repair() -> None:
    text = "---\nschema: proposal.v1\nproject: pimc\nagent: idea\n"
    result = _validate_role_completion(role="judge", text=text, output_schema="proposal.v1")
    artifact = _artifact_from_text(result, "proposal.v1")
    assert artifact.text == text.strip()
    assert not validate_document(artifact.text, expected_schema="proposal.v1").valid


def test_debate_manifest_records_actual_input_without_creating_turns(tmp_path: Path) -> None:
    _write_debate_manifest(
        request=RunRequest(project="pimc", user_request="authored context",
                           extra={"run_root": str(tmp_path), "run_id": "manifest-contract"}),
        agent_name="idea", output_schema="proposal.v1", messages=[Message("user", "authored context")],
        purpose="before-request", role="proposer", mode="real_multi_model",
    )
    assert list((tmp_path / "context").rglob("*.json"))
    assert not list(tmp_path.rglob("idea_proposal*.md"))


@pytest.mark.asyncio
async def test_unconfigured_debate_writes_no_answer_or_completed_transcript(tmp_path: Path) -> None:
    config = replace(get_agent_config("idea"), model_provider="unconfigured")
    progress = tmp_path / "debate.md"
    with pytest.raises(RuntimeError, match="provider"):
        await run_debate(agent_name="idea", agent_config=config,
                         request=RunRequest(project="pimc", user_request="real provider required"),
                         context=ContextPack(system="rules", project="pimc", task="research"),
                         output_schema="proposal.v1", progress_path=str(progress))
    assert not progress.exists()
