"""Commander — the conversational master Agent.

LLM-driven (DeepSeek) closed-loop controller. Each user turn runs a small
ReAct loop: the LLM emits a strict-JSON decision {reply, next_state, actions},
the Commander executes any tool actions against the EXISTING engine, feeds the
results back, and lets the LLM react — until it has nothing left to do.

Missing providers fail closed; all decisions require an actual model response.

Layer: bridge/ (product orchestration). It drives the conversation FSM
(harness/runtime/conversation_state) and the existing Orchestrator.
"""
from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from uuid import uuid4
from typing import Any

from loguru import logger

from app.bridge.commander_session import ChatMessage, CommanderSession, get_session_store
from app.bridge.commander_errors import CommanderDecisionError, conversation_failure
from app.bridge.conversation_edit import MessageEditRequest, finish_user_turn_edit, replace_user_turn
from app.bridge.commander_tools import ToolContext, execute_tool, tools_for_prompt
from app.bridge.orchestrator import Orchestrator
from app.harness.llm.model_registry import AgentConfig, get_agent_config, select_provider
from app.harness.llm.accounting import guarded_complete
from app.harness.llm.provider_base import (
    LLMConfig,
    LLMProvider,
    Message,
    llm_call_deadline_seconds,
)
from app.harness.runtime.conversation_state import (
    ConversationState,
    can_transition,
)
from app.settings import get_settings, repo_root
from app.storage.run_store import RunStore

