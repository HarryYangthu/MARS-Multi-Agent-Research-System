"""Multi-model debate runner.

Three modes (DESIGN §16.3):

* ``real_multi_model``  — every participant uses its declared provider.
* ``single_model_simulated`` — only one provider is available; reuse it
  while swapping system prompts to fake distinct roles.
* ``mock_debate`` — no real providers; uses ``MockProvider`` everywhere.

The runner picks the mode automatically based on ``available_providers()``
and the agent's debate config. The output is a list of ``Turn`` objects
plus a synthesized final artifact (the *judge* role's last turn).
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.debate.roles import role_prompt
from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.model_registry import (
    AgentConfig,
    available_providers,
    provider_configured_for_agent,
    select_provider,
)
from app.harness.llm.provider_base import (
    Completion,
    LLMConfig,
    LLMCompletionError,
    LLMProvider,
    Message,
    llm_call_deadline_seconds,
)
from app.settings import get_settings


class DebateMode(str, Enum):
    REAL_MULTI_MODEL = "real_multi_model"
    SINGLE_MODEL_SIMULATED = "single_model_simulated"
    MOCK_DEBATE = "mock_debate"


class DebateRoleOutputError(RuntimeError):
    """Raised without echoing malformed model output into logs or artifacts."""

    def __init__(self, *, code: str, role: str, reason: str) -> None:
        self.reason = {"code": code, "role": role, "reason": reason}
        super().__init__(f"{code}: role={role} reason={reason}")


@dataclass
class Turn:
    role: str
    provider: str
    model: str
    text: str


@dataclass
class DebateResult:
    mode: DebateMode
    rounds: int
    turns: list[Turn] = field(default_factory=list)
    final_artifact: Artifact | None = None
    transcript_md: str = ""

    def consensus_summary(self) -> str:
        if not self.turns:
            return ""
        return f"{self.mode.value} 模式辩论完成：{self.rounds} 轮，{len(self.turns)} 次发言"


def _auto_mode(agent_config: AgentConfig) -> DebateMode:
    """Replicates the auto-degrade logic from DESIGN §16.3."""
    settings = get_settings()
    if settings.mars_mock_mode == "always":
        if settings.is_production:
            raise RuntimeError("production mode cannot use MARS_MOCK_MODE=always")
        return DebateMode.MOCK_DEBATE
    avail = available_providers()
    if not agent_config.debate_enabled:
        return DebateMode.MOCK_DEBATE
    required = {
        str(p.get("provider")) for p in agent_config.debate_participants if p.get("provider")
    }
    if provider_configured_for_agent(agent_config):
        avail.add(agent_config.model_provider)
    missing = required - avail
    if missing and (settings.is_production or settings.mars_mock_mode == "never"):
        raise RuntimeError(
            f"debate provider(s) not configured for agent '{agent_config.name}': "
            + ", ".join(sorted(missing))
        )
    if required.issubset(avail) and required - {"mock"}:
        return DebateMode.REAL_MULTI_MODEL
    if avail - {"mock"}:
        return DebateMode.SINGLE_MODEL_SIMULATED
    return DebateMode.MOCK_DEBATE


def _select_role_provider(
    role: str,
    participants: tuple[Mapping[str, Any], ...],
    fallback: tuple[LLMProvider, LLMConfig],
    agent_config: AgentConfig,
    *,
    mode: DebateMode,
) -> tuple[LLMProvider, LLMConfig, str, str]:
    """Pick a provider for a debate role.

    Returns (provider, config, provider_name, model_name).
    In real_multi_model mode each participant's own provider is used.
    In single_model_simulated mode the fallback (the agent's primary) is
    used for every role. In mock_debate mode MockProvider is forced.
    """
    settings = get_settings()
    if mode == DebateMode.MOCK_DEBATE:
        if settings.is_production or settings.mars_mock_mode == "never":
            raise RuntimeError("mock debate is disabled by runtime settings")
        return (
            MockProvider(),
            _role_llm_config(fallback[1], provider="mock", model="mock-1"),
            "mock",
            "mock-1",
        )

    if mode == DebateMode.SINGLE_MODEL_SIMULATED:
        return (
            fallback[0],
            _role_llm_config(
                fallback[1],
                provider=fallback[1].provider,
                model=fallback[1].model,
            ),
            fallback[1].provider,
            fallback[1].model,
        )

    # real_multi_model
    for p in participants:
        if p.get("role") == role:
            from app.harness.llm.model_registry import _build_real_provider

            cfg = _role_llm_config(
                fallback[1],
                provider=str(p.get("provider")),
                model=str(p.get("model", "default")),
            )
            real = _build_real_provider(
                cfg.provider,
                agent_config=agent_config,
                api_key_env=str(p.get("api_key_env") or ""),
                base_url=str(p.get("base_url") or ""),
                base_url_env=str(p.get("base_url_env") or ""),
            )
            if real is None:
                if settings.is_production or settings.mars_mock_mode == "never":
                    raise RuntimeError(
                        f"debate provider '{cfg.provider}' failed to initialize "
                        f"for role '{role}'"
                    )
                real = MockProvider()
            return real, cfg, cfg.provider, cfg.model
    return (
        fallback[0],
        _role_llm_config(
            fallback[1],
            provider=fallback[1].provider,
            model=fallback[1].model,
        ),
        fallback[1].provider,
        fallback[1].model,
    )


def _role_llm_config(
    base: LLMConfig,
    *,
    provider: str,
    model: str,
) -> LLMConfig:
    """Clone every bounded generation control while changing role routing only."""

    return LLMConfig(
        provider=provider,
        model=model,
        temperature=base.temperature,
        max_tokens=base.max_tokens,
        top_p=base.top_p,
        response_schema=base.response_schema,
        thinking_enabled=base.thinking_enabled,
        reasoning_effort=base.reasoning_effort,
        request_timeout_seconds=base.request_timeout_seconds,
        max_retries=base.max_retries,
        retry_base_delay_seconds=base.retry_base_delay_seconds,
        extra=dict(base.extra),
    )


def _resolve_roles(participants: tuple[Mapping[str, Any], ...]) -> list[str]:
    if participants:
        seen: list[str] = []
        for p in participants:
            r = str(p.get("role", "proposer"))
            if r not in seen:
                seen.append(r)
        if "judge" not in seen:
            seen.append("judge")
        return seen
    return ["proposer", "critic", "judge"]


async def run_debate(
    *,
    agent_name: str,
    agent_config: AgentConfig,
    request: RunRequest,
    context: ContextPack,
    output_schema: str,
    mode: DebateMode | None = None,
    progress_path: str | None = None,
) -> DebateResult:
    """Run a debate and return the synthesized artifact + transcript.

    If ``progress_path`` is provided, writes the running transcript to that
    path after each turn so the UI can stream it in.
    """
    if mode is None:
        mode = _auto_mode(agent_config)
    logger.info("debate ({}) starting in mode={}", agent_name, mode.value)

    fallback = select_provider(agent_config)
    rounds = max(1, agent_config.debate_rounds)
    participants = tuple(agent_config.debate_participants)
    roles = _resolve_roles(participants)
    total_turns = rounds * len(roles)

    turns: list[Turn] = []
    last_text = context.task or "开始辩论。"
    retry_notes: list[str] = []

    def _flush_progress(running: bool) -> None:
        if not progress_path:
            return
        try:
            from pathlib import Path

            header = (
                f"# 多模型辩论转录（模式={mode.value}，轮数={rounds}，角色数={len(roles)}）\n"
                f"_{('运行中…' if running else '已完成')}_  "
                f"（{len(turns)}/{total_turns} 次发言）\n\n"
            )
            body = "\n".join(
                f"## {i}. {_role_label(t.role)}（{t.provider}/{t.model}）\n\n{t.text}\n"
                for i, t in enumerate(turns, 1)
            )
            retry_body = ""
            if retry_notes:
                retry_body = "## 系统恢复记录\n\n" + "\n".join(
                    f"- {note}" for note in retry_notes
                ) + "\n\n"
            Path(progress_path).write_text(
                header + retry_body + body,
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("debate progress write failed: {}", exc)

    _flush_progress(running=True)

    for r in range(rounds):
        for role in roles:
            provider, cfg, p_name, m_name = _select_role_provider(
                role, participants, fallback, agent_config, mode=mode
            )
            cfg.extra = dict(cfg.extra or {})
            cfg.extra["debate_role"] = role
            cfg.response_schema = output_schema
            messages = context.to_messages(
                agent_name=agent_name, output_schema=output_schema
            )
            messages.insert(
                1,
                Message(
                    role="system",
                    content=role_prompt(role, output_schema=output_schema),
                ),
            )
            if last_text and role != roles[0]:
                messages.append(
                    Message(role="assistant", content=f"上一轮发言：\n{last_text}")
                )
            _write_debate_manifest(
                request=request,
                agent_name=agent_name,
                output_schema=output_schema,
                messages=messages,
                purpose=f"debate_{role}_round_{r + 1}",
                role=role,
                mode=mode.value,
                diagnostics_extra={
                    "completion_attempt": 1,
                    "empty_final_retry_budget": 1,
                },
            )
            try:
                try:
                    completion = await _complete_role_once(provider, messages, cfg)
                except LLMCompletionError as exc:
                    if exc.reason.get("code") != "empty_final_content":
                        raise
                    retry_notes.append(
                        f"{_role_label(role)}第 {r + 1} 轮返回空最终答案；"
                        "已执行有界重试 1/1（empty_final_content）。"
                    )
                    _flush_progress(running=True)
                    _write_debate_manifest(
                        request=request,
                        agent_name=agent_name,
                        output_schema=output_schema,
                        messages=messages,
                        purpose=(
                            f"debate_{role}_round_{r + 1}_"
                            "empty_final_retry_1"
                        ),
                        role=role,
                        mode=mode.value,
                        diagnostics_extra={
                            "completion_attempt": 2,
                            "retry_index": 1,
                            "retry_trigger": "empty_final_content",
                            "empty_final_retry_budget": 1,
                        },
                    )
                    # The retry intentionally reuses the exact provider,
                    # config, and messages. A second empty completion is
                    # propagated unchanged and must never degrade to mock.
                    completion = await _complete_role_once(provider, messages, cfg)
                role_text = _validate_role_completion(
                    role=role,
                    text=completion.text,
                    output_schema=output_schema,
                )
            except Exception as exc:
                settings = get_settings()
                if (
                    isinstance(exc, LLMCompletionError)
                    and exc.reason.get("code") == "empty_final_content"
                ):
                    raise
                if settings.is_production or settings.mars_mock_mode == "never":
                    if isinstance(exc, (DebateRoleOutputError, LLMCompletionError)):
                        raise
                    raise RuntimeError(
                        f"debate role '{role}' provider '{p_name}' failed"
                    ) from exc
                logger.warning(
                    "debate role {} provider {} failed ({}); falling back to mock",
                    role,
                    p_name,
                    exc,
                )
                mock = MockProvider(default_schema=output_schema)
                completion = await mock.complete(messages, cfg)
                role_text = _validate_role_completion(
                    role=role,
                    text=completion.text,
                    output_schema=output_schema,
                )
            last_text = role_text
            turns.append(
                Turn(role=role, provider=p_name, model=m_name, text=role_text)
            )
            _flush_progress(running=True)

    _flush_progress(running=False)

    # The judge's last turn (or the final turn) is the synthesized artifact.
    judge_turns = [t for t in turns if t.role == "judge"]
    final_text = (judge_turns[-1] if judge_turns else turns[-1]).text

    transcript = _format_transcript(mode, rounds, turns)
    artifact = _artifact_from_text(final_text, output_schema)
    return DebateResult(
        mode=mode,
        rounds=rounds,
        turns=turns,
        final_artifact=artifact,
        transcript_md=transcript,
    )


def _format_transcript(mode: DebateMode, rounds: int, turns: list[Turn]) -> str:
    lines = [f"# 多模型辩论转录（模式={mode.value}，轮数={rounds}）", ""]
    for i, t in enumerate(turns, 1):
        lines.append(f"## {i}. {_role_label(t.role)}（{t.provider}/{t.model}）")
        lines.append("")
        lines.append(t.text)
        lines.append("")
    return "\n".join(lines)


def _role_label(role: str) -> str:
    labels = {
        "proposer": "提案者",
        "critic": "批判者",
        "judge": "裁判",
        "positive_reviewer": "正向审稿人",
    }
    return labels.get(role, role)


async def _complete_role_once(
    provider: LLMProvider,
    messages: list[Message],
    config: LLMConfig,
) -> Completion:
    return await asyncio.wait_for(
        provider.complete(messages, config),
        timeout=llm_call_deadline_seconds(
            config,
            minimum_seconds=get_settings().mars_llm_timeout_seconds,
        ),
    )


def _validate_role_completion(
    *,
    role: str,
    text: str,
    output_schema: str,
) -> str:
    if not text.strip():
        raise DebateRoleOutputError(
            code="debate_empty_final",
            role=role,
            reason="provider returned no visible final answer",
        )
    lowered = text.casefold()
    if "<tool_calls" in lowered or "</tool_calls>" in lowered:
        raise DebateRoleOutputError(
            code="debate_tool_call_forbidden",
            role=role,
            reason="debate roles must return text and cannot invoke tools",
        )
    if role != "judge" or not output_schema:
        return text

    # Match BaseAgent's normal completion boundary: fenced documents and a
    # short preamble are transport noise, not schema failures.
    normalized = BaseAgent._unwrap_llm_text(text)
    if not normalized.strip():
        raise DebateRoleOutputError(
            code="debate_empty_final",
            role=role,
            reason="provider returned no visible final answer after normalization",
        )
    from app.harness.schema.validator import validate_document

    validation = validate_document(normalized, expected_schema=output_schema)
    if not validation.valid:
        # Do not discard a non-empty, tool-free judge document here. The
        # enclosing BaseAgent.run_loop owns bounded schema repair and HITL
        # preservation, so returning the draft is the recoverable path.
        logger.warning(
            "debate judge schema-invalid; handing document to agent repair: {}",
            validation.first_error() or "schema validation failed",
        )
    return normalized


def _artifact_from_text(text: str, output_schema: str) -> Artifact:
    from app.harness.schema.frontmatter_parser import close_unclosed_frontmatter
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    cleaned = close_unclosed_frontmatter(BaseAgent._unwrap_llm_text(text))
    try:
        parsed = parse_fm(cleaned)
        metadata = parsed.metadata
        body = parsed.body
    except Exception:
        metadata = {}
        body = cleaned
    return Artifact(
        text=cleaned,
        schema_id=str(metadata.get("schema", output_schema)),
        metadata=metadata,
        body=body,
    )


def _write_debate_manifest(
    *,
    request: RunRequest,
    agent_name: str,
    output_schema: str,
    messages: list[Message],
    purpose: str,
    role: str,
    mode: str,
    diagnostics_extra: Mapping[str, Any] | None = None,
) -> None:
    raw_root = request.extra.get("run_root")
    if not raw_root:
        return
    try:
        from pathlib import Path

        from app.harness.context.engine import write_messages_manifest

        diagnostics: dict[str, Any] = {
            "debate_role": role,
            "debate_mode": mode,
        }
        if diagnostics_extra:
            diagnostics.update(diagnostics_extra)
        write_messages_manifest(
            run_root=Path(str(raw_root)),
            run_id=str(request.extra.get("run_id", "")),
            agent=agent_name,
            node_key=str(request.extra.get("node_key", agent_name)),
            project=request.project,
            output_schema=output_schema,
            purpose=purpose,
            messages=messages,
            diagnostics_extra=diagnostics,
        )
    except Exception as exc:
        logger.warning("debate context manifest write failed: {}", exc)


# convenience for asyncio.run() in CLIs
def run_debate_sync(**kwargs: Any) -> DebateResult:
    return asyncio.run(run_debate(**kwargs))
