"""Native bounded ReAct/Reflection engine; framework adapters implement one Protocol."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import INSTRUCTION, parse_action, parse_review
from app.harness.agent_loop.trace import LoopTrace, atomic_json, canonical, digest
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig, LLMProvider, Message, llm_call_deadline_seconds
from app.harness.tools.registry import ToolContext, ToolRegistry

Validator = Callable[[str, list[dict[str, Any]]], Awaitable[list[str]]]


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


class NativeAgentLoop:
    async def run(self, request: LoopInput) -> LoopResult:
        p = request.policy
        specs = []
        for name in request.tools:
            spec = request.registry.spec(name)
            if spec is None or spec.bridge_only:
                raise ValueError(f"configured tool has no executable specification: {name}")
            specs.append({"name": name, "description": spec.description, "args_schema": spec.input_schema})
        pinned = list(request.messages) + [Message(role="system", content=INSTRUCTION + "\nTools:\n" + canonical(specs))]
        fingerprint = digest({"messages": [asdict(x) for x in pinned], "policy": asdict(p),
                              "model": request.config.model, "provider": request.config.provider,
                              "project": request.tool_context.project, "tools": specs})
        trace = LoopTrace(request.trace_root, p.trace, resume=request.resume)
        state: dict[str, Any] = {
            "fingerprint": fingerprint, "status": "running", "pending": None,
            "counts": {k: 0 for k in ("model_requests", "model_responses", "tool_dispatches",
                                     "observations", "sdk_attempts", "action_rounds", "protocol_repairs",
                                     "validation_repairs", "reflections")},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "usage_complete": True, "history": [], "candidate": "", "feedback": "",
            "next_phase": "act", "seen": {}, "reflection_accepted": False,
        }
        if request.resume:
            if p.trace != "full":
                raise ValueError("resume requires full trace/checkpoint mode")
            state = json.loads((request.trace_root / "checkpoint.json").read_text())
            if state["fingerprint"] != fingerprint or state["status"] not in {"running", "interrupted", "model_error"}:
                raise ValueError("resume requires identical inputs/configuration and an interrupted/model-error run")
            if state["pending"] == "tool":
                raise ValueError("tool outcome unknown: reconcile its receipt before resuming; automatic replay forbidden")
            if state["pending"] == "model":
                state["usage_complete"] = False
            state["status"] = "running"
            state["pending"] = None
        counts = state["counts"]
        trace.emit("resumed" if request.resume else "started", {"fingerprint": fingerprint})
        trace.snapshot(state)
        cfg = request.config
        cfg.json_mode = True

        def on_attempt(kind: str, data: dict[str, Any]) -> None:
            if kind == "sdk_attempt_started":
                counts["sdk_attempts"] += 1
            trace.emit(kind, {"request": counts["model_requests"], **data})
            trace.snapshot(state)

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
        try:
            for _ in range(max(0, p.max_model_calls - counts["model_requests"])):
                reviewing = state["next_phase"] == "reflect"
                extra: list[Message] = []
                if reviewing:
                    extra = [Message(role="system", content=(
                        "You are reviewing the current candidate, not generating tool actions. "
                        'Return exactly {"accept":bool,"issues":["specific unresolved issue"],"rationale":"brief review"}. '
                        "Accept only if no material issue remains. Self-review is not independent scientific validation.\n"
                        + request.reflection_rubric))]
                feedback = state["feedback"]
                if counts["tool_dispatches"] >= p.max_tool_steps:
                    feedback += "\nTool budget exhausted. Return a final grounded document or explicit evidence gaps."
                messages, manifest = pack_context(
                    pinned + extra, state["history"], feedback, state["candidate"],
                    budget=p.input_token_budget, observation_chars=p.observation_chars,
                )
                counts["model_requests"] += 1
                state["pending"] = "model"
                trace.emit("context_packed", manifest)
                trace.emit("model_request", {"request": counts["model_requests"], "phase": state["next_phase"]},
                           visible=[asdict(m) for m in messages])
                trace.snapshot(state)
                try:
                    completion = await asyncio.wait_for(
                        request.provider.complete(messages, cfg), timeout=llm_call_deadline_seconds(cfg))
                except Exception as exc:
                    usage(getattr(exc, "usage", None))
                    if isinstance(exc, LLMCompletionError):
                        counts["model_responses"] += 1
                        trace.emit("model_response", {"request": counts["model_requests"],
                                                      "rejected": True, "reason": exc.reason,
                                                      "usage": exc.usage})
                        state["pending"] = None
                    state["status"] = "model_error"
                    trace.emit("model_error", {"error_type": type(exc).__name__, "reason": getattr(exc, "reason", None)})
                    break
                if completion.is_mock or completion.provider in {"mock", "fake"}:
                    raise RuntimeError("non-real completion rejected")
                counts["model_responses"] += 1
                usage(completion.raw.get("usage"))
                state["pending"] = None
                trace.emit("model_response", {"request": counts["model_requests"], "provider": completion.provider,
                                              "model": completion.model, "usage": completion.raw.get("usage")},
                           visible=completion.text)
                try:
                    decision = parse_review(completion.text) if reviewing else parse_action(completion.text)
                except ValueError as exc:
                    counts["protocol_repairs"] += 1
                    state["feedback"] = f"Protocol error: {exc}. Return the required JSON object."
                    trace.emit("protocol_error", {"error": str(exc)})
                    if counts["protocol_repairs"] > p.max_protocol_repairs:
                        state["status"] = "protocol_exhausted"
                        break
                    trace.snapshot(state)
                    continue
                if reviewing:
                    counts["reflections"] += 1
                    trace.emit("reflection", {"accept": decision["accept"], "round": counts["reflections"]}, visible=decision)
                    if decision["accept"]:
                        state["reflection_accepted"] = True
                        state["status"] = "passed"
                        break
                    state["feedback"] = canonical(decision)
                    state["next_phase"] = "act"
                    if counts["reflections"] >= p.max_reflections:
                        state["status"] = "reflection_rejected"
                        break
                elif "final" in decision:
                    state["candidate"] = decision["final"]
                    errors = await request.validate(state["candidate"], state["history"])
                    trace.emit("validation", {"valid": not errors}, visible=errors)
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
                        if result.requires_approval or result.blocked_by_gate:
                            state["status"] = "blocked"
                            break
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
        return LoopResult(state["candidate"], state["status"], state["history"], dict(counts),
                          request.trace_root, state["reflection_accepted"])
