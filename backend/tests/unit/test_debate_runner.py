"""Verify debate auto-degrade across the three modes."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.debate.debate_runner import (
    DebateRoleOutputError,
    DebateMode,
    _auto_mode,
    _select_role_provider,
    _validate_role_completion,
    run_debate,
)
from app.agents.debate.roles import role_prompt
from app.harness.llm.mock_provider import MockProvider, build_fake_metadata
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMCompletionError,
    Message,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps

ALL_LLM_KEY_ENVS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "QWEN_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
)


class _MalformedDebateProvider:
    name = "malformed-test-provider"

    def __init__(self) -> None:
        self.prompts: dict[str, str] = {}

    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> Completion:
        role = str(config.extra.get("debate_role") or "")
        self.prompts[role] = "\n".join(message.content for message in messages)
        if role == "proposer":
            text = ""
        elif role == "judge":
            text = '<tool_calls>{"name":"search.web"}</tool_calls>'
        else:
            text = "具体且可验证的批评。"
        return Completion(text=text, provider=self.name, model=config.model)

    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> AsyncIterator[Delta]:
        if False:
            yield Delta(text="")


class _JudgeRetryProvider:
    name = "judge-retry-test-provider"

    def __init__(
        self,
        *,
        failure_code: str,
        failure_count: int,
        judge_text: str | None = None,
    ) -> None:
        self.failure_code = failure_code
        self.failure_count = failure_count
        self.judge_text = judge_text or _valid_proposal_text()
        self.judge_calls = 0
        self.judge_message_ids: list[int] = []
        self.judge_config_ids: list[int] = []

    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> Completion:
        role = str(config.extra.get("debate_role") or "")
        if role != "judge":
            return Completion(
                text="具体且可验证的研究发言。",
                provider=self.name,
                model=config.model,
            )
        self.judge_calls += 1
        self.judge_message_ids.append(id(messages))
        self.judge_config_ids.append(id(config))
        if self.judge_calls <= self.failure_count:
            empty_final = self.failure_code == "empty_final_content"
            raise LLMCompletionError(
                code=self.failure_code,
                provider=self.name,
                model=config.model,
                finish_reason="stop" if empty_final else "length",
                empty_final=empty_final,
            )
        return Completion(
            text=self.judge_text,
            provider=self.name,
            model=config.model,
        )

    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> AsyncIterator[Delta]:
        if False:
            yield Delta(text="")


def _valid_proposal_text() -> str:
    return fm_dumps(
        build_fake_metadata("proposal.v1", seed="debate-retry"),
        "# Judge proposal\n\n可验证的研究提案。",
    )


def test_auto_mode_no_keys_returns_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    mode = _auto_mode(cfg)
    assert mode == DebateMode.MOCK_DEBATE


def test_auto_mode_never_rejects_missing_debate_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "staging")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    with pytest.raises(RuntimeError, match="debate provider"):
        _auto_mode(cfg)


def test_auto_mode_partial_keys_simulates(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = replace(
        get_agent_config("idea"),
        debate_participants=(
            {"role": "proposer", "provider": "deepseek", "model": "deepseek-chat"},
            {"role": "critic", "provider": "openai", "model": "gpt-test"},
        ),
    )
    mode = _auto_mode(cfg)
    assert mode == DebateMode.SINGLE_MODEL_SIMULATED


def test_auto_mode_all_keys_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    mode = _auto_mode(cfg)
    assert mode == DebateMode.REAL_MULTI_MODEL


def test_real_role_config_inherits_all_agent_generation_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.harness.llm.model_registry as registry_module

    def build_provider(
        provider: str,
        **kwargs: Any,
    ) -> MockProvider:
        assert provider == "deepseek"
        assert kwargs["agent_config"].name == "idea"
        return MockProvider()

    monkeypatch.setattr(registry_module, "_build_real_provider", build_provider)
    base = LLMConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        temperature=0.25,
        max_tokens=16_384,
        top_p=0.9,
        response_schema="proposal.v1",
        thinking_enabled=True,
        reasoning_effort="high",
        request_timeout_seconds=321.0,
        max_retries=2,
        retry_base_delay_seconds=1.5,
        extra={"trace": "base"},
    )

    _provider, role_config, provider_name, model_name = _select_role_provider(
        "critic",
        (
            {
                "role": "critic",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            },
        ),
        (MockProvider(), base),
        get_agent_config("idea"),
        mode=DebateMode.REAL_MULTI_MODEL,
    )

    assert (provider_name, model_name) == ("deepseek", "deepseek-v4-flash")
    assert role_config.temperature == 0.25
    assert role_config.max_tokens == 16_384
    assert role_config.top_p == 0.9
    assert role_config.thinking_enabled is True
    assert role_config.reasoning_effort == "high"
    assert role_config.request_timeout_seconds == 321.0
    assert role_config.max_retries == 2
    assert role_config.retry_base_delay_seconds == 1.5
    assert role_config.response_schema == "proposal.v1"
    assert role_config.extra == {"trace": "base"}
    assert role_config.extra is not base.extra


def test_role_prompts_forbid_tools_and_require_one_judge_document() -> None:
    proposer = role_prompt("proposer", output_schema="proposal.v1")
    judge = role_prompt("judge", output_schema="proposal.v1")

    assert "不允许调用任何工具" in proposer
    assert "<tool_calls>" in proposer
    assert "完整的 proposal.v1 文档" in judge
    assert "不得附加前言" in judge


def test_judge_output_validator_rejects_empty_and_tool_envelopes_but_hands_off_schema() -> None:
    with pytest.raises(DebateRoleOutputError) as empty_error:
        _validate_role_completion(
            role="judge",
            text="  ",
            output_schema="proposal.v1",
        )
    assert empty_error.value.reason["code"] == "debate_empty_final"

    with pytest.raises(DebateRoleOutputError) as tool_error:
        _validate_role_completion(
            role="judge",
            text="<tool_calls>{}</tool_calls>",
            output_schema="proposal.v1",
        )
    assert tool_error.value.reason["code"] == "debate_tool_call_forbidden"

    invalid = _validate_role_completion(
        role="judge",
        text="这不是完整的 schema 文档。",
        output_schema="proposal.v1",
    )
    assert invalid == "这不是完整的 schema 文档。"


@pytest.mark.asyncio
async def test_run_debate_mock_mode_produces_valid_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    request = RunRequest(project="pimc", user_request="test")
    context = ContextPack(
        system="system text", project="project text", task="task text"
    )
    result = await run_debate(
        agent_name="idea",
        agent_config=cfg,
        request=request,
        context=context,
        output_schema="proposal.v1",
    )
    assert result.mode == DebateMode.MOCK_DEBATE
    assert result.final_artifact is not None
    # validate the synthesized artifact
    from app.harness.schema.validator import validate_document

    res = validate_document(
        result.final_artifact.text, expected_schema="proposal.v1"
    )
    assert res.valid, res.errors


@pytest.mark.asyncio
async def test_judge_empty_final_retries_once_and_normalizes_fenced_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app.agents.debate.debate_runner as debate_module
    import app.settings as settings_mod

    monkeypatch.setenv("MARS_RUNTIME_MODE", "staging")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    settings_mod._settings = None
    fenced = f"```markdown\n{_valid_proposal_text()}\n```"
    provider = _JudgeRetryProvider(
        failure_code="empty_final_content",
        failure_count=1,
        judge_text=fenced,
    )
    config = LLMConfig(
        provider=provider.name,
        model="test-model",
        response_schema="proposal.v1",
    )

    def select(_agent_config: object) -> tuple[_JudgeRetryProvider, LLMConfig]:
        return provider, config

    monkeypatch.setattr(debate_module, "select_provider", select)
    run_root = tmp_path / "run"
    progress_path = run_root / "idea" / "debate_transcript.v1.md"
    progress_path.parent.mkdir(parents=True)
    try:
        result = await run_debate(
            agent_name="idea",
            agent_config=replace(get_agent_config("idea"), debate_rounds=1),
            request=RunRequest(
                project="pimc",
                user_request="test",
                extra={
                    "run_id": "retry-run",
                    "run_root": str(run_root),
                    "node_key": "idea",
                },
            ),
            context=ContextPack(system="system", project="project", task="task"),
            output_schema="proposal.v1",
            mode=DebateMode.SINGLE_MODEL_SIMULATED,
            progress_path=str(progress_path),
        )
    finally:
        settings_mod._settings = None

    assert provider.judge_calls == 2
    assert len(set(provider.judge_message_ids)) == 1
    assert len(set(provider.judge_config_ids)) == 1
    assert result.final_artifact is not None
    assert result.final_artifact.text.startswith("---\n")
    from app.harness.schema.validator import validate_document

    validation = validate_document(
        result.final_artifact.text,
        expected_schema="proposal.v1",
    )
    assert validation.valid, validation.errors
    assert "empty_final_content" in progress_path.read_text(encoding="utf-8")
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (run_root / "context").glob("context_manifest.v2.*.json")
    ]
    retry_manifests = [
        item
        for item in manifests
        if item.get("purpose") == "debate_judge_round_1_empty_final_retry_1"
    ]
    assert len(retry_manifests) == 1
    assert retry_manifests[0]["diagnostics"]["completion_attempt"] == 2
    assert retry_manifests[0]["diagnostics"]["retry_trigger"] == "empty_final_content"


@pytest.mark.asyncio
async def test_judge_second_empty_final_fails_closed_without_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.debate.debate_runner as debate_module
    import app.settings as settings_mod

    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    settings_mod._settings = None
    provider = _JudgeRetryProvider(
        failure_code="empty_final_content",
        failure_count=2,
    )
    config = LLMConfig(provider=provider.name, model="test-model")

    def select(_agent_config: object) -> tuple[_JudgeRetryProvider, LLMConfig]:
        return provider, config

    monkeypatch.setattr(debate_module, "select_provider", select)
    try:
        with pytest.raises(LLMCompletionError) as exc_info:
            await run_debate(
                agent_name="idea",
                agent_config=replace(get_agent_config("idea"), debate_rounds=1),
                request=RunRequest(project="pimc", user_request="test"),
                context=ContextPack(system="system", project="project", task="task"),
                output_schema="proposal.v1",
                mode=DebateMode.SINGLE_MODEL_SIMULATED,
            )
    finally:
        settings_mod._settings = None

    assert provider.judge_calls == 2
    assert exc_info.value.reason["code"] == "empty_final_content"


@pytest.mark.asyncio
async def test_output_truncated_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.debate.debate_runner as debate_module
    import app.settings as settings_mod

    monkeypatch.setenv("MARS_RUNTIME_MODE", "staging")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    settings_mod._settings = None
    provider = _JudgeRetryProvider(
        failure_code="output_truncated",
        failure_count=1,
    )
    config = LLMConfig(provider=provider.name, model="test-model")

    def select(_agent_config: object) -> tuple[_JudgeRetryProvider, LLMConfig]:
        return provider, config

    monkeypatch.setattr(debate_module, "select_provider", select)
    try:
        with pytest.raises(LLMCompletionError) as exc_info:
            await run_debate(
                agent_name="idea",
                agent_config=replace(get_agent_config("idea"), debate_rounds=1),
                request=RunRequest(project="pimc", user_request="test"),
                context=ContextPack(system="system", project="project", task="task"),
                output_schema="proposal.v1",
                mode=DebateMode.SINGLE_MODEL_SIMULATED,
            )
    finally:
        settings_mod._settings = None

    assert provider.judge_calls == 1
    assert exc_info.value.reason["code"] == "output_truncated"


@pytest.mark.asyncio
async def test_schema_invalid_judge_is_returned_for_base_agent_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.debate.debate_runner as debate_module

    provider = _JudgeRetryProvider(
        failure_code="empty_final_content",
        failure_count=0,
        judge_text="这不是完整的 schema 文档。",
    )
    config = LLMConfig(provider=provider.name, model="test-model")

    def select(_agent_config: object) -> tuple[_JudgeRetryProvider, LLMConfig]:
        return provider, config

    monkeypatch.setattr(debate_module, "select_provider", select)
    result = await run_debate(
        agent_name="idea",
        agent_config=replace(get_agent_config("idea"), debate_rounds=1),
        request=RunRequest(project="pimc", user_request="test"),
        context=ContextPack(system="system", project="project", task="task"),
        output_schema="proposal.v1",
        mode=DebateMode.SINGLE_MODEL_SIMULATED,
    )

    assert provider.judge_calls == 1
    assert result.final_artifact is not None
    assert result.final_artifact.text == "这不是完整的 schema 文档。"


@pytest.mark.asyncio
async def test_run_debate_replaces_empty_and_tool_call_role_outputs_before_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.debate.debate_runner as debate_module
    import app.settings as settings_mod

    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    settings_mod._settings = None
    provider = _MalformedDebateProvider()
    config = LLMConfig(
        provider=provider.name,
        model="test-model",
        max_tokens=16_384,
        response_schema="proposal.v1",
    )

    def select(_agent_config: object) -> tuple[_MalformedDebateProvider, LLMConfig]:
        return provider, config

    monkeypatch.setattr(debate_module, "select_provider", select)
    result = await run_debate(
        agent_name="idea",
        agent_config=replace(get_agent_config("idea"), debate_rounds=1),
        request=RunRequest(project="pimc", user_request="test"),
        context=ContextPack(system="system", project="project", task="task"),
        output_schema="proposal.v1",
        mode=DebateMode.SINGLE_MODEL_SIMULATED,
    )

    assert result.final_artifact is not None
    assert all(turn.text.strip() for turn in result.turns)
    assert "<tool_calls>" not in result.final_artifact.text
    assert "不允许调用任何工具" in provider.prompts["proposer"]
    assert "完整的 proposal.v1 文档" in provider.prompts["judge"]


@pytest.mark.asyncio
async def test_never_mode_preserves_structured_debate_output_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.debate.debate_runner as debate_module
    import app.settings as settings_mod

    monkeypatch.setenv("MARS_RUNTIME_MODE", "staging")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    settings_mod._settings = None
    provider = _MalformedDebateProvider()
    config = LLMConfig(
        provider=provider.name,
        model="test-model",
        max_tokens=16_384,
        response_schema="proposal.v1",
    )

    def select(_agent_config: object) -> tuple[_MalformedDebateProvider, LLMConfig]:
        return provider, config

    monkeypatch.setattr(debate_module, "select_provider", select)
    try:
        with pytest.raises(DebateRoleOutputError) as exc_info:
            await run_debate(
                agent_name="idea",
                agent_config=replace(get_agent_config("idea"), debate_rounds=1),
                request=RunRequest(project="pimc", user_request="test"),
                context=ContextPack(
                    system="system",
                    project="project",
                    task="task",
                ),
                output_schema="proposal.v1",
                mode=DebateMode.SINGLE_MODEL_SIMULATED,
            )
    finally:
        settings_mod._settings = None

    assert exc_info.value.reason["code"] == "debate_empty_final"
    assert exc_info.value.reason["role"] == "proposer"


@pytest.mark.asyncio
async def test_run_debate_writes_precall_manifests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = replace(get_agent_config("idea"), debate_rounds=1)
    run_root = tmp_path / "run"
    request = RunRequest(
        project="pimc",
        user_request="debate manifest",
        extra={"run_id": "run-debate", "run_root": str(run_root), "node_key": "idea"},
    )
    context = ContextPack(
        system="system text", project="project text", task="task text"
    )

    result = await run_debate(
        agent_name="idea",
        agent_config=cfg,
        request=request,
        context=context,
        output_schema="proposal.v1",
        mode=DebateMode.MOCK_DEBATE,
    )

    assert result.final_artifact is not None
    manifests = []
    for path in sorted((run_root / "context").glob("context_manifest.v2.*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            manifests.append(raw)
    purposes = {item["purpose"] for item in manifests}
    assert {
        "debate_proposer_round_1",
        "debate_critic_round_1",
        "debate_judge_round_1",
    }.issubset(purposes)
    assert all(item["diagnostics"].get("capture_mode") == "messages" for item in manifests)
