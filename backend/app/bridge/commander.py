"""Commander — the conversational master Agent.

LLM-driven (DeepSeek) closed-loop controller. Each user turn runs a small
ReAct loop: the LLM proposes decisions and the Commander executes real tools.
A per-turn completion ledger distinguishes an answer from an unfinished action;
only actual receipts can certify effects. Optional model metadata is advisory.

Missing providers fail closed; all decisions require an actual model response.

Layer: bridge/ (product orchestration). It drives the conversation FSM
(harness/runtime/conversation_state) and the existing Orchestrator.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

from loguru import logger

from app.bridge.commander_session import ChatMessage, CommanderSession, get_session_store
from app.bridge.commander_errors import CommanderDecisionError, conversation_failure, require_native_decision
from app.bridge.commander_completion import (
    CompletionVerdict, TurnGoal, admitted_existing_model_goal, assess_completion, goal_state, parse_turn_goal, proposed_action_goal, receipt_intervention,
)
from app.bridge.commander_dispatch import (
    RUN_CREATION_TOOLS, batch_created_identity, batch_start_is_bound,
    coding_handoff_clarification, ordinary_coding_handoff_actions, promised_source_read_action,
    PREPARING_NOTICE, dispatch_failure_reason, existing_model_fallback_action, existing_run_recovery_guard, has_run_dispatch,
    retained_dispatch_failure_reply, retained_dispatch_run_id, run_dispatch_receipt, stale_executing_without_run,
    pending_existing_admission, truthful_unstarted_reply, unfulfilled_run_dispatch,
)
from app.bridge.conversation_edit import MessageEditRequest, finish_user_turn_edit, replace_user_turn
from app.bridge.commander_tools import ToolContext, commander_tool_mutates, execute_tool, tools_for_prompt
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
    response_mode: str = "structured"
    goal: dict[str, Any] | None = None


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
        if stale_executing_without_run(session):
            self._try_transition(session, "idle")
        retained = replace_user_turn(session, edit, text) if edit is not None else None
        from app.bridge.commander_recovery import recover_interrupted_model_requests
        from app.harness.persistence import path_lock
        root = get_session_store().session_directory(session)
        with path_lock(root / '.action_journal.lock'):
            model_recovery = recover_interrupted_model_requests(root, session)
        session.processing = True
        try:
            from app.bridge.commander_journal import restore_action_receipts
            restore_action_receipts(get_session_store().session_directory(session), session)
            if edit is None:
                user_message = ChatMessage(role="user", content=text)
                session.active_turn_id = user_message.id
                session.add(user_message)
            session.completion_state = _begin_turn_completion(session)
            if model_recovery['status'] == 'blocked':
                recovery_reply = session.add(ChatMessage(role='assistant', content=str(model_recovery['message'])))
                _store_turn_completion(session, None, CompletionVerdict(status='blocked', reason=recovery_reply.content))
                return [recovery_reply]
            ctx = ToolContext(
                orchestrator=self.orchestrator,
                session=session,
                run_store=self.run_store,
            )
            emitted: list[ChatMessage] = []
            continuation_prompt: str | None = None
            continuation_evidence: set[str] = set()
            goal: TurnGoal | None = None
            completion_reviews: dict[str, Any] = {}
            last_completion: CompletionVerdict | None = None
            pending_correction: dict[str, Any] | None = None

            for _step in range(self.max_react_steps):
                terminal_receipt = False
                pending_action = pending_correction or (pending_existing_admission(session) if _step == 0 else None)
                pending_correction = None
                if pending_action is not None:
                    # Restore the same explicit never-created request without
                    # asking a model to choose a creation tool or wording again.
                    goal = admitted_existing_model_goal(session, pending_action)
                    if goal is not None:
                        _store_turn_completion(session, goal, CompletionVerdict(
                            status="pending", reason="恢复已准入的原始请求，等待真实执行结果",
                            missing=goal.required_effects))
                    decision = Decision(actions=[pending_action])
                else:
                    activity = session.begin_activity("model", "总控正在调用模型")
                    get_session_store().persist(session)
                    try:
                        decision = await self._decide(session, continuation=continuation_prompt)
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
                    continuation_prompt = None
                if goal is None:
                    goal = _decision_goal(session, decision)
                    if goal is not None:
                        _store_turn_completion(session, goal, CompletionVerdict(
                            status="pending", reason="本轮目标已识别，等待真实执行结果",
                            missing=goal.required_effects))
                        get_session_store().persist(session)
                decision = _prevent_duplicate_creation(session, decision)
                recovery = existing_run_recovery_guard(session, decision.actions)
                if recovery is not None:
                    decision = Decision(actions=[recovery])
                if (len(decision.actions) == 1 and decision.actions[0].get("tool") in {
                        "run.create", "create_and_start_run"}
                        and isinstance(decision.actions[0].get("args"), dict)
                        and decision.actions[0]["args"].get("entrypoint") == "experiment"):
                    # The model cannot turn the user's fixed parameter change
                    # into permission to design a comparison protocol. This
                    # existing handoff still requires a named current model,
                    # a real source read, and no user request for research or
                    # choosing search dimensions; all ordinary reviews remain.
                    handoff = ordinary_coding_handoff_actions(session, decision.reply,
                        response_mode=decision.response_mode)
                    if handoff is not None:
                        decision = Decision(actions=handoff, response_mode="ordinary_coding_handoff")
                if decision.actions and all(action.get("tool") in {
                        "code.repo_list", "code.repo_search", "code.repo_read_lines"} for action in decision.actions):
                    # Once a real configuration read identifies the requested
                    # model, Coding owns further implementation inspection.
                    # Ambiguous real models require a concrete choice instead
                    # of another round of guessed repository directories.
                    handoff = ordinary_coding_handoff_actions(session, decision.reply,
                        response_mode=decision.response_mode)
                    question = coding_handoff_clarification(session, decision.reply,
                        response_mode=decision.response_mode) if handoff is None else None
                    if handoff is not None:
                        decision = Decision(actions=handoff, response_mode="ordinary_coding_handoff")
                    elif question is not None:
                        decision = Decision(reply=question, response_mode="clarification")
                if not decision.actions and decision.response_mode != "clarification":
                    fallback = existing_model_fallback_action(session)
                    if fallback is not None:
                        # A promise to inspect is not the inspection. Dispatch
                        # one real bounded read, retaining the original request.
                        decision = Decision(actions=[fallback])
                    else:
                        from app.bridge.commander_code import routing_conclusion_needs_source
                        if routing_conclusion_needs_source(session):
                            # Execute a real admission tool and let the model react;
                            # never publish an unsupported absence/routing conclusion.
                            decision = Decision(actions=[{'tool': 'routing.repository_check', 'args': {}}])

                if not decision.actions:
                    goal, last_completion, question = await self._completion_gate(
                        session, goal, decision.reply, completion_reviews)
                    if last_completion.status in {"pending", "unknown"}:
                        handoff = ordinary_coding_handoff_actions(session, decision.reply,
                            response_mode=decision.response_mode)
                        question = coding_handoff_clarification(session, decision.reply,
                            response_mode=decision.response_mode) if handoff is None else None
                        source_read = promised_source_read_action(session, decision.reply,
                            response_mode=decision.response_mode) if handoff is None and question is None else None
                        if handoff is not None:
                            decision = Decision(actions=handoff, response_mode="ordinary_coding_handoff")
                        elif question is not None:
                            decision = Decision(reply=question, response_mode="clarification")
                            last_completion = CompletionVerdict(status="needs_user", reason=question,
                                evidence_ids=last_completion.evidence_ids, missing=last_completion.missing)
                        elif source_read is not None:
                            decision = Decision(actions=[source_read])
                        elif (_completion_fingerprint(session, goal) not in continuation_evidence
                                and _step + 1 < self.max_react_steps):
                            continuation_evidence.add(_completion_fingerprint(session, goal))
                            continuation_prompt = _completion_prompt(session, goal, last_completion)
                            logger.info("Commander continues unfinished action turn={} step={} reason={}",
                                        session.active_turn_id, _step, last_completion.reason)
                            continue
                        else:
                            decision = Decision(reply=_completion_unfinished_reply(last_completion))
                    elif last_completion.status == "needs_user" and question:
                        decision = Decision(reply=question, response_mode="clarification")
                    elif last_completion.status == "blocked":
                        decision = Decision(reply=_completion_unfinished_reply(last_completion))
                    elif last_completion.status == "delegated":
                        decision = Decision(reply=_completion_unfinished_reply(last_completion))

                if decision.actions:
                    from app.bridge.commander_code import creation_needs_source, creation_source_preparation
                    preparation = creation_source_preparation(session, decision.actions[0])
                    if preparation is not None:
                        # Dispatch the prerequisite, not a creation that is
                        # known to fail. The next native decision consumes the
                        # real read/list result and chooses its route again.
                        decision = Decision(actions=[preparation], response_mode='repository_preparation',
                            reply='MARS 先核对当前代码；读取结果后再创建任务。')
                    elif creation_needs_source(session, decision.actions[0]):
                        # Do not call a mutation whose prerequisite is known
                        # missing. No observation or creation receipt is forged.
                        continuation_prompt = ('创建尚未执行：本轮还没有读取任务相关源码。'
                            '请从已浏览/搜索到的真实路径选择相关文件，调用 code.repo_read_lines；'
                            '读取后重新判断入口。此前对话的读取不能替代本轮实际读取。')
                        continue
                dispatching_run = has_run_dispatch(decision.actions)
                # Only a real dispatch receipt can enter executing. A model's
                # suggested state cannot certify that an operation happened.
                if decision.next_state and decision.next_state != "executing":
                    self._try_transition(session, decision.next_state)

                reply = PREPARING_NOTICE if dispatching_run else decision.reply
                if not decision.actions and unfulfilled_run_dispatch(session):
                    reply = truthful_unstarted_reply(reply, retained_run_id=retained_dispatch_run_id(session),
                                                     failure_reason=dispatch_failure_reason(session))
                if reply:
                    emitted.append(session.add(ChatMessage(role="assistant", content=reply)))

                if not decision.actions:
                    break

                handoff_run_id: str | None = None
                batch_initial_run_id = session.linked_run_id
                batch_creates = any(action.get('tool') in RUN_CREATION_TOOLS for action in decision.actions)
                for action in decision.actions:
                    # Earlier actions in this same batch may already have
                    # allocated a task or performed this exact mutation.
                    if not _prevent_duplicate_creation(session, Decision(actions=[action])).actions:
                        continue
                    # Native batches may search and then try to create without
                    # reading. Recheck after preceding real observations too;
                    # dependent start calls must wait for the next decision.
                    from app.bridge.commander_code import creation_needs_source, creation_source_preparation
                    prepared_action = creation_source_preparation(session, action)
                    if prepared_action is not None:
                        action = prepared_action
                    elif creation_needs_source(session, action):
                        continuation_prompt = ('本批后续创建和启动尚未执行：需要本轮实际源码读取。'
                            '请根据真实浏览和搜索结果调用 code.repo_read_lines，随后重新判断入口。')
                        break
                    tool = str(action.get("tool", ""))
                    args = action.get("args", {}) or {}
                    if not isinstance(args, dict):
                        args = {}
                    if (batch_creates and tool == 'run.start' and not batch_start_is_bound(
                            session, args, created_run_id=handoff_run_id)):
                        emitted.append(session.add(ChatMessage(role="assistant", content=truthful_unstarted_reply(
                            "", failure_reason="未取得与当前会话一致的新任务创建回执，未派发启动。"))))
                        terminal_receipt = True
                        break
                    activity = session.begin_activity("tool", f"总控正在执行工具 · {tool}")
                    get_session_store().persist(session)
                    from app.bridge.commander_journal import (begin_action, complete_action,
                        repeated_failure, unresolved_write)
                    journal_root = get_session_store().session_directory(session)
                    failed = repeated_failure(session, tool, args)
                    unknown = unresolved_write(journal_root, tool, args, session)
                    journal_id: str | None = None
                    result: dict[str, Any]
                    try:
                        if unknown is not None:
                            result = {"ok": False, "status": "action_reconciliation_required", "retryable": False,
                                "requires_user_input": True, "error": "上次操作在中断前没有取得完整回执；请先核对已有任务，MARS不会重复执行未知结果的修改。"}
                        elif failed is not None:
                            result = {"ok": False, "status": "repeated_action_skipped", "retryable": False,
                                "requires_user_input": bool(failed.get('requires_user_input', True)),
                                "error": str(failed.get('error') or '相同调用未取得新证据，已停止重复执行。')}
                        else:
                            journal_id = begin_action(journal_root, session, tool, args,
                                mutation=commander_tool_mutates(tool))
                            result = await execute_tool(tool, args, ctx)
                            complete_action(journal_root, journal_id, result)
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
                                id=journal_id or uuid4().hex,
                                role="tool",
                                content=_summarize_result(tool, result),
                                tool_name=tool,
                                tool_args=args,
                                tool_result=result,
                            )
                        )
                    )
                    get_session_store().persist(session)
                    if result.get('ok') is False and result.get('retryable') is False:
                        details = result.get('details', {})
                        expected = details.get('expected_budget') if isinstance(details, dict) else None
                        if (tool == 'run.existing_model' and result.get('error_code') == 'budget_mismatch'
                                and isinstance(expected, dict) and type(expected.get('max_iters')) is int
                                and expected.get('budget_unit') in {'steps', 'epochs'} and failed is None):
                            # Exact values come from the actual user's validated
                            # request, not a second model guess or unit conversion.
                            pending_correction = {'tool': tool, 'args': {**args, **expected}}
                            break
                        emitted.append(session.add(ChatMessage(role='assistant', content=str(
                            result.get('error') or '本次调用没有完成，请核对保留的实际进度。'))))
                        last_completion = CompletionVerdict(status='needs_user' if result.get('requires_user_input')
                            else 'blocked', reason=emitted[-1].content)
                        terminal_receipt = True
                        break
                    if prepared_action is not None:
                        break
                    if tool in RUN_CREATION_TOOLS:
                        handoff_run_id = batch_created_identity(session, tool, result,
                            initial_run_id=batch_initial_run_id)
                    if (decision.response_mode == "ordinary_coding_handoff"
                            and (result.get("ok") is not True or tool == "run.create" and handoff_run_id is None)):
                        # Creation and start are dependent operations. A failed
                        # allocation must never start another or an old task.
                        emitted.append(session.add(ChatMessage(role="assistant", content=truthful_unstarted_reply(
                            "", retained_run_id=result.get("run_id"), failure_reason=str(result.get("error") or "")))))
                        terminal_receipt = True
                        break
                    if tool in RUN_CREATION_TOOLS and handoff_run_id is None:
                        # A failed admission may be repaired by the next model
                        # decision. Its dependent actions cannot run now, even
                        # if an older task remains linked to this conversation.
                        break
                    if receipt_intervention(result) is not None:
                        # Real choices and review boundaries stop this batch;
                        # a later authored action cannot select or approve them.
                        terminal_receipt = True
                        break
                    receipt = run_dispatch_receipt(tool, result)
                    retained_failure = retained_dispatch_failure_reply(tool, result)
                    if receipt is not None:
                        if receipt.executing:
                            self._try_transition(session, "executing")
                        emitted.append(session.add(ChatMessage(role="assistant", content=receipt.reply)))
                        get_session_store().persist(session)
                        terminal_receipt = True
                        if _receipt_ends_action_batch(tool, len(decision.actions)):
                            break
                    elif retained_failure is not None:
                        # The task exists even though starting it failed. End
                        # admission here so the next model cannot create it again.
                        emitted.append(session.add(ChatMessage(role="assistant", content=retained_failure)))
                        get_session_store().persist(session)
                        terminal_receipt = True
                        break
                if pending_correction is not None:
                    continue
                if terminal_receipt and last_completion is not None and last_completion.status in {'blocked', 'needs_user'}:
                    break
                batch_completion = (assess_completion(session, goal, run_store=self.run_store)
                    if not terminal_receipt and goal is not None and goal.intent == "action" else None)
                if batch_completion is not None and batch_completion.status in {"fulfilled", "delegated"}:
                    # Finished mutations must not require another model reply;
                    # its optional format could otherwise obscure real success.
                    terminal_receipt = True
                    emitted.append(session.add(ChatMessage(role="assistant", content=(
                        _completion_unfinished_reply(batch_completion) if batch_completion.status == "delegated"
                        else "本轮操作已完成，实际结果已保留。"))))
                if terminal_receipt:
                    goal, last_completion, question = await self._completion_gate(
                        session, goal, emitted[-1].content if emitted else "", completion_reviews)
                    if last_completion.status in {"pending", "unknown"} and _step + 1 < self.max_react_steps:
                        continuation_prompt = _completion_prompt(session, goal, last_completion)
                        continue
                    if last_completion.status in {"pending", "unknown", "blocked"}:
                        emitted.append(session.add(ChatMessage(role="assistant",
                            content=_completion_unfinished_reply(last_completion))))
                    elif last_completion.status == "needs_user" and question:
                        emitted.append(session.add(ChatMessage(role="assistant", content=question)))
                    elif last_completion.status == "delegated":
                        emitted.append(session.add(ChatMessage(role="assistant",
                            content=_completion_unfinished_reply(last_completion))))
                    break
                # loop again so the LLM can react to tool results
            if emitted and emitted[-1].role == "tool":
                goal, last_completion, question = await self._completion_gate(
                    session, goal, "", completion_reviews)
                if last_completion.status not in {"fulfilled", "delegated"}:
                    emitted.append(session.add(ChatMessage(role="assistant",
                        content=question if last_completion.status == "needs_user" and question
                        else _completion_unfinished_reply(last_completion))))
                elif last_completion.status == "delegated":
                    emitted.append(session.add(ChatMessage(role="assistant",
                        content=_completion_unfinished_reply(last_completion))))
                else:
                    emitted.append(session.add(ChatMessage(role="assistant", content="本轮操作已完成，实际结果已保留。")))
            if last_completion is not None:
                _store_turn_completion(session, goal, last_completion)
                if last_completion.status == "needs_user":
                    self._try_transition(session, "clarifying")
                elif last_completion.status in {"pending", "unknown"}:
                    self._try_transition(session, "planning")
            return emitted

        except Exception as error:
            failure = conversation_failure(error)
            if failure is not None:
                _store_turn_completion(session, goal, CompletionVerdict(status="blocked", reason=failure[1]))
            raise
        finally:
            session.interrupt_activities()
            session.active_turn_id = None
            if retained is not None:
                finish_user_turn_edit(session, retained)
            get_session_store().persist(session)

    async def _completion_gate(
        self, session: CommanderSession, goal: TurnGoal | None, reply: str,
        reviews: dict[str, Any],
    ) -> tuple[TurnGoal | None, CompletionVerdict, str]:
        """All exits use user intent and actual receipts, never a trigger word."""
        verdict = (assess_completion(session, goal, reply=reply, run_store=self.run_store)
                   if goal is not None else CompletionVerdict(
                       status="unknown", reason="本轮目标尚未核对", evidence_ids=(), missing=()))
        question = ""
        # A real effect satisfies its declared obligation without another model
        # call. Missing intent, conversational answers and proposed questions
        # receive one semantic review per distinct evidence/reply state.
        grounded_handoff = bool(goal is not None and goal.intent == "action"
                                and verdict.status in {"fulfilled", "delegated"})
        if verdict.status not in {"blocked", "needs_user"} and not grounded_handoff:
            from app.bridge.commander_semantics import review_turn_completion
            from app.bridge.commander_native import commander_call_deadline_seconds
            key = hashlib.sha256((_completion_fingerprint(session, goal) + reply).encode()).hexdigest()
            review = reviews.get(key)
            if review is None:
                activity = session.begin_activity("model", "总控正在核对本轮请求是否完成")
                get_session_store().persist(session)
                try:
                    review = await review_turn_completion(
                        session, reply, provider=self._provider, llm_config=self._llm_config,
                        run_root=get_session_store().session_directory(session),
                        timeout_seconds=commander_call_deadline_seconds(self._llm_config,
                            minimum_seconds=get_settings().mars_llm_timeout_seconds))
                except BaseException:
                    session.finish_activity(activity, "failed")
                    raise
                session.finish_activity(activity)
                reviews[key] = review
            reviewed_goal = parse_turn_goal(review.goal_raw, session)
            if goal is None or goal.intent in {"answer", "observe", "unknown"}:
                goal = reviewed_goal or goal
            elif reviewed_goal is not None and reviewed_goal.intent == "action":
                # A later semantic check may find a previously omitted user
                # requirement, but cannot downgrade an outstanding operation.
                goal = replace(goal,
                    required_effects=tuple(dict.fromkeys((*goal.required_effects, *reviewed_goal.required_effects))),
                    expected_tools=tuple(dict.fromkeys((*goal.expected_tools, *reviewed_goal.expected_tools))))
            question = review.question
            semantic_status = review.status
            if semantic_status == "needs_user" and not question.strip():
                semantic_status = "unknown"
            verdict = (assess_completion(session, goal, reply=question or reply,
                        semantic_status=semantic_status, evidence_ids=review.evidence_ids,
                        run_store=self.run_store) if goal is not None else CompletionVerdict(
                            status="unknown", reason="尚未取得可核验的完成依据", evidence_ids=(), missing=()))
        if verdict.status == "needs_user" and not question.strip():
            question = _receipt_question(session, verdict)
        _store_turn_completion(session, goal, verdict)
        get_session_store().persist(session)
        return goal, verdict, question

    # ----------------------------------------------------------- decision

    async def _decide(self, session: CommanderSession, *, continuation: str | None = None) -> Decision:
        goal = session.completion_state.get("goal")
        action_pending = (isinstance(goal, dict) and goal.get("intent") == "action"
                          and session.completion_state.get("status") == "pending")
        # Conversation may use normal text; operations use the provider's native
        # function channel. No text JSON or promise is dispatched as an action.
        from app.bridge.commander_native import native_commander_decision, commander_call_deadline_seconds
        raw = await native_commander_decision(
            session, self._build_messages(session, continuation=continuation),
            self._provider, self._llm_config, get_session_store().session_directory(session),
            commander_call_deadline_seconds(self._llm_config,
                minimum_seconds=get_settings().mars_llm_timeout_seconds),
            accept_text_reply=not action_pending)
        require_native_decision(raw)
        decision = _parse_decision(json.dumps({
            "actions": raw.get("actions", []), "reply": raw.get("reply", "")}, ensure_ascii=False))
        if raw.get("native_status") == "question":
            decision.response_mode = "clarification"
        return decision

    async def _decide_llm(self, session: CommanderSession, *, continuation: str | None = None) -> Decision:
        from app.harness.agent_loop.trace import LoopTrace
        root = get_session_store().session_directory(session)
        messages = self._build_messages(session, continuation=continuation)
        manifest_path = None
        if session.context_version >= 3:
            from app.harness.context.runtime_pack import pack_messages
            from app.bridge.commander_context import commander_materials
            from app.harness.context.runtime_policy import freeze_policy, input_budget
            from app.harness.context.runtime_manifest import record_manifest
            root = get_session_store().session_directory(session)
            policy = freeze_policy(root)
            materials = commander_materials(messages)
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
        previous_observer = self._llm_config.attempt_observer

        def observe(kind: str, data: dict[str, Any]) -> None:
            if previous_observer is not None:
                previous_observer(kind, data)
            trace.emit(kind, {key: value for key, value in data.items() if key != "wire_payload"},
                       visible=data.get("wire_payload"))

        try:
            deadline = asyncio.get_running_loop().time() + llm_call_deadline_seconds(
                self._llm_config, minimum_seconds=get_settings().mars_llm_timeout_seconds)
            for response_attempt in range(2):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError('Commander response time budget exhausted before dispatch')
                trace.emit('model_request', {'model': self._llm_config.model,
                    'response_attempt': response_attempt}, visible=[m.to_wire() for m in messages])
                completion = await asyncio.wait_for(
                    guarded_complete(self._provider, messages, replace(self._llm_config, attempt_observer=observe),
                        run_root=root, active_model_time=True,
                        correlation={'trace_id': session.conv_id, 'node_id': 'commander'}),
                    timeout=remaining)
                if manifest_path is not None:
                    from app.harness.context.runtime_manifest import record_usage
                    record_usage(manifest_path, completion.raw.get('usage'))
                trace.emit('model_response', {'model': self._llm_config.model, 'usage': completion.raw.get('usage'),
                    'response_attempt': response_attempt}, visible=completion.text)
                decision = _decision_from_response(completion.text)
                if decision.response_mode == 'clarification' and not response_attempt:
                    trace.emit('decision_response_correction', {'response_sha256':
                        hashlib.sha256(completion.text.encode()).hexdigest(), 'actions_executed': 0})
                    messages = [*messages, Message(role='assistant', content=completion.text), Message(role='user', content=(
                        '上条回复尚未派发任何操作。请依据原始用户请求和已有Observation继续，'
                        '只给出尚未完成的操作，不要重放已经成功的工具。普通问答可以直接自然语言回复；'
                        '需要操作时给出完整工具名和args，不猜缺失参数或宣称未执行的任务已完成。'))]
                    if manifest_path is not None:
                        # The correction is a genuine second request with its
                        # own packed context/manifest and normal resource ledger.
                        materials = commander_materials(messages)
                        messages, manifest = pack_messages(messages, policy=policy,
                            budget=input_budget(policy, int(policy['input_budget']),
                                output_reserve=self._llm_config.max_tokens,
                                model_window=self._llm_config.extra.get('context_window')),
                            tools=self._llm_config.tools, materials=materials, root=root,
                            previous=session.context_compaction, agent='commander')
                        session.context_compaction = manifest['state']
                        atomic_json(root / 'context/commander_state.json', session.context_compaction)
                        manifest_path = record_manifest(root, agent='commander', node=session.conv_id,
                            project=session.project, messages=messages, tools=self._llm_config.tools, manifest=manifest)
                        trace.emit('context_packed', json.loads(manifest_path.read_text()))
                    continue
                if decision.response_mode != 'structured':
                    trace.emit('decision_text_response', {'mode': decision.response_mode, 'actions': 0})
                return decision
            raise RuntimeError('Commander response correction did not finish')
        except Exception as error:
            trace.emit("model_error", {"error_type": type(error).__name__})
            raise
        finally:
            await self._provider.close()

    def _build_messages(self, session: CommanderSession, *, continuation: str | None = None) -> list[Message]:
        sys = _system_prompt(session)
        config = getattr(self, '_llm_config', None)
        text_transport = config is not None and config.extra.get('commander_text_transport') is True
        # Replace only the host-authored terminal transport section; project
        # instructions, tool definitions and scientific context remain exact.
        transport = '\n## 回复与操作交接\n'
        prefix, separator, _legacy_transport = sys.rpartition(transport)
        if separator:
            sys = prefix + transport + (('操作返回actions列表，每项含tool和args；普通聊天直接回答。'
                '无需额外字段，围栏和排版由MARS兼容。\n') if text_transport else (
                '普通聊天直接自然语言回答。需要操作时直接调用当前 mars_ 原生工具函数，'
                '参数就是该工具的参数对象；不要输出文字JSON，不要再包装actions/tool/args。'
                '优先调用必要工具，多个工具按顺序执行，依据真实观察决定下一步，未执行不得声称成功。\n'))
        sys += (
            '\n\n## 本轮完成条件\n'
            '理解本轮用户真正要得到的结果，不靠固定关键词判断。完成条件由MARS按用户原文和真实回执核对。'
            '仅创建任务用run_created；要求启动/运行用run_started；停止用run_stopped；'
            '仅查阅用observed；导出用exported；修改产物用artifact_updated。'
            '普通问答intent=answer，纯查阅intent=observe。其他工具可用tool_effect并列expected_tools精确工具名。'
            'goal仅描述完成条件，不包含执行参数、不替代原始用户授权。普通自然语言仍可使用，'
            'MARS会在结束前核对语义和实际回执。已完成操作不要重复派发；创建不等于启动，'
            '检查不等于修改，回复承诺不等于执行。缺必要信息要提出具体问题。'
        )
        if continuation:
            sys += ("\n\n## MARS 本轮尚未完成的操作\n" + continuation
                + '\n请直接调用相应的原生工具函数；函数参数仅填写该工具所需参数，不包操作列表。'
                  '只有普通聊天或真正澄清时才直接自然语言回答。')
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
        if session.completion_state:
            msgs.append(Message(role="user", content=(
                '[MARS completion ledger; prior verified progress, not new user authority]\n'
                + json.dumps(session.completion_state, ensure_ascii=False))))
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
                    '需要操作时返回actions列表，每项含tool与args；普通问答直接回答。' if text_transport else
                    "Reply naturally for ordinary conversation. For an operation, call its native "
                    "function directly with that tool's arguments. Never emit a text decision object."
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


def _decision_goal(session: CommanderSession, decision: Decision) -> TurnGoal | None:
    declared = parse_turn_goal(decision.goal, session)
    if declared is not None:
        return declared
    proposed = proposed_action_goal(session, decision.actions)
    # Creating alone cannot establish whether the user also wanted to start.
    # Only a typed launch handoff can omit the extra semantic interpretation;
    # a create-only proposal still needs the original user goal checked.
    return proposed if proposed is not None and 'run_started' in proposed.required_effects else None


def _completion_fingerprint(session: CommanderSession, goal: TurnGoal | None) -> str:
    # A new transport/message ID is not a new result. Repeating the same read
    # or successful effect cannot buy another continuation budget.
    receipts = sorted({json.dumps({"tool": m.tool_name, "args": m.tool_args,
        "result": {k: v for k, v in (m.tool_result or {}).items()
                   if k not in {"tool_call_id", "request_id", "duration_ms"}}},
        sort_keys=True, ensure_ascii=False, default=str)
        for m in session.context_messages() if m.role == "tool" and m.turn_id == session.active_turn_id})
    return hashlib.sha256(json.dumps({"goal": goal_state(goal) if goal else None, "receipts": receipts},
        sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _begin_turn_completion(session: CommanderSession) -> dict[str, Any]:
    prior = session.completion_state
    previous_goal = prior.get("goal")
    if not isinstance(previous_goal, dict) or previous_goal.get("intent") == "unknown":
        previous_goal = prior.get("previous_goal")
        previous_status = prior.get("previous_status")
    else:
        previous_status = prior.get("status")
    return {
        "schema": "commander.turn_completion.v1", "turn_id": session.active_turn_id,
        "goal": None, "status": "unknown", "reason": "本轮请求处理中",
        "initial_linked_run_id": session.linked_run_id,
        "previous_goal": previous_goal, "previous_status": previous_status,
    }


def _store_turn_completion(session: CommanderSession, goal: TurnGoal | None,
                           verdict: CompletionVerdict) -> None:
    previous = session.completion_state
    session.completion_state = {
        "schema": "commander.turn_completion.v1", "turn_id": session.active_turn_id,
        "goal": goal_state(goal) if goal is not None else None,
        "status": verdict.status, "reason": verdict.reason,
        "evidence_ids": list(verdict.evidence_ids), "missing": list(verdict.missing),
        "linked_run_id": session.linked_run_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if "initial_linked_run_id" in previous:
        session.completion_state["initial_linked_run_id"] = previous["initial_linked_run_id"]
    # Earlier intent remains reference data when this interpretation is absent
    # or unknown; it never grants new execution permission.
    session.completion_state.update({key: previous[key] for key in ("previous_goal", "previous_status")
                                     if key in previous})


def _completion_prompt(session: CommanderSession, goal: TurnGoal | None, verdict: CompletionVerdict) -> str:
    return (
        'MARS尚未取得本轮用户目标的完成证据。请继续原始用户真正要求的操作，'
        '不要只给下一步说明；不要猜测科学参数、扩大修改范围或绕过审核。'
        '实际完成的操作不得重放，已创建任务只处理同一个run。'
        '若确实缺少必要信息，提出具体问题；真实审批或错误请如实说明。\n'
        + json.dumps({"goal": goal_state(goal) if goal else None, "missing": list(verdict.missing),
                      "reason": verdict.reason, "linked_run_id": session.linked_run_id}, ensure_ascii=False)
    )


def _receipt_question(session: CommanderSession, verdict: CompletionVerdict) -> str:
    for message in reversed(session.context_messages()):
        if message.id not in verdict.evidence_ids:
            continue
        result = message.tool_result or {}
        rows = result.get('choices') or result.get('candidates') or result.get('options')
        if isinstance(rows, list) and rows:
            labels = []
            for row in rows[:8]:
                if isinstance(row, dict):
                    label = row.get('config_path') or row.get('model_name') or row.get('name') or row.get('id')
                else:
                    label = row if isinstance(row, str) else None
                if isinstance(label, str) and label.strip():
                    labels.append(label)
            if labels:
                return '找到多个实际候选项，请确认使用哪一个：' + '；'.join(labels) + '。'
    return '本轮操作需要你确认必要的候选项，实际查询结果已保留。请确认后继续。'


def _completion_unfinished_reply(verdict: CompletionVerdict) -> str:
    if verdict.status == "delegated":
        return ('任务流程已创建并启动，文件修改已交给 Coding；'
                '子流程产物、训练和结果尚未确认完成，请以该任务的实际进度和审核记录为准。')
    if verdict.status == "needs_user":
        return '本轮操作还需要确认候选项或补充必要信息；请求与已核对的结果已保留。'
    if verdict.status == "blocked":
        reason = {'real_tool_requires_intervention': '工具返回了需要审核或处理的事项'}.get(
            verdict.reason, verdict.reason)
        return '本轮操作尚未完成：' + reason + '。请求、已有任务和结果已保留。'
    labels = {'run_created': '任务创建', 'run_started': '任务启动', 'run_stopped': '任务停止',
        'artifact_updated': '文件修改结果', 'exported': '导出文件', 'observed': '实际查阅结果',
        'tool_effect': '操作结果', 'semantic_answer': '完整答复', 'declared_effect': '实际操作结果'}
    missing = '、'.join(labels.get(item, item) for item in verdict.missing)
    return ('本轮操作尚未完成，已保留请求与实际进度。'
            + ('尚未取得的完成依据：' + missing + '。' if missing else '本轮全部要求尚未核对完成。'))


def _prevent_duplicate_creation(session: CommanderSession, decision: Decision) -> Decision:
    """Do not replay an allocation or a successful exact mutation this turn."""
    if not decision.actions:
        return decision
    allocated = next((m.tool_result.get('run_id') for m in reversed(session.context_messages())
        if m.role == 'tool' and m.turn_id == session.active_turn_id and isinstance(m.tool_result, dict)
        and m.tool_name in {'run.create', 'create_and_start_run', 'run.existing_model'}
        and isinstance(m.tool_result.get('run_id'), str) and m.tool_result.get('run_id')
        and m.tool_result.get('run_id') == session.linked_run_id), None)
    prior = [m for m in session.context_messages() if m.role == 'tool'
             and m.turn_id == session.active_turn_id and (m.tool_result or {}).get('ok') is True]
    actions = []
    queued_mutations: list[tuple[str, dict[str, Any]]] = []
    for action in decision.actions:
        name = str(action.get('tool', ''))
        if allocated and name in {'run.create', 'create_and_start_run', 'run.existing_model'}:
            continue
        mutates = commander_tool_mutates(name)
        raw_args = action.get('args')
        action_args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        repeated = (mutates
                    and (any(m.tool_name == name and (m.tool_args or {}) == action_args for m in prior)
                         or (name, action_args) in queued_mutations))
        if not repeated:
            actions.append(action)
            if mutates:
                queued_mutations.append((name, action_args))
    if len(actions) != len(decision.actions):
        return replace(decision, actions=actions, reply='')
    return decision


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
        flat = {key: value for key, value in action.items() if key not in {"tool", "args"}}
        if "args" in action and flat:
            raise CommanderDecisionError("ambiguous action arguments: use args or flat fields, not both")
        args = action.get("args", flat)
        if not isinstance(tool, str) or not tool.strip() or not isinstance(args, dict):
            raise CommanderDecisionError("invalid action tool or arguments")
        actions.append({"tool": tool, "args": args})
    return Decision(
        reply=reply.strip(),
        next_state=next_state,
        actions=actions,
        goal=raw.get("goal") if isinstance(raw.get("goal"), dict) else None,
    )


def _receipt_ends_action_batch(tool: str, action_count: int) -> bool:
    # The specialized tool already creates AND optionally starts its task. A
    # second authored action cannot turn that single request into another run.
    # Keep the ordinary run.create -> run.start batch supported.
    return tool in {"run.existing_model", "create_and_start_run"} or action_count == 1


def _decision_from_response(text: str) -> Decision:
    """Keep conversation usable without guessing an ambiguous tool operation."""
    try:
        return _parse_decision(text)
    except CommanderDecisionError:
        value = text.strip()
        operation_claim = (re.search(r"已(?:经)?(?:启动|创建|完成|停止|暂停|取消|删除|批准|修改|保存|提交)", value) is not None
            or any(marker in value for marker in ("现在创建", "将启动", "正在启动", "开始执行", "正在运行"))
            or re.search(r"\b(?:started|created|launched|completed|starting|running|stopped|paused|cancelled|canceled|"
                         r"deleted|approved|modified|saved|committed|will\s+(?:run|start|create))\b",
                         value, re.IGNORECASE) is not None)
        if (value and not operation_claim
                and not any(marker in value for marker in ('"reply"', '"actions"', '"tool"', '"next_state"'))):
            return Decision(reply=value, response_mode="plain_reply")
        # Nothing from a malformed action is dispatched, including any valid
        # earlier action in that same response. Prior completed tool observations
        # remain in the session; this path never replays a decision or model call.
        return Decision(reply="请求已保留，这条回复尚未执行新的操作。请补充要做的具体任务或必要参数；已有任务和结果已保留。",
                        response_mode="clarification")


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
    from app.harness.llm.structured_output import StructuredOutputError, parse_json_object
    try:
        return parse_json_object(text, object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant, parse_float=_finite_json_float)
    except StructuredOutputError:
        return None


def _system_prompt(session: CommanderSession) -> str:
    from app.bridge.commander_code import repository_admission_state, repository_summary
    from app.harness.context.folder_context import load_folder_context, render_folder_context, is_context_template

    folder = load_folder_context(session.project)
    project_context = render_folder_context(folder) if folder else ""
    if folder is not None and session.context_version >= 3:
        project_context = "\n".join(f["content"] for f in folder["files"]
            if f["role"] == "instructions" and not is_context_template(f))
    rules = (Path(__file__).parent / "prompts/commander_runtime.md").read_text(encoding="utf-8")
    state = {"project": session.project, "repository": repository_summary(session.project),
        "admission": repository_admission_state(session), "state": session.state.value,
        "linked_run_id": session.linked_run_id, "auto_mode": session.auto_mode,
        "metric_targets": session.metric_targets}
    return (rules + "\n## 当前上下文\n" + json.dumps(state, ensure_ascii=False)
        + "\n## 项目指令（保留原文）\n" + project_context
        + "\n## 可用工具\n" + tools_for_prompt()
        + "\n## 回复与操作交接\n依据当前传输接口调用工具；普通聊天直接回答。")
