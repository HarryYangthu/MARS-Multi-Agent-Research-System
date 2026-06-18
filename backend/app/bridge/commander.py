"""Commander — the conversational master Agent.

LLM-driven (DeepSeek) closed-loop controller. Each user turn runs a small
ReAct loop: the LLM emits a strict-JSON decision {reply, next_state, actions},
the Commander executes any tool actions against the EXISTING engine, feeds the
results back, and lets the LLM react — until it has nothing left to do.

When no real LLM provider is configured it falls back to a deterministic
mock decision path so the zero-dependency demo still works.

Layer: bridge/ (product orchestration). It drives the conversation FSM
(harness/runtime/conversation_state) and the existing Orchestrator.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool, tools_for_prompt
from app.bridge.orchestrator import Orchestrator
from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import LLMProvider, Message
from app.harness.runtime.conversation_state import (
    ConversationState,
    can_transition,
)
from app.settings import get_settings
from app.storage.run_store import RunStore

MAX_STEPS = 4  # max ReAct iterations per user turn


@dataclass
class Decision:
    reply: str = ""
    next_state: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)


class Commander:
    name = "commander"

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        run_store: RunStore | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.run_store = run_store or orchestrator.run_store
        self._provider, self._llm_config = self._resolve_provider()
        self._is_mock = isinstance(self._provider, MockProvider)

    def _resolve_provider(self) -> tuple[LLMProvider, Any]:
        try:
            cfg = get_agent_config("commander")
        except KeyError:
            # Fall back to the idea agent's model config (also DeepSeek) if no
            # explicit commander block exists in agents.yaml.
            cfg = get_agent_config("idea")
        return select_provider(cfg)

    # ----------------------------------------------------------- public API

    async def handle_user_message(
        self, session: CommanderSession, text: str
    ) -> list[ChatMessage]:
        """Process one user turn; returns the messages emitted this turn."""
        session.add(ChatMessage(role="user", content=text))
        ctx = ToolContext(
            orchestrator=self.orchestrator,
            session=session,
            run_store=self.run_store,
        )
        emitted: list[ChatMessage] = []

        for _step in range(MAX_STEPS):
            decision = await self._decide(session)

            if decision.next_state:
                self._try_transition(session, decision.next_state)

            if decision.reply:
                emitted.append(session.add(ChatMessage(role="assistant", content=decision.reply)))

            if not decision.actions:
                break

            for action in decision.actions:
                tool = str(action.get("tool", ""))
                args = action.get("args", {}) or {}
                if not isinstance(args, dict):
                    args = {}
                result = await execute_tool(tool, args, ctx)
                emitted.append(
                    session.add(
                        ChatMessage(
                            role="tool",
                            content=_summarize_result(tool, result),
                            tool_name=tool,
                            tool_args=args,
                            tool_result=result,
                        )
                    )
                )
            # loop again so the LLM can react to tool results
        return emitted

    # ----------------------------------------------------------- decision

    async def _decide(self, session: CommanderSession) -> Decision:
        if self._is_mock:
            return self._decide_mock(session)
        return await self._decide_llm(session)

    async def _decide_llm(self, session: CommanderSession) -> Decision:
        messages = self._build_messages(session)
        try:
            completion = await asyncio.wait_for(
                self._provider.complete(messages, self._llm_config),
                timeout=get_settings().mars_llm_timeout_seconds,
            )
            return _parse_decision(completion.text)
        except Exception as exc:
            settings = get_settings()
            if settings.is_production or settings.mars_mock_mode == "never":
                raise
            logger.warning("commander LLM decide failed ({}); using mock", exc)
            return self._decide_mock(session)

    def _build_messages(self, session: CommanderSession) -> list[Message]:
        sys = _system_prompt(session)
        msgs: list[Message] = [Message(role="system", content=sys)]
        if session.rolling_summary:
            msgs.append(
                Message(
                    role="system",
                    content="[rolling_summary]\n" + session.rolling_summary,
                )
            )
        # Replay dialogue. Tool messages are folded in as user-side observations.
        for m in session.context_messages():
            if m.role == "user":
                msgs.append(Message(role="user", content=m.content))
            elif m.role == "assistant":
                msgs.append(Message(role="assistant", content=m.content))
            elif m.role == "tool":
                obs = json.dumps(m.tool_result, ensure_ascii=False)
                msgs.append(
                    Message(role="user", content=f"[observation tool={m.tool_name}] {obs}")
                )
        msgs.append(
            Message(
                role="user",
                content=(
                    "Respond now with ONLY the JSON decision object "
                    "(reply / next_state / actions). No prose, no code fences."
                ),
            )
        )
        return msgs

    # ----------------------------------------------------------- mock path

    def _decide_mock(self, session: CommanderSession) -> Decision:
        """Deterministic fallback when no LLM is available."""
        last_user = next(
            (m.content for m in reversed(session.messages) if m.role == "user"), ""
        )
        text = last_user.lower()

        # Already saw a tool observation this turn? Then summarize and stop.
        if session.messages and session.messages[-1].role == "tool":
            res = session.messages[-1].tool_result or {}
            if res.get("ok") and res.get("run_id"):
                return Decision(
                    reply=(
                        f"已启动 run `{res['run_id']}`(入口:{res.get('entrypoint')})。"
                        "我会监控执行,有节点需要审核时提醒你。"
                    ),
                    next_state="executing",
                )
            return Decision(reply=f"工具结果:{json.dumps(res, ensure_ascii=False)[:300]}")

        # Intent routing heuristics (mock):
        has_idea = any(k in text for k in ["已有", "已经有", "有了想法", "有 idea", "有idea", "假设", "验证"])
        wants_code = any(k in text for k in ["代码", "实现", "coding", "patch"])
        if not last_user:
            return Decision(
                reply="你好,我是 MARS 主控 Agent。告诉我你的研究目标,我来规划并调度 5 个 Agent。",
                next_state="idle",
            )
        if has_idea:
            entry = "experiment"
            note = "检测到你已有想法/假设,跳过 Idea Agent,直接从实验设计进入。"
        elif wants_code:
            entry = "coding"
            note = "检测到你要写代码,从 Coding Agent 进入。"
        else:
            entry = "pipeline"
            note = "走完整 Idea→Experiment→Coding→Execution→Writing 链路。"
        return Decision(
            reply=f"明白。{note}正在启动…",
            next_state="planning",
            actions=[{
                "tool": "create_and_start_run",
                "args": {"entrypoint": entry, "user_request": last_user},
            }],
        )

    # ----------------------------------------------------------- helpers

    def _try_transition(self, session: CommanderSession, target: str) -> None:
        try:
            dst = ConversationState(target)
        except ValueError:
            return
        if can_transition(session.state, dst):
            session.state = dst


def _summarize_result(tool: str, result: dict[str, Any]) -> str:
    if not result.get("ok", True):
        return f"[{tool}] 失败:{result.get('error', 'unknown')}"
    if tool == "create_and_start_run":
        return f"[{tool}] 已启动 {result.get('run_id')} (entry={result.get('entrypoint')})"
    if tool == "get_run_status":
        return f"[{tool}] {json.dumps(result.get('states', {}), ensure_ascii=False)}"
    return f"[{tool}] {json.dumps(result, ensure_ascii=False)[:200]}"


def _parse_decision(text: str) -> Decision:
    raw = _extract_json(text)
    if raw is None:
        # No JSON — treat whole text as a plain reply.
        return Decision(reply=text.strip())
    reply = str(raw.get("reply", "")).strip()
    next_state = raw.get("next_state")
    actions_raw = raw.get("actions", [])
    actions: list[dict[str, Any]] = []
    if isinstance(actions_raw, list):
        for a in actions_raw:
            if isinstance(a, dict) and a.get("tool"):
                actions.append({"tool": str(a["tool"]), "args": a.get("args", {})})
    return Decision(
        reply=reply,
        next_state=str(next_state) if next_state else None,
        actions=actions,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl >= 0:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _system_prompt(session: CommanderSession) -> str:
    targets = (
        json.dumps(session.metric_targets, ensure_ascii=False)
        if session.metric_targets
        else "(未设定)"
    )
    return f"""你是 MARS 研究系统的**主控 Agent (Commander)**。你用中文和研究员对话,理解意图后通过工具调度底层的 5 个领域 Agent(Idea / Experiment / Coding / Execution / Writing)和自愈反馈引擎。

