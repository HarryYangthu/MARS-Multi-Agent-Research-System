"""Native bounded ReAct/Reflection engine; framework adapters implement one Protocol."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from app.harness.agent_loop.context import compact, pack_context
from app.harness.agent_loop.completion_recovery import apply_author_empty_completion_recovery, validate_author_empty_recovery_resume
from app.harness.agent_loop.native_protocol import INSTRUCTION as NATIVE_INSTRUCTION, history_groups, native_decision, native_specs
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.review import ExternalReview, review_revision
from app.harness.agent_loop.review_plan import (
    WHOLE_REVIEW_UNIT, ReviewPlan, ReviewPlanFactory, ReviewUnit, UnitReviewResult, finish_review_unit, pack_review_unit,
    prepare_review_plan, remaining_budget_message, review_plan_fingerprint, review_unit_config,
    start_review_unit, validate_review_plan_resume, validate_review_provider,
)
from app.harness.agent_loop.protocol import INSTRUCTION, ReviewConflictError, invalid_output_context, is_review_format_error, parse_action, parse_review
from app.harness.agent_loop.trace import LoopTrace, atomic_json, canonical, digest
from app.harness.agent_loop.stop import StopCondition, evaluate_stop, stop_fingerprint
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig, LLMProvider, Message, llm_call_deadline_seconds
from app.harness.tools.registry import ToolContext, ToolRegistry

Validator = Callable[[str, list[dict[str, Any]]], Awaitable[list[str]]]
ProgressSink = Callable[[dict[str, Any]], Awaitable[None]]


def truncation_recovery(reason: object, *, repairs: int, limit: int, effort: str | None) -> dict[str, Any] | None:
    """Plan a bounded repair from public error metadata, never from partial output."""
    if not isinstance(reason, dict) or reason.get("code") != "output_truncated" or repairs >= limit:
        return None
    selected = "low" if effort in {"medium", "high", "max"} else effort
    return {"previous_effort": effort, "reasoning_effort": selected,
            "feedback": "The last response hit the output token limit and was rejected. "
                        "Reasoning may consume this same limit. Produce a concise complete JSON response; "
                        "retain required equations, parameter accounting and evidence, remove repetition. "
                        "Existing observations remain valid; do not repeat research without a specific evidence gap."}


@dataclass
class LoopInput:
    messages: list[Message]
    provider: LLMProvider
    config: LLMConfig
    registry: ToolRegistry
    tool_context: ToolContext
    tools: tuple[str, ...]
    policy: AgentLoopPolicy
    trace_root: Path
    validate: Validator
    reflection_rubric: str = "Check evidence, definitions, arithmetic, internal consistency and falsifiability."
    resume: bool = False
    external_review: ExternalReview | None = None
    progress_sink: ProgressSink | None = None
    review_messages: list[Message] | None = None
    final_schema: dict[str, Any] | None = None
    required_review_tools: tuple[str, ...] = ()
    stop_condition: StopCondition | None = None
    stop_contract_id: str | None = None
    review_plan_factory: ReviewPlanFactory | None = None
    review_plan_contract_id: str | None = None


@dataclass
class LoopResult:
    text: str
    status: str
    observations: list[dict[str, Any]]
    counts: dict[str, int]
    trace_root: Path
    reflection_accepted: bool = False


class AgentLoopExecutor(Protocol):
    async def run(self, request: LoopInput) -> LoopResult: ...


def budget_message(policy: AgentLoopPolicy, counts: dict[str, int]) -> Message:
    """Expose actual remaining local resources before choosing another action."""
    remaining = {"model_calls": max(0, policy.max_model_calls - counts["model_requests"]),
                 "tool_calls": max(0, policy.max_tool_steps - counts["tool_dispatches"]),
                 "validation_repairs": max(0, policy.max_validation_repairs - counts["validation_repairs"])}
    return remaining_budget_message(remaining)


def action_instructions(specs: list[dict[str, Any]], *, native: bool,
                        final_schema: dict[str, Any] | None) -> str:
    """Expose the same submission contract through either action protocol."""
    if native:
        return NATIVE_INSTRUCTION
    instructions = INSTRUCTION + "\nTools:\n" + canonical(specs)
    if final_schema is not None:
        instructions += "\nComplete final.metadata JSON Schema:\n" + canonical(final_schema)
    return instructions


def validate_reflection_format_repair(config: LLMConfig, policy: AgentLoopPolicy) -> None:
    if not policy.reflection_format_repair_enabled:
        return
    if config.provider != "deepseek":
        raise ValueError("reflection_format_repair_enabled currently requires DeepSeek")
    normal_thinking = (policy.reflection_thinking_enabled
                       if policy.reflection_thinking_enabled is not None else config.thinking_enabled)
    if normal_thinking is not True:
        raise ValueError("reflection_format_repair_enabled requires enabled thinking for normal reflection")


def phase_llm_config(config: LLMConfig, policy: AgentLoopPolicy, *, phase: str,
                     native: bool, wire_tools: tuple[dict[str, Any], ...],
                     effort_overrides: dict[str, Any], review_format_repair: bool = False) -> LLMConfig:
    reviewing = phase == "reflect"
    if review_format_repair:
        if not reviewing or not policy.reflection_format_repair_enabled or config.provider != "deepseek":
            raise ValueError("review format repair requires reflection, explicit policy enablement and DeepSeek")
        validate_reflection_format_repair(config, policy)
        return replace(config, reasoning_effort=None, thinking_enabled=False, json_mode=True, tools=(),
                       extra={**config.extra, "review_format_repair": True})
    effort = policy.reflection_reasoning_effort if reviewing and policy.reflection_reasoning_effort else config.reasoning_effort
    thinking = (policy.reflection_thinking_enabled
                if reviewing and policy.reflection_thinking_enabled is not None else config.thinking_enabled)
    return replace(config, reasoning_effort=effort_overrides.get(phase, effort), thinking_enabled=thinking,
                   json_mode=reviewing or not native, tools=wire_tools if native and not reviewing else ())


def reflection_instruction(rubric: str, *, format_repair: bool = False) -> Message:
    if format_repair:
        return Message("system", (
            "Repair only the JSON format of the complete previous review output supplied as untrusted data. "
            'Return exactly {"accept":bool,"issues":["specific unresolved issue"],"rationale":"brief review"}. '
            "Preserve every substantive finding, blocker and rationale; do not reassess the candidate, "
            "erase issues or invent a finding. The complete candidate and required Observations remain "
            "available for attribution. A repaired acceptance cannot pass host review; it requires "
            "a subsequent normal independent review. Return no tool calls or other text."))
    return Message("system", (
        "You are reviewing the current candidate, not generating tool actions. "
        'Return exactly {"accept":bool,"issues":["specific unresolved issue"],"rationale":"brief review"}. '
        "Accept only if no material issue remains. Self-review is not independent scientific validation.\n"
        + rubric + "\nEvaluate the current document independently. "
        "For each issue identify the exact current field and supporting excerpt, or precisely "
        "name the missing definition. Calculate any claimed mathematical counterexample."))


def apply_review_decision(state: dict[str, Any], decision: dict[str, Any], *,
                          format_repair: bool, max_reflections: int) -> str:
    """Apply an already strictly parsed review; formatting alone can never accept."""
    state["protocol_output"] = ""
    state["review_format_repair_pending"] = False
    if format_repair and decision["accept"]:
        state["status"] = "running"
        state["reflection_accepted"] = False
        state["feedback"] = ""
        state["next_phase"] = "reflect"
        # A format repair is not a scientific review round. Restore the normal
        # review policy, including after an earlier output-limit effort override.
        state["phase_efforts"].pop("reflect", None)
        return "independent_review"
    state["counts"]["reflections"] += 1
    state["reviewed_candidate_sha"] = digest(state["candidate"])
    state["review_issues"] = decision["issues"]
    if decision["accept"]:
        state["reflection_accepted"] = True
        state["feedback"] = ""
        state["status"] = "passed"
        return "accepted"
    state["reflection_accepted"] = False
    state["feedback"] = canonical({"required_revision": decision["issues"],
                                    "review_rationale": decision["rationale"],
                                    "instruction": "Revise the complete candidate to resolve these issues. Do not merely remove warnings."})
    state["next_phase"] = "act"
    if state["counts"]["reflections"] >= max_reflections:
        state["status"] = "reflection_rejected"
    return "revision"


def apply_planned_review_decision(state: dict[str, Any], result: UnitReviewResult, *, response_text: str,
                                  response_visible: Any, max_reflections: int) -> tuple[dict[str, Any], str]:
    """Commit unit and candidate transitions together, before any notification await."""
    record = finish_review_unit(state, result, response_text=response_text, response_visible=response_visible)
    if record["unit_id"] != WHOLE_REVIEW_UNIT and result.decision["accept"]:
        return record, "next_unit"
    outcome = apply_review_decision(state, result.decision, format_repair=False, max_reflections=max_reflections)
    return record, outcome


def apply_loop_cancellation(state: dict[str, Any]) -> bool:
    """Interrupt unfinished work; notification cancellation cannot reopen a terminal outcome."""
    if state["status"] != "running":
        return False
    state["status"] = "interrupted"
    if state["pending"] == "model":
        state["usage_complete"] = False
    return True


def missing_review_evidence(history: list[dict[str, Any]], manifest: dict[str, Any],
                            required_tools: tuple[str, ...], *, observation_chars: int) -> list[str]:
    """Refuse review when required real tool evidence was compressed or omitted."""
    hidden = set(manifest.get("compressed_history", [])) | set(manifest.get("omitted_history", []))
    preserved = set(manifest.get("preserved_review_history", []))
    missing: list[str] = []
    for index, group in enumerate(history_groups(history)):
        for item in group:
            if item.get("tool") in required_tools and item.get("ok"):
                if index in hidden or (index not in preserved and compact(item, observation_chars) != item):
                    missing.append(str(item["tool"]) + " history group " + str(index))
    return missing


class NativeAgentLoop:
    async def run(self, request: LoopInput) -> LoopResult:
        p = request.policy
        validate_reflection_format_repair(request.config, p)
        specs = []
        for name in request.tools:
            spec = request.registry.spec(name)
            if not request.registry.has(name) or spec is None or spec.bridge_only:
                raise ValueError(f"configured tool has no executable specification: {name}")
            specs.append({"name": name, "description": spec.description, "args_schema": spec.input_schema})
        native = p.protocol == "native_tools"
        if native and request.config.thinking_enabled is not False:
            raise ValueError("native tool loop requires explicitly disabled thinking until continuation support is available")
        wire_tools = native_specs(specs, request.final_schema) if native else ()
        tool_schema_budget = len(canonical(wire_tools).encode("utf-8")) if native else 0
        instructions = action_instructions(specs, native=native, final_schema=request.final_schema)
        pinned = list(request.messages) + [Message(role="system", content=instructions)]
        fingerprint = digest({"messages": [x.to_wire() for x in pinned], "policy": p.fingerprint_data(),
                              "model": request.config.model, "provider": request.config.provider,
                              "project": request.tool_context.project, "tools": specs,
                              "context_format_version": 8})
        if native:
            fingerprint = digest({"base": fingerprint, "wire_tools": wire_tools})
        if request.required_review_tools:
            fingerprint = digest({"base": fingerprint, "required_review_tools": request.required_review_tools})
        if request.review_messages is not None:
            fingerprint = digest({"base": fingerprint, "review_messages": [m.to_wire() for m in request.review_messages]})
        if request.final_schema is not None:
            fingerprint = digest({"base": fingerprint, "final_schema": request.final_schema})
        fingerprint = stop_fingerprint(fingerprint, request.stop_condition, request.stop_contract_id)
        fingerprint = review_plan_fingerprint(fingerprint, request.review_plan_factory, request.review_plan_contract_id,
                                              request.config, p)
        if request.review_plan_factory is not None:
            validate_review_provider(request.provider, configured_provider=request.config.provider)
            fingerprint = digest({"base": fingerprint, "reflection_rubric": request.reflection_rubric})
        trace = LoopTrace(request.trace_root, p.trace, resume=request.resume)
        state: dict[str, Any] = {
            "fingerprint": fingerprint, "status": "running", "pending": None,
            "counts": {k: 0 for k in ("model_requests", "model_responses", "tool_dispatches",
                                     "observations", "sdk_attempts", "action_rounds", "protocol_repairs",
                                     "validation_repairs", "reflections")},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "usage_complete": True, "history": [], "candidate": "", "feedback": "",
            "next_phase": "act", "seen": {}, "reflection_accepted": False,
            "review_issues": [], "reviewed_candidate_sha": "",
        }
        if request.review_plan_factory is not None:
            state["review_plan_contract_id"] = request.review_plan_contract_id
        if request.resume:
            if p.trace != "full":
                raise ValueError("resume requires full trace/checkpoint mode")
            state = json.loads((request.trace_root / "checkpoint.json").read_text())
            allowed_status = {"running", "interrupted", "model_error"}
            if request.external_review:
                allowed_status.add("passed")
                review_revision(state, request.external_review, p)
            if state["fingerprint"] != fingerprint or state["status"] not in allowed_status:
                raise ValueError("resume requires identical inputs/configuration and an interrupted/model-error run")
            validate_author_empty_recovery_resume(state, p)
            if request.review_plan_contract_id is not None:
                validate_review_plan_resume(state, request.trace_root, contract_id=request.review_plan_contract_id)
            if state.get("pending_batch"):
                raise ValueError("incomplete batch: reconcile tool receipts before resuming; automatic replay forbidden")
            if state["pending"] == "tool":
                raise ValueError("tool outcome unknown: reconcile its receipt before resuming; automatic replay forbidden")
            if state["pending"] == "model":
                state["usage_complete"] = False
            state["status"] = "running"
            state["pending"] = None
        state.setdefault("review_issues", [])
        state.setdefault("validation_issues", [])
        state.setdefault("protocol_output", "")
        state.setdefault("reviewed_candidate_sha", "")
        state.setdefault("phase_efforts", {})
        state.setdefault("review_format_repair_pending", False)
        async def progress(kind: str, **payload: Any) -> None:
            if request.progress_sink is not None:
                try:
                    await request.progress_sink({"kind": kind, "phase": state["next_phase"], **payload})
                except asyncio.CancelledError:
                    if state["status"] == "running":
                        raise
                    # Delivery cancellation after durable completion cannot
                    # replace a known final outcome or force another review.
                    trace.emit("progress_cancelled_after_completion", {"progress_kind": kind, "status": state["status"]})
                    trace.snapshot(state)
        if request.resume and "last_model_error" not in state:
            prior_events = [json.loads(line) for line in trace.events.read_text().splitlines()]
            state["last_model_error"] = next((row.get("reason") for row in reversed(prior_events)
                                               if row["kind"] == "model_error"), None)
        counts = state["counts"]
        trace.emit("resumed" if request.resume else "started", {"fingerprint": fingerprint})
        if request.external_review:
            if not request.resume:
                raise ValueError("external review requires an existing invocation")
            state.update(review_revision(state, request.external_review, p))
            trace.emit("external_review", {"reviewer": request.external_review.reviewer,
                                           "candidate_digest": request.external_review.candidate_digest,
                                           "budgets_reset": False}, visible=list(request.external_review.issues))
        trace.snapshot(state)
        cfg = replace(request.config, json_mode=not native)

        def on_attempt(kind: str, data: dict[str, Any]) -> None:
            trace.record_attempt(state, kind, data)

        def usage(payload: Any) -> None:
            if not isinstance(payload, dict):
                state["usage_complete"] = False
                return
            for key in state["usage"]:
                value = payload.get(key)
                if isinstance(value, int) and value >= 0:
                    state["usage"][key] += value
                else:
                    state["usage_complete"] = False

        cfg.attempt_observer = on_attempt
        active_plan: ReviewPlan | None = None

        def phase_config() -> LLMConfig:
            configured = phase_llm_config(cfg, p, phase=state["next_phase"], native=native,
                                    wire_tools=wire_tools, effort_overrides=state["phase_efforts"],
                                    review_format_repair=bool(request.review_plan_factory is None and p.reflection_format_repair_enabled
                                        and state["next_phase"] == "reflect" and state["protocol_output"]
                                        and state["review_format_repair_pending"]))
            # One actual attempt per review unit. Provider SDK retries are also
            # disabled by the provider adapter; author behavior is unchanged.
            if request.review_plan_factory is not None and state["next_phase"] == "reflect":
                configured = review_unit_config(configured)
            return configured

        def recover_completion(reason: object, *, error: Exception | None = None,
                               response: dict[str, Any] | None = None) -> bool:
            if request.review_plan_factory is not None and state["next_phase"] == "reflect":
                return False  # An attempted unit cannot receive a second draw.
            plan = apply_author_empty_completion_recovery(error, response=response, state=state, policy=p,
                                                          effort=phase_config().reasoning_effort)
            if plan is None:
                plan = truncation_recovery(reason, repairs=counts["protocol_repairs"],
                                           limit=p.max_protocol_repairs, effort=phase_config().reasoning_effort)
            if plan is None:
                return False
            phase = state["next_phase"]
            empty_recovery = plan.get("recovery_kind") == "author_empty_completed_response"
            if not empty_recovery:
                counts["protocol_repairs"] += 1
                state["phase_efforts"][phase] = plan["reasoning_effort"]
            final_description = ("mars_submit_document call with complete metadata and body"
                                 if request.final_schema is not None else "Markdown document beginning with YAML frontmatter, without preamble or code fences")
            if not empty_recovery:
                state["feedback"] = (plan["feedback"].replace("JSON response", final_description)
                                     if native and state["next_phase"] != "reflect" else plan["feedback"])
            state["pending"] = None
            state["status"] = "running"
            state["last_model_error"] = None
            trace.emit("completion_recovery", {"phase": phase, "code": plan.get("code", "output_truncated"),
                                               "previous_effort": plan["previous_effort"],
                                               "reasoning_effort": plan["reasoning_effort"],
                                               "remaining_model_calls": p.max_model_calls-counts["model_requests"],
                                               **({"recovery_kind": plan["recovery_kind"],
                                                   "response_event_seq": plan["response_event_seq"],
                                                   "response_metadata_sha256": plan["response_metadata_sha256"],
                                                   "protocol_repairs": counts["protocol_repairs"]} if empty_recovery else {})})
            trace.snapshot(state)
            return True

        def stop_at_boundary(stage: Literal["before_model", "after_validation"]) -> bool:
            decision = evaluate_stop(request.stop_condition, state, stage=stage)
            if decision is None:
                return False
            state["termination"] = {"status": decision.status, "reason": decision.reason,
                                    "details": decision.details, "stage": stage,
                                    "contract_id": request.stop_contract_id}
            state["status"] = decision.status
            state["feedback"] = decision.reason
            state["reflection_accepted"] = False
            trace.emit("stopped", state["termination"])
            trace.snapshot(state)
            return True

        try:
            if request.resume:
                recover_completion(state.get("last_model_error"))
            for _ in range(max(0, p.max_model_calls - counts["model_requests"])):
                if stop_at_boundary("before_model"):
                    break
                reviewing = state["next_phase"] == "reflect"
                planned_review = reviewing and request.review_plan_factory is not None
                unit: ReviewUnit | None = None
                if planned_review:
                    assert request.review_plan_factory is not None and request.review_plan_contract_id is not None
                    if active_plan is None or active_plan.candidate_sha256 != digest(state["candidate"]):
                        # Domain callbacks get a copy of history, no provider or
                        # dispatcher. Only the loop can make or account for calls.
                        active_plan = request.review_plan_factory(state["candidate"], json.loads(canonical(state["history"])))
                    prior_plan_sha = state.get("review_plan", {}).get("plan_sha256")
                    ready = prepare_review_plan(state, active_plan, contract_id=request.review_plan_contract_id,
                                                max_model_calls=p.max_model_calls)
                    if state["review_plan"]["plan_sha256"] != prior_plan_sha:
                        trace.emit("review_plan_started", {"candidate_sha256": active_plan.candidate_sha256,
                                   "plan_sha256": state["review_plan"]["plan_sha256"]}, visible=state["review_plan"])
                    if not ready:
                        trace.emit("review_budget_exhausted", {"reason": state["feedback"], "budgets_reset": False})
                        trace.snapshot(state)
                        break
                    index = state["review_plan"]["next_unit"]
                    unit = active_plan.units[index] if index < len(active_plan.units) else None
                format_repair = bool(not planned_review and p.reflection_format_repair_enabled and reviewing
                                     and state["protocol_output"] and state["review_format_repair_pending"])
                if counts["model_requests"] == 0:
                    await progress("started")
                extra: list[Message] = [budget_message(p, counts)]
                if state["protocol_output"]:
                    extra.append(invalid_output_context(state["protocol_output"],
                                 native=native and not (reviewing and p.reflection_format_repair_enabled)))
                if reviewing and unit is None:
                    extra.append(reflection_instruction(request.reflection_rubric, format_repair=format_repair))
                feedback = state["feedback"]
                if not reviewing and counts["tool_dispatches"] >= p.max_tool_steps:
                    feedback += "\nTool budget exhausted. Return a final grounded document or explicit evidence gaps."
                # Reflection sends no tools; reserve only schemas actually sent.
                phase_schema_budget = 0 if reviewing else tool_schema_budget
                if unit is not None:
                    try:
                        messages, manifest = pack_review_unit(unit, budget=p.input_token_budget,
                                                              budget_context=budget_message(p, counts))
                    except ValueError as exc:
                        state["status"] = "review_evidence_unavailable"
                        state["feedback"] = str(exc)
                        trace.emit("review_evidence_unavailable", {"reason": str(exc), "unit_id": unit.unit_id})
                        trace.snapshot(state)
                        break
                else:
                    messages, manifest = pack_context(
                        (request.review_messages if reviewing and request.review_messages is not None else pinned) + extra,
                        state["history"], feedback, state["candidate"],
                        budget=p.input_token_budget - phase_schema_budget, observation_chars=p.observation_chars,
                        native=native, reviewing=reviewing, review_issues=state["review_issues"],
                        validation_issues=state["validation_issues"],
                        required_review_tools=request.required_review_tools,
                    )
                manifest["tool_schema_upper_bound_tokens"] = phase_schema_budget
                manifest["total_input_upper_bound_tokens"] = manifest["estimated_upper_bound_tokens"] + phase_schema_budget
                if reviewing and unit is None:
                    missing = missing_review_evidence(state["history"], manifest, request.required_review_tools,
                                                      observation_chars=p.observation_chars)
                    if missing:
                        state["status"] = "review_evidence_unavailable"
                        state["feedback"] = "Required review evidence was compressed or omitted: " + "; ".join(missing)
                        trace.emit("review_evidence_unavailable", {"missing": missing, "context_manifest": manifest})
                        trace.snapshot(state)
                        break
                counts["model_requests"] += 1
                state["pending"] = "model"
                call_config = phase_config()
                review_request = start_review_unit(state, messages) if planned_review else None
                trace.emit("context_packed", manifest)
                trace.emit("model_request", {"request": counts["model_requests"], "phase": state["next_phase"],
                                             "reasoning_effort": call_config.reasoning_effort,
                                             "thinking_enabled": call_config.thinking_enabled,
                                             "repair_mode": "review_format" if format_repair else None,
                                             "max_tokens": call_config.max_tokens,
                                             **({"review_unit_id": review_request["unit_id"],
                                                 "review_plan_sha256": review_request["plan_sha256"],
                                                 "review_candidate_sha256": review_request["candidate_sha256"],
                                                 "max_retries": call_config.max_retries} if review_request else {})},
                           visible=[m.to_wire() for m in messages])
                trace.snapshot(state)
                try:
                    completion = await asyncio.wait_for(
                        request.provider.complete(messages, call_config), timeout=llm_call_deadline_seconds(call_config))
                except Exception as exc:
                    usage(getattr(exc, "usage", None))
                    rejected_response: dict[str, Any] | None = None
                    if isinstance(exc, LLMCompletionError):
                        counts["model_responses"] += 1
                        response_payload = {"request": counts["model_requests"], "rejected": True,
                                            "reason": exc.reason, "usage": exc.usage}
                        trace.emit("model_response", response_payload)
                        rejected_response = {"event_seq": trace.seq, "kind": "model_response", **response_payload}
                        state["pending"] = None
                    state["status"] = "model_error"
                    state["last_model_error"] = getattr(exc, "reason", None)
                    trace.emit("model_error", {"error_type": type(exc).__name__, "reason": getattr(exc, "reason", None)})
                    if recover_completion(state["last_model_error"], error=exc, response=rejected_response):
                        continue
                    break
                if completion.is_mock or completion.provider in {"mock", "fake"}:
                    raise RuntimeError("non-real completion rejected")
                counts["model_responses"] += 1
                usage(completion.raw.get("usage"))
                state["pending"] = None
                state["last_model_error"] = None
                response_visible = ({"text": completion.text, "tool_calls": [c.to_wire() for c in completion.tool_calls]}
                                    if native else completion.text)
                trace.emit("model_response", {"request": counts["model_requests"], "provider": completion.provider,
                                              "model": completion.model, "usage": completion.raw.get("usage")},
                           visible=response_visible)
                review_conflict = False
                unit_result: UnitReviewResult | None = None
                try:
                    if planned_review and completion.tool_calls:
                        raise ValueError("review units cannot return tool calls")
                    if unit is not None:
                        unit_result = unit.parse_response(completion.text)
                        decision = parse_review(canonical(unit_result.decision))
                    else:
                        decision = (parse_review(completion.text) if reviewing else
                                    native_decision(completion, request.tools, structured_final=request.final_schema is not None) if native else parse_action(completion.text))
                except ReviewConflictError as exc:
                    # Keep the original response in trace, but never fix this by
                    # asking the reviewer to erase its issue list without revision.
                    review_conflict = True
                    decision = {**exc.review, "accept": False}
                    trace.emit("review_conflict", {"effective_accept": False}, visible=exc.review)
                except ValueError as exc:
                    counts["protocol_repairs"] += 1
                    if planned_review:
                        # A malformed reviewer response is an execution failure,
                        # not an invitation to edit the scientific candidate or
                        # silently call the same review unit again.
                        failure = UnitReviewResult({"accept": False, "issues": ["Review response failed its contract: " + str(exc)],
                                                    "rationale": "No usable review decision; the candidate is not accepted."},
                                                   {"protocol_error": str(exc)})
                        record = finish_review_unit(state, failure, response_text=completion.text,
                                                    response_visible=response_visible, valid=False)
                        trace.emit("review_unit", {"request": counts["model_requests"], "unit_id": record["unit_id"],
                                                   "valid": False}, visible=record)
                        state["status"] = "review_protocol_error"
                        state["reflection_accepted"] = False
                        state["feedback"] = failure.decision["issues"][0]
                        trace.emit("protocol_error", {"error": str(exc), "review_unit_id": record["unit_id"],
                                                      "automatic_retry": False})
                        trace.snapshot(state)
                        break
                    state["review_format_repair_pending"] = bool(
                        p.reflection_format_repair_enabled and reviewing and is_review_format_error(exc))
                    state["protocol_output"] = (canonical({"text": completion.text, "tool_calls": [c.to_wire() for c in completion.tool_calls]})
                                                if native else completion.text)
                    state["feedback"] = (f"Protocol error: {exc}. Correct the provided invalid output and return "
                                         "exactly one required JSON object. No extra braces, prose or second action. "
                                         "Preserve the proposal's content while fixing syntax; existing Observations remain valid.")
                    if reviewing:
                        state["feedback"] = (
                            f"Review protocol error: {exc}. Return exactly one JSON object with "
                            "accept (boolean), issues (array of unresolved blocker strings), and rationale (string). "
                            "Do not return a proposal, tool invocation, XML, code fence or multiple JSON objects. "
                            "Review the current candidate and preserve substantive findings while fixing only format."
                        )
                        if state["review_format_repair_pending"]:
                            state["feedback"] = (
                                f"Review JSON format error: {exc}. Correct only syntax or duplicate keys in the complete "
                                "provided review. Preserve every substantive finding and unresolved blocker. "
                                "Do not reassess the candidate or discard an issue to produce acceptance."
                            )
                    elif native:
                        final_instruction = ("call mars_submit_document with complete metadata and body"
                                             if request.final_schema is not None else "submit the complete Markdown candidate")
                        state["feedback"] = (f"Protocol error: {exc}. Use valid native research tool calls or {final_instruction}. "
                                             "For document submission, arguments must be exactly one JSON object with "
                                             "two keys: metadata and body. Close metadata before body; close the root "
                                             "once after body. Do not append another body or object after the root. "
                                             "Resolve the pinned candidate validation errors too. No rejected action was executed.")
                    trace.emit("protocol_error", {"error": str(exc),
                               "repair_mode": "review_format" if state["review_format_repair_pending"] else None})
                    if counts["protocol_repairs"] > p.max_protocol_repairs:
                        state["status"] = "protocol_exhausted"
                        break
                    trace.snapshot(state)
                    continue
                state["protocol_output"] = ""
                if reviewing:
                    finished_record: dict[str, Any] | None = None
                    if planned_review:
                        details = unit_result.details if unit_result is not None else {"review": decision}
                        finished_record, outcome = apply_planned_review_decision(state, UnitReviewResult(decision, details),
                            response_text=completion.text, response_visible=response_visible, max_reflections=p.max_reflections)
                        trace.emit("review_unit", {"request": counts["model_requests"], "unit_id": finished_record["unit_id"],
                                                   "valid": True}, visible=finished_record)
                        if outcome == "next_unit":
                            # Positive units are not whole-candidate acceptance.
                            # They also never become input to another reviewer.
                            trace.snapshot(state)
                            await progress("review_unit", unit_id=finished_record["unit_id"], accepted=True)
                            continue
                    else:
                        outcome = apply_review_decision(state, decision, format_repair=format_repair,
                                                        max_reflections=p.max_reflections)
                    if outcome == "independent_review":
                        trace.emit("review_format_repaired", {"accept": True, "effective_accept": False,
                                   "repair_mode": "review_format", "independent_review_required": True}, visible=decision)
                        await progress("review_format_repaired", independent_review_required=True)
                        trace.snapshot(state)
                        continue
                    trace.emit("reflection", {"accept": decision["accept"], "round": counts["reflections"],
                                              "host_conflict_rejection": review_conflict,
                                              "repair_mode": "review_format" if format_repair else None}, visible=decision)
                    trace.snapshot(state)
                    if finished_record is not None:
                        await progress("review_unit", unit_id=finished_record["unit_id"], accepted=decision["accept"])
                    await progress("review", accepted=decision["accept"], issues=decision["issues"])
                    if outcome == "accepted":
                        break
                    if counts["reflections"] >= p.max_reflections:
                        break
                elif "final" in decision:
                    state["candidate"] = decision["final"]
                    if "submission_id" in decision:
                        trace.emit("document_submission", {"call_id": decision["submission_id"],
                                                           "candidate_sha256": digest(state["candidate"]),
                                                           "serialization_only": True})
                    await progress("candidate", text=state["candidate"])
                    errors = await request.validate(state["candidate"], state["history"])
                    if state["review_issues"] and digest(state["candidate"]) == state["reviewed_candidate_sha"]:
                        errors.append("/candidate: unresolved review issues require a revised candidate")
                    state["validation_issues"] = list(errors)
                    trace.emit("validation", {"valid": not errors}, visible=errors)
                    await progress("validation", valid=not errors, issues=errors)
                    if errors:
                        counts["validation_repairs"] += 1
                        state["feedback"] = canonical({"validation_errors": errors})
                        if counts["validation_repairs"] > p.max_validation_repairs:
                            state["status"] = "validation_exhausted"
                            break
                    elif stop_at_boundary("after_validation"):
                        break
                    elif p.mode == "reflection":
                        state["next_phase"] = "reflect"
                        state["feedback"] = ""
                    else:
                        state["status"] = "passed"
                        break
                else:
                    actions = decision.get("batch", [decision])
                    identities = [digest({"tool": a["tool"], "args": a["args"]}) for a in actions]
                    if len(actions) > 1:
                        if (len(actions) > p.max_tool_steps - counts["tool_dispatches"] or
                            len(identities) != len(set(identities)) or
                            any(i in state["seen"] and not state["seen"][i]["retry_allowed"] for i in identities)):
                            state["feedback"] = "Batch not executed: exceeds remaining tool budget or repeats completed/permanent-failed actions. Submit only needed actions within budget."
                            trace.snapshot(state)
                            continue
                        for action in actions:
                            action.update(native_batch_id=counts["model_requests"], native_batch_size=len(actions))
                        state["pending_batch"] = True
                    for decision in actions:
                        counts["action_rounds"] += 1
                        tool = decision["tool"]
                        identity = digest({"tool": tool, "args": decision["args"]})
                        if tool not in request.tools:
                            state["feedback"] = f"Tool {tool} is not available for this agent."
                        elif counts["tool_dispatches"] >= p.max_tool_steps:
                            state["feedback"] = "Tool budget exhausted."
                        elif identity in state["seen"] and not state["seen"][identity]["retry_allowed"]:
                            state["feedback"] = "Duplicate successful/permanent-failed action rejected; use its prior Observation."
                        else:
                            prior = state["seen"].get(identity)
                            state["pending"] = "tool"
                            counts["tool_dispatches"] += 1
                            trace.emit("tool_dispatch", {"step": counts["tool_dispatches"], "tool": tool}, visible=decision)
                            trace.snapshot(state)
                            await progress("action", tool=tool, reason=decision.get("reason", ""), args=decision["args"])
                            result = await request.registry.dispatch(tool, decision["args"], request.tool_context)
                            observation = {**decision, "ok": result.ok, "output": result.output, "error": result.error,
                                           "status": result.status, "blocked_by_gate": result.blocked_by_gate}
                            if p.trace == "full":
                                raw = request.trace_root / "tools" / f"{counts['tool_dispatches']:04d}.json"
                                atomic_json(raw, observation)
                                observation["raw_ref"] = str(raw)
                            state["history"].append(observation)
                            counts["observations"] += 1
                            state["pending"] = None
                            retryable = (not result.ok and not result.blocked_by_gate and not result.requires_approval
                                         and any(word in (result.error or "").lower() for word in
                                                 ("timeout", "timed out", "429", "502", "503", "504", "connection")))
                            state["seen"][identity] = {"retry_allowed": retryable and prior is None}
                            state["feedback"] = ""
                            trace.emit("observation", {"step": counts["tool_dispatches"], "tool": tool, "ok": result.ok},
                                       visible=observation)
                            await progress("observation", tool=tool, ok=result.ok, error=result.error)
                            if result.requires_approval or result.blocked_by_gate:
                                state["status"] = "blocked"
                                break
                    if state["status"] == "blocked":
                        break
                    state["pending_batch"] = False
                trace.snapshot(state)
            if state["status"] == "running":
                stop_at_boundary("before_model")
            if state["status"] == "running":
                state["status"] = "budget_exhausted"
        except asyncio.CancelledError:
            if apply_loop_cancellation(state):
                trace.emit("interrupted", {"pending": state["pending"]})
                raise
            trace.emit("cancelled_after_completion", {"status": state["status"]})
        except Exception as exc:
            state["status"] = "error"
            trace.emit("error", {"error_type": type(exc).__name__, "message": str(exc)[:500]})
            raise
        finally:
            trace.emit("finished", {"status": state["status"]})
            trace.snapshot(state)
            await request.provider.close()
            cfg.attempt_observer = None
            await progress("finished", status=state["status"], reason=state.get("termination", {}).get("reason", ""))
        return LoopResult(state["candidate"], state["status"], state["history"], dict(counts),
                          request.trace_root, state["reflection_accepted"])
