"""Native bounded ReAct/Reflection engine; framework adapters implement one Protocol."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from app.harness.agent_loop.context import compact, pack_context
from app.harness.agent_loop.native_protocol import INSTRUCTION as NATIVE_INSTRUCTION, history_groups, native_decision, native_specs
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.review import ExternalReview, review_revision
from app.harness.agent_loop.protocol import INSTRUCTION, ReviewConflictError, invalid_output_context, parse_action, parse_review
from app.harness.agent_loop.trace import LoopTrace, atomic_json, canonical, digest
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
    return Message("system", "Host remaining budget for this agent loop: " + canonical(remaining)
                   + ". The next model call is included. Each dispatched tool, including failures, consumes "
                   "one tool call. Reserve tools for acquiring and checking evidence, and calls for submission "
                   "and revision. These are local counters, not the total cost of any delegated loops. "
                   "Do not invent evidence when resources are insufficient.")


def phase_llm_config(config: LLMConfig, policy: AgentLoopPolicy, *, phase: str,
                     native: bool, wire_tools: tuple[dict[str, Any], ...],
                     effort_overrides: dict[str, Any]) -> LLMConfig:
    reviewing = phase == "reflect"
    effort = policy.reflection_reasoning_effort if reviewing and policy.reflection_reasoning_effort else config.reasoning_effort
    thinking = (policy.reflection_thinking_enabled
                if reviewing and policy.reflection_thinking_enabled is not None else config.thinking_enabled)
    return replace(config, reasoning_effort=effort_overrides.get(phase, effort), thinking_enabled=thinking,
                   json_mode=reviewing or not native, tools=wire_tools if native and not reviewing else ())


def missing_review_evidence(history: list[dict[str, Any]], manifest: dict[str, Any],
                            required_tools: tuple[str, ...], *, observation_chars: int) -> list[str]:
    """Refuse review when required real tool evidence was compressed or omitted."""
    hidden = set(manifest.get("compressed_history", [])) | set(manifest.get("omitted_history", []))
    missing: list[str] = []
    for index, group in enumerate(history_groups(history)):
        for item in group:
            if item.get("tool") in required_tools and item.get("ok"):
                if index in hidden or compact(item, observation_chars) != item:
                    missing.append(str(item["tool"]) + " history group " + str(index))
    return missing


class NativeAgentLoop:
    async def run(self, request: LoopInput) -> LoopResult:
        p = request.policy
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
        instructions = NATIVE_INSTRUCTION if native else INSTRUCTION + "\nTools:\n" + canonical(specs)
        pinned = list(request.messages) + [Message(role="system", content=instructions)]
        fingerprint = digest({"messages": [x.to_wire() for x in pinned], "policy": asdict(p),
                              "model": request.config.model, "provider": request.config.provider,
                              "project": request.tool_context.project, "tools": specs,
                              "context_format_version": 6})
        if native:
            fingerprint = digest({"base": fingerprint, "wire_tools": wire_tools})
        if request.required_review_tools:
            fingerprint = digest({"base": fingerprint, "required_review_tools": request.required_review_tools})
        if request.review_messages is not None:
            fingerprint = digest({"base": fingerprint, "review_messages": [m.to_wire() for m in request.review_messages]})
        if request.final_schema is not None:
            fingerprint = digest({"base": fingerprint, "final_schema": request.final_schema})
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
        async def progress(kind: str, **payload: Any) -> None:
            if request.progress_sink is not None:
                await request.progress_sink({"kind": kind, "phase": state["next_phase"], **payload})
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

        def phase_config() -> LLMConfig:
            return phase_llm_config(cfg, p, phase=state["next_phase"], native=native,
                                    wire_tools=wire_tools, effort_overrides=state["phase_efforts"])

        def recover_completion(reason: object) -> bool:
            plan = truncation_recovery(reason, repairs=counts["protocol_repairs"],
                                       limit=p.max_protocol_repairs, effort=phase_config().reasoning_effort)
            if plan is None:
                return False
            phase = state["next_phase"]
            counts["protocol_repairs"] += 1
            state["phase_efforts"][phase] = plan["reasoning_effort"]
            final_description = ("mars_submit_document call with complete metadata and body"
                                 if request.final_schema is not None else "Markdown document beginning with YAML frontmatter, without preamble or code fences")
            state["feedback"] = (plan["feedback"].replace("JSON response", final_description)
                                 if native and state["next_phase"] != "reflect" else plan["feedback"])
            state["pending"] = None
            state["status"] = "running"
            state["last_model_error"] = None
            trace.emit("completion_recovery", {"phase": phase, "code": "output_truncated",
                                               "previous_effort": plan["previous_effort"],
                                               "reasoning_effort": plan["reasoning_effort"],
                                               "remaining_model_calls": p.max_model_calls-counts["model_requests"]})
            trace.snapshot(state)
            return True

        try:
            if request.resume:
                recover_completion(state.get("last_model_error"))
            for _ in range(max(0, p.max_model_calls - counts["model_requests"])):
                reviewing = state["next_phase"] == "reflect"
                if counts["model_requests"] == 0:
                    await progress("started")
                extra: list[Message] = [budget_message(p, counts)]
                if state["protocol_output"]:
                    extra.append(invalid_output_context(state["protocol_output"]))
                if reviewing:
                    extra.append(Message(role="system", content=(
                        "You are reviewing the current candidate, not generating tool actions. "
                        'Return exactly {"accept":bool,"issues":["specific unresolved issue"],"rationale":"brief review"}. '
                        "Accept only if no material issue remains. Self-review is not independent scientific validation.\n"
                        + request.reflection_rubric + "\nEvaluate the current document independently. "
                        "For each issue identify the exact current field and supporting excerpt, or precisely "
                        "name the missing definition. Calculate any claimed mathematical counterexample.")))
                feedback = state["feedback"]
                if not reviewing and counts["tool_dispatches"] >= p.max_tool_steps:
                    feedback += "\nTool budget exhausted. Return a final grounded document or explicit evidence gaps."
                messages, manifest = pack_context(
                    (request.review_messages if reviewing and request.review_messages is not None else pinned) + extra,
                    state["history"], feedback, state["candidate"],
                    budget=p.input_token_budget - tool_schema_budget, observation_chars=p.observation_chars,
                    native=native, reviewing=reviewing, review_issues=state["review_issues"],
                    validation_issues=state["validation_issues"],
                )
                manifest["tool_schema_upper_bound_tokens"] = tool_schema_budget
                manifest["total_input_upper_bound_tokens"] = manifest["estimated_upper_bound_tokens"] + tool_schema_budget
                if reviewing:
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
                trace.emit("context_packed", manifest)
                trace.emit("model_request", {"request": counts["model_requests"], "phase": state["next_phase"],
                                             "reasoning_effort": call_config.reasoning_effort,
                                             "thinking_enabled": call_config.thinking_enabled,
                                             "max_tokens": call_config.max_tokens},
                           visible=[m.to_wire() for m in messages])
                trace.snapshot(state)
                try:
                    completion = await asyncio.wait_for(
                        request.provider.complete(messages, call_config), timeout=llm_call_deadline_seconds(call_config))
                except Exception as exc:
                    usage(getattr(exc, "usage", None))
                    if isinstance(exc, LLMCompletionError):
                        counts["model_responses"] += 1
                        trace.emit("model_response", {"request": counts["model_requests"],
                                                      "rejected": True, "reason": exc.reason,
                                                      "usage": exc.usage})
                        state["pending"] = None
                    state["status"] = "model_error"
                    state["last_model_error"] = getattr(exc, "reason", None)
                    trace.emit("model_error", {"error_type": type(exc).__name__, "reason": getattr(exc, "reason", None)})
                    if recover_completion(state["last_model_error"]):
                        continue
                    break
                if completion.is_mock or completion.provider in {"mock", "fake"}:
                    raise RuntimeError("non-real completion rejected")
                counts["model_responses"] += 1
                usage(completion.raw.get("usage"))
                state["pending"] = None
                state["last_model_error"] = None
                trace.emit("model_response", {"request": counts["model_requests"], "provider": completion.provider,
                                              "model": completion.model, "usage": completion.raw.get("usage")},
                           visible=({"text": completion.text, "tool_calls": [c.to_wire() for c in completion.tool_calls]}
                                    if native else completion.text))
                review_conflict = False
                try:
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
                    elif native:
                        final_instruction = ("call mars_submit_document with complete metadata and body"
                                             if request.final_schema is not None else "submit the complete Markdown candidate")
                        state["feedback"] = (f"Protocol error: {exc}. Use valid native research tool calls or {final_instruction}. "
                                             "For document submission, arguments must be exactly one JSON object with "
                                             "two keys: metadata and body. Close metadata before body; close the root "
                                             "once after body. Do not append another body or object after the root. "
                                             "Resolve the pinned candidate validation errors too. No rejected action was executed.")
                    trace.emit("protocol_error", {"error": str(exc)})
                    if counts["protocol_repairs"] > p.max_protocol_repairs:
                        state["status"] = "protocol_exhausted"
                        break
                    trace.snapshot(state)
                    continue
                state["protocol_output"] = ""
                if reviewing:
                    counts["reflections"] += 1
                    state["reviewed_candidate_sha"] = digest(state["candidate"])
                    state["review_issues"] = decision["issues"]
                    trace.emit("reflection", {"accept": decision["accept"], "round": counts["reflections"],
                                              "host_conflict_rejection": review_conflict}, visible=decision)
                    await progress("review", accepted=decision["accept"], issues=decision["issues"])
                    if decision["accept"]:
                        state["reflection_accepted"] = True
                        state["feedback"] = ""
                        state["status"] = "passed"
                        break
                    state["feedback"] = canonical({"required_revision": decision["issues"],
                                                   "review_rationale": decision["rationale"],
                                                   "instruction": "Revise the complete candidate to resolve these issues. Do not merely remove warnings."})
                    state["next_phase"] = "act"
                    if counts["reflections"] >= p.max_reflections:
                        state["status"] = "reflection_rejected"
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
                state["status"] = "budget_exhausted"
        except asyncio.CancelledError:
            state["status"] = "interrupted"
            if state["pending"] == "model":
                state["usage_complete"] = False
            trace.emit("interrupted", {"pending": state["pending"]})
            raise
        except Exception as exc:
            state["status"] = "error"
            trace.emit("error", {"error_type": type(exc).__name__, "message": str(exc)[:500]})
            raise
        finally:
            trace.emit("finished", {"status": state["status"]})
            trace.snapshot(state)
            await request.provider.close()
            cfg.attempt_observer = None
            await progress("finished", status=state["status"])
        return LoopResult(state["candidate"], state["status"], state["history"], dict(counts),
                          request.trace_root, state["reflection_accepted"])
