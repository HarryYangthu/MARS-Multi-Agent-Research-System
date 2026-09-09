"""Narrow author-side recovery from a completed, rejected empty response.

These pure contracts neither call a provider nor turn a failure into an answer.
The executor supplies its actual typed error and the response it just journaled.
"""
from __future__ import annotations

from typing import Any

from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import LLMCompletionError


def author_empty_completion_recovery(error: Exception | None, *, response: dict[str, Any] | None,
                                     state: dict[str, Any], policy: AgentLoopPolicy,
                                     effort: str | None) -> dict[str, Any] | None:
    """Plan one new author call inside existing budgets, never an SDK retry."""
    if (not policy.author_empty_completion_repair_enabled or state.get("next_phase") != "act"
            or not isinstance(error, LLMCompletionError) or response is None):
        return None
    reason = error.reason
    if (reason.get("code") != "empty_final_content" or reason.get("empty_final") is not True
            or reason.get("finish_reason") != "stop" or state.get("status") != "model_error"
            or state.get("last_model_error") != reason):
        return None
    usage = error.usage
    if (not isinstance(usage, dict)
            or any(type(usage.get(key)) is not int or usage[key] < 0
                   for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
            or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]):
        return None
    counts = state["counts"]
    if (state.get("pending") is not None or state.get("pending_batch")
            or counts["model_requests"] != counts["model_responses"] or counts["model_requests"] < 1
            or response.get("kind") != "model_response" or response.get("rejected") is not True
            or response.get("request") != counts["model_requests"] or response.get("reason") != reason
            or response.get("usage") != usage or type(response.get("event_seq")) is not int
            or response["event_seq"] < 1 or "visible" in response
            or counts["protocol_repairs"] >= policy.max_protocol_repairs
            or counts["model_requests"] >= policy.max_model_calls):
        return None
    return {
        "code": "empty_final_content", "recovery_kind": "author_empty_completed_response",
        "previous_effort": effort, "reasoning_effort": effort,
        "response_event_seq": response["event_seq"],
        "response_metadata_sha256": digest({key: response[key] for key in
                                            ("event_seq", "kind", "request", "rejected", "reason", "usage")}),
        "feedback": "The last model response completed with no usable visible action or final content and was rejected. "
                    "Return one valid action under the current action protocol or a complete final submission. "
                    "Use the existing observations and continue the unresolved task; do not repeat completed research "
                    "without a specific evidence gap. No tool action or candidate was accepted from the empty response.",
    }


def apply_author_empty_completion_recovery(error: Exception | None, *, response: dict[str, Any] | None,
                                           state: dict[str, Any], policy: AgentLoopPolicy,
                                           effort: str | None) -> dict[str, Any] | None:
    """Persistable state change; the executor journals it before another call."""
    plan = author_empty_completion_recovery(error, response=response, state=state, policy=policy, effort=effort)
    if plan is None:
        return None
    state["counts"]["protocol_repairs"] += 1
    state["feedback"] = plan["feedback"] + ("\n" + state["feedback"] if state["feedback"] else "")
    state["pending"] = None
    state["status"] = "running"
    state["last_model_error"] = None
    return plan


def validate_author_empty_recovery_resume(state: dict[str, Any], policy: AgentLoopPolicy) -> None:
    """A saved repair may continue; a terminal failure cannot obtain a fresh budget."""
    if not policy.author_empty_completion_repair_enabled:
        return
    reason = state.get("last_model_error")
    if (state.get("pending") == "model"
            or isinstance(reason, dict) and reason.get("code") == "empty_final_content"):
        # A successful in-loop recovery records its consumed repair and clears
        # last_model_error synchronously. Anything else lacks that durable
        # authorization; do not reconstruct a second draw on resume.
        raise ValueError("no persisted bounded author recovery, or model outcome unknown; automatic replay forbidden")
