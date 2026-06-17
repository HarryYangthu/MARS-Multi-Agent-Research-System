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

from app.agents.base import Artifact, ContextPack, RunRequest
from app.agents.debate.roles import role_prompt
from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.model_registry import (
    AgentConfig,
    available_providers,
    select_provider,
)
from app.harness.llm.provider_base import LLMConfig, LLMProvider, Message
from app.settings import get_settings


class DebateMode(str, Enum):
    REAL_MULTI_MODEL = "real_multi_model"
    SINGLE_MODEL_SIMULATED = "single_model_simulated"
    MOCK_DEBATE = "mock_debate"


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
    from app.settings import get_settings

    if get_settings().mars_mock_mode == "always":
        return DebateMode.MOCK_DEBATE
    avail = available_providers()
    if not agent_config.debate_enabled:
        return DebateMode.MOCK_DEBATE
    required = {p.get("provider") for p in agent_config.debate_participants}
    if required.issubset(avail) and required - {"mock"}:
        return DebateMode.REAL_MULTI_MODEL
    if avail - {"mock"}:
        return DebateMode.SINGLE_MODEL_SIMULATED
    return DebateMode.MOCK_DEBATE


def _select_role_provider(
    role: str,
    participants: tuple[Mapping[str, Any], ...],
    fallback: tuple[LLMProvider, LLMConfig],
    *,
    mode: DebateMode,
) -> tuple[LLMProvider, LLMConfig, str, str]:
    """Pick a provider for a debate role.

    Returns (provider, config, provider_name, model_name).
    In real_multi_model mode each participant's own provider is used.
    In single_model_simulated mode the fallback (the agent's primary) is
    used for every role. In mock_debate mode MockProvider is forced.
    """
    if mode == DebateMode.MOCK_DEBATE:
        return (
            MockProvider(),
            LLMConfig(provider="mock", model="mock-1", response_schema=fallback[1].response_schema),
            "mock",
            "mock-1",
        )

    if mode == DebateMode.SINGLE_MODEL_SIMULATED:
        return fallback[0], fallback[1], fallback[1].provider, fallback[1].model

    # real_multi_model
    for p in participants:
        if p.get("role") == role:
            from app.harness.llm.model_registry import _build_real_provider

            real = _build_real_provider(str(p.get("provider"))) or MockProvider()
            cfg = LLMConfig(
                provider=str(p.get("provider")),
                model=str(p.get("model", "default")),
                temperature=0.7,
                response_schema=fallback[1].response_schema,
            )
            return real, cfg, cfg.provider, cfg.model
    return fallback[0], fallback[1], fallback[1].provider, fallback[1].model


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
            Path(progress_path).write_text(header + body, encoding="utf-8")
        except OSError as exc:
            logger.warning("debate progress write failed: {}", exc)

    _flush_progress(running=True)

    for r in range(rounds):
        for role in roles:
            provider, cfg, p_name, m_name = _select_role_provider(
                role, participants, fallback, mode=mode
            )
            cfg.extra = dict(cfg.extra or {})
            cfg.extra["debate_role"] = role
            cfg.response_schema = output_schema
            messages = context.to_messages(
                agent_name=agent_name, output_schema=output_schema
            )
            messages.insert(
                1,
                Message(role="system", content=role_prompt(role)),
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
            )
            try:
                completion = await asyncio.wait_for(
                    provider.complete(messages, cfg),
                    timeout=get_settings().mars_llm_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "debate role {} provider {} failed ({}); falling back to mock",
                    role,
                    p_name,
                    exc,
                )
                mock = MockProvider(default_schema=output_schema)
                completion = await mock.complete(messages, cfg)
            last_text = completion.text
            turns.append(
                Turn(role=role, provider=p_name, model=m_name, text=completion.text)
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


def _artifact_from_text(text: str, output_schema: str) -> Artifact:
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    try:
        parsed = parse_fm(text)
        metadata = parsed.metadata
        body = parsed.body
    except Exception:
        metadata = {}
        body = text
    return Artifact(
        text=text,
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
) -> None:
    raw_root = request.extra.get("run_root")
    if not raw_root:
        return
    try:
        from pathlib import Path

        from app.harness.context.engine import write_messages_manifest

        write_messages_manifest(
            run_root=Path(str(raw_root)),
            run_id=str(request.extra.get("run_id", "")),
            agent=agent_name,
            node_key=str(request.extra.get("node_key", agent_name)),
            project=request.project,
            output_schema=output_schema,
            purpose=purpose,
            messages=messages,
            diagnostics_extra={"debate_role": role, "debate_mode": mode},
        )
    except Exception as exc:
        logger.warning("debate context manifest write failed: {}", exc)


# convenience for asyncio.run() in CLIs
def run_debate_sync(**kwargs: Any) -> DebateResult:
    return asyncio.run(run_debate(**kwargs))