## 你的职责
1. **理解意图 + 智能选入口**:如果用户已经有 idea/假设,就跳过 Idea Agent,直接 entrypoint=experiment;只要代码就 entrypoint=coding;什么都没有就走 pipeline 全链路。**注意:用户用自然语言描述的想法/目标不是 seed_artifact,启动时只传 entrypoint + user_request,让该阶段 Agent 自己起草产物,不要塞 seed_artifact。**
2. **规划并启动**:用 create_and_start_run 启动。启动成功后转 executing 状态,简要告诉用户已启动 + 入口,不要再追问方案细节(Agent 会自己起草)。
3. **监控执行**:用 get_run_status / get_diagnosis 查看进展和自愈引擎的追责结论。
4. **配合自愈循环**:执行结果没达预期时,底层引擎会自动追责(查 coding 还是 experiment 的锅)并拉回重跑。你负责把诊断结论用人话解释给用户,并在半自动模式下征求用户同意。
5. **审核闸口**:节点进入 waiting_review 时提醒用户;用户同意后用 approve_node 放行,或 reject_node 驳回。
6. **汇报**:对照用户设定的指标预期({targets})判断是否达标。

## 当前上下文
- 会话状态(FSM): {session.state.value}
- 关联 run: {session.linked_run_id or "(无)"}
- 介入模式: {"全自动(只汇报)" if session.auto_mode else "半自动(每次拉回前征求同意)"}
- 指标预期: {targets}

## 可用工具
{tools_for_prompt()}

## 会话状态机(next_state 只能取这些)
idle(待命) / clarifying(澄清需求) / planning(规划) / awaiting_confirm(等确认) / executing(执行中) / awaiting_review(等审核) / reporting(汇报)

## 输出协议(严格)
只输出一个 JSON 对象,不要任何额外文字或代码围栏:
{{"reply": "给用户看的中文回复", "next_state": "会话状态(可选)", "actions": [{{"tool": "工具名", "args": {{...}}}}]}}
- 如果只是聊天/澄清/汇报,actions 留空 []。
- 如果要调度,在 actions 里列工具。调完工具后你会收到 [observation ...],据此再决定下一步或给出最终 reply。
- reply 必须是给用户的自然语言,不要把 JSON 或工具名直接念给用户。
"""