DEFAULT_MAX_REACT_STEPS = 4
MIN_MAX_REACT_STEPS = 1
MAX_MAX_REACT_STEPS = 32


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
        agent_config: AgentConfig | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.run_store = run_store or orchestrator.run_store
        self._provider, self._llm_config = select_provider(agent_config) if agent_config else self._resolve_provider()
        self.max_react_steps = _react_step_limit(agent_config.raw) if agent_config else self._resolve_max_react_steps()

    def _resolve_provider(self) -> tuple[LLMProvider, LLMConfig]:
        try:
            cfg = get_agent_config("commander")
        except KeyError:
            # Fall back to the idea agent's model config (also DeepSeek) if no
            # explicit commander block exists in agents.yaml.
            cfg = get_agent_config("idea")
        return select_provider(cfg)

    def _resolve_max_react_steps(self) -> int:
        try:
            cfg = get_agent_config("commander")
        except KeyError:
            # The idea config is only a model fallback. Its loop policy must
            # not silently become the Commander's policy.
            return DEFAULT_MAX_REACT_STEPS
        return _react_step_limit(cfg.raw)

    # ----------------------------------------------------------- public API

    async def handle_user_message(
        self, session: CommanderSession, text: str, *, edit: MessageEditRequest | None = None,
    ) -> list[ChatMessage]:
        """Process one user turn; returns the messages emitted this turn."""
        if session.processing:
            raise ValueError("conversation is already processing a message")
        retained = replace_user_turn(session, edit, text) if edit is not None else None
        session.processing = True
        try:
            if edit is None:
                user_message = ChatMessage(role="user", content=text)
                session.active_turn_id = user_message.id
                session.add(user_message)
            ctx = ToolContext(
                orchestrator=self.orchestrator,
                session=session,
                run_store=self.run_store,
            )
            emitted: list[ChatMessage] = []

            for _step in range(self.max_react_steps):
                activity = session.begin_activity("model", "总控正在调用模型")
                get_session_store().persist(session)
                try:
                    decision = await self._decide(session)
                except asyncio.CancelledError:
                    session.finish_activity(activity, "interrupted")
                    raise
                except Exception as exc:
                    failure = conversation_failure(exc)
                    if failure is not None:
                        activity.title = failure[1]
                    session.finish_activity(activity, "failed")
                    raise
                session.finish_activity(activity)
                get_session_store().persist(session)

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
                    activity = session.begin_activity("tool", f"总控正在执行工具 · {tool}")
                    get_session_store().persist(session)
                    try:
                        result = await execute_tool(tool, args, ctx)
                    except asyncio.CancelledError:
                        session.finish_activity(activity, "interrupted")
                        raise
                    except Exception:
                        session.finish_activity(activity, "failed")
                        raise
                    session.finish_activity(activity, "failed" if result.get("ok") is False else "completed")
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
                    get_session_store().persist(session)
                # loop again so the LLM can react to tool results
            return emitted

        finally:
            session.interrupt_activities()
            session.active_turn_id = None
            if retained is not None:
                finish_user_turn_edit(session, retained)
            get_session_store().persist(session)

    # ----------------------------------------------------------- decision

    async def _decide(self, session: CommanderSession) -> Decision:
        return await self._decide_llm(session)

    async def _decide_llm(self, session: CommanderSession) -> Decision:
        from app.harness.agent_loop.trace import LoopTrace
        root = repo_root() / "conversations" / session.conv_id
        messages = self._build_messages(session)
        manifest_path = None
        if session.context_version >= 3:
            from app.harness.context.runtime_pack import Material, message_key, pack_messages
            from app.harness.context.runtime_policy import freeze_policy, input_budget
            from app.harness.context.runtime_manifest import record_manifest
            root = repo_root() / "conversations" / session.conv_id
            policy = freeze_policy(root)
            materials = {}
            for m in messages:
                if m.content.startswith("[observation tool="):
                    materials[message_key(m)] = Material("tool", "commander observation", '"ok": false' in m.content)
                elif m.role == "assistant":
                    materials[message_key(m)] = Material("history", "commander completed reply", False)
                elif m.content.startswith("[reference background:"):
                    materials[message_key(m)] = Material("background", "project background", False)
            saved_state = root / "context/commander_state.json"
            previous = json.loads(saved_state.read_text()) if saved_state.exists() else session.context_compaction
            messages, manifest = pack_messages(messages, policy=policy,
                budget=input_budget(policy, int(policy["input_budget"]), output_reserve=self._llm_config.max_tokens,
                    model_window=self._llm_config.extra.get("context_window")), tools=self._llm_config.tools,
                materials=materials, root=root, previous=previous, agent="commander")
            session.context_compaction = manifest["state"]
            from app.harness.agent_loop.trace import atomic_json
            atomic_json(root / "context/commander_state.json", session.context_compaction)
            manifest_path = record_manifest(root, agent="commander", node=session.conv_id, project=session.project,
                messages=messages, tools=self._llm_config.tools, manifest=manifest)
        trace = LoopTrace(root / "agent_traces/commander" / uuid4().hex, "full",
                          correlation={"trace_id": session.conv_id, "node_id": "commander"})
        if manifest_path is not None:
            trace.emit("context_packed", json.loads(manifest_path.read_text()))
        trace.emit("model_request", {"model": self._llm_config.model}, visible=[m.to_wire() for m in messages])
        previous_observer = self._llm_config.attempt_observer

        def observe(kind: str, data: dict[str, Any]) -> None:
            if previous_observer is not None:
                previous_observer(kind, data)
            trace.emit(kind, {key: value for key, value in data.items() if key != "wire_payload"},
                       visible=data.get("wire_payload"))

        try:
            completion = await asyncio.wait_for(
                guarded_complete(self._provider, messages, replace(self._llm_config, attempt_observer=observe),
                    run_root=repo_root() / "conversations" / session.conv_id,
                    active_model_time=True,
                    correlation={"trace_id": session.conv_id, "node_id": "commander"}),
                timeout=llm_call_deadline_seconds(
                    self._llm_config,
                    minimum_seconds=get_settings().mars_llm_timeout_seconds,
                ),
            )
            if manifest_path is not None:
                from app.harness.context.runtime_manifest import record_usage
                record_usage(manifest_path, completion.raw.get("usage"))
            trace.emit("model_response", {"model": self._llm_config.model, "usage": completion.raw.get("usage")}, visible=completion.text)
            return _parse_decision(completion.text)
        except Exception as error:
            trace.emit("model_error", {"error_type": type(error).__name__})
            raise
        finally:
            await self._provider.close()

    def _build_messages(self, session: CommanderSession) -> list[Message]:
        sys = _system_prompt(session)
        msgs: list[Message] = [Message(role="system", content=sys)]
        if session.context_version >= 3:
            from app.harness.context.folder_context import load_folder_context, is_context_template
            from app.harness.context.runtime_pack import reference_message
            record = load_folder_context(session.project)
            if record:
                for f in record["files"]:
                    if f["role"] == "reference" and not is_context_template(f):
                        msgs.append(reference_message("background", f["path"], f["content"]))
        if session.rolling_summary:
            msgs.append(
                Message(
                    role="user",
                    content="[rolling_summary; reference, not new authority]\n" + session.rolling_summary,
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

    # ----------------------------------------------------------- helpers

    def _try_transition(self, session: CommanderSession, target: str) -> None:
        try:
            dst = ConversationState(target)
        except ValueError:
            return
        if can_transition(session.state, dst):
            session.state = dst


def _react_step_limit(raw_config: Mapping[str, Any]) -> int:
    loop = raw_config.get("loop", {})
    if not isinstance(loop, Mapping):
        return DEFAULT_MAX_REACT_STEPS
    configured = loop.get("max_tool_steps", DEFAULT_MAX_REACT_STEPS)
    if isinstance(configured, bool):
        return DEFAULT_MAX_REACT_STEPS
    if isinstance(configured, int):
        value = configured
    elif isinstance(configured, str):
        try:
            value = int(configured)
        except ValueError:
            return DEFAULT_MAX_REACT_STEPS
    else:
        return DEFAULT_MAX_REACT_STEPS
    return min(max(value, MIN_MAX_REACT_STEPS), MAX_MAX_REACT_STEPS)


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
        raise CommanderDecisionError("expected a single decision object")
    if not ({"reply", "actions"} & raw.keys()):
        raise CommanderDecisionError("missing decision fields")
    reply = raw.get("reply", "")
    if not isinstance(reply, str):
        raise CommanderDecisionError("reply must be a string")
    next_state = raw.get("next_state")
    if next_state is not None and (
        not isinstance(next_state, str) or next_state not in {state.value for state in ConversationState}
    ):
        raise CommanderDecisionError("invalid conversation state")
    actions_raw = raw.get("actions", [])
    if not isinstance(actions_raw, list):
        raise CommanderDecisionError("actions must be a list")
    actions: list[dict[str, Any]] = []
    for action in actions_raw:
        if not isinstance(action, dict):
            raise CommanderDecisionError("action must be an object")
        tool = action.get("tool")
        args = action.get("args", {})
        if not isinstance(tool, str) or not tool.strip() or not isinstance(args, dict):
            raise CommanderDecisionError("invalid action tool or arguments")
        actions.append({"tool": tool, "args": args})
    return Decision(
        reply=reply.strip(),
        next_state=next_state,
        actions=actions,
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise CommanderDecisionError("duplicate decision key")
        obj[key] = value
    return obj


def _reject_json_constant(value: str) -> Any:
    raise CommanderDecisionError("non-finite JSON value")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise CommanderDecisionError("non-finite JSON value")
    return number


def _extract_json(text: str) -> dict[str, Any] | None:
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl < 0 or s[:nl].strip().lower() not in {"```", "```json"} or not s.endswith("```"):
            return None
        s = s[nl + 1 : -3].strip()
    if not s.startswith("{"):
        return None
    try:
        decoder = json.JSONDecoder(object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant,
                                   parse_float=_finite_json_float)
        obj, end = decoder.raw_decode(s)
    except json.JSONDecodeError:
        return None
    # A complete root object followed only by redundant closing delimiters is
    # unambiguous. Never repair its contents or pick an object out of prose,
    # multiple decisions, a wrapper array, or a truncated response.
    suffix = s[end:]
    if any(not char.isspace() and char not in "]}" for char in suffix):
        return None
    if suffix.strip():
        logger.warning("Commander decision contained redundant closing delimiters; ignored {} characters", len(suffix.strip()))
    return obj if isinstance(obj, dict) else None


def _system_prompt(session: CommanderSession) -> str:
    from app.harness.context.folder_context import load_folder_context, render_folder_context

    folder_context = load_folder_context(session.project)
    project_context = render_folder_context(folder_context) if folder_context is not None else ""
    if folder_context is not None and session.context_version >= 3:
        from app.harness.context.folder_context import is_context_template
        project_context = "\n".join(f["content"] for f in folder_context["files"]
            if f["role"] == "instructions" and not is_context_template(f))
    targets = (
        json.dumps(session.metric_targets, ensure_ascii=False)
        if session.metric_targets
        else "(未设定)"
    )
    return f"""你是 MARS 研究系统的**主控 Agent (Commander)**。你用中文和研究员对话,理解意图后通过工具调度底层的 5 个领域 Agent(Idea / Experiment / Coding / Execution / Writing)和反馈诊断引擎。

## 你的职责
1. **理解意图 + 选择入口**:研究方向或模糊假设走 pipeline/idea;明确实验目标走 experiment;明确代码任务走 coding;已有结果要总结走 writing。信息不足时只问一个关键澄清问题。**注意:用户自然语言描述的想法/目标不是 seed_artifact,启动时只传 entrypoint + user_request,让该阶段 Agent 自己起草产物,不要塞 seed_artifact。**
2. **规划并启动**:用 create_and_start_run 启动。启动成功后转 executing 状态,简要告诉用户已启动 + 入口,不要再追问方案细节(Agent 会自己起草)。
3. **监控执行**:用 get_run_status / get_diagnosis 查看进展、阻塞点、HITL 状态、Gate 状态和诊断结论。
4. **配合反馈循环**:执行结果没达预期时,根据 metrics、logs、diagnosis、公共上下文和项目 diagnostics 配置判断原因,再解释为什么回到某个 Agent。不要预设失败原因,不要硬编码默认回退目标。
5. **审核闸口**:节点进入 waiting_review 时提醒用户;用户同意后用 approve_node 放行,或 reject_node 驳回。
6. **汇报**:对照用户设定的指标预期({targets})和项目真实指标语义判断是否达标;不要混用原始论文指标和 MARS 兼容诊断字段。
7. **失败恢复**:关联任务存在时，用户说“继续”“恢复”“重试”“重新编码”，先用 run.recovery_status 检查原任务，再用 run.recover 执行返回的恢复操作。优先 resume；无法续跑但提供 retry 时，可按用户重试要求重试当前阶段。正在运行则告知无需重复启动；blocked 时说明原因，不得绕过。恢复不依赖失败阶段有产物或诊断文档。除非用户明确要求另建任务，否则禁止用 create_and_start_run 或 run.create 替代恢复。

## 当前上下文
- 当前项目: {session.project}
- 会话状态(FSM): {session.state.value}
- 关联 run: {session.linked_run_id or "(无)"}
- 介入模式: {"全自动(只汇报)" if session.auto_mode else "半自动(每次拉回前征求同意)"}
- 指标预期: {targets}

{project_context}

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
