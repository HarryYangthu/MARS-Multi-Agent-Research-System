"""Factual publication and bounded continuation around real Commander tools.

These policies inspect observations; they never execute a model, create a run,
infer training parameters, or turn a source inspection into a success receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from pathlib import PurePosixPath
import re
from typing import Any

from app.bridge.commander_existing import existing_model_turn_reply
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.harness.runtime.conversation_state import ConversationState


RUN_DISPATCH_TOOLS = frozenset({"run.existing_model", "run.create", "run.start", "create_and_start_run"})
_SOURCE_TOOLS = frozenset({"code.repo_list", "code.repo_search", "code.repo_read_lines"})
_FALLBACK_STATUSES = frozenset({"model_not_resolved", "capability_unavailable", "execution_registration_required"})
PREPARING_NOTICE = "MARS 正在核对本次任务配置，创建和启动结果以实际回执为准。"
UNSTARTED_NOTICE = "MARS 尚未创建或启动本次任务；你的请求和本轮核对记录已保留。"


@dataclass(frozen=True)
class DispatchReceipt:
    reply: str
    executing: bool


def has_run_dispatch(actions: list[dict[str, Any]]) -> bool:
    return any(action.get("tool") in RUN_DISPATCH_TOOLS for action in actions)


def stale_executing_without_run(session: CommanderSession) -> bool:
    """Repair only an old premature state backed by a real failed admission.

    A retained link, a processing turn, or any successful/retained run receipt
    makes this reset inapplicable. Historical messages remain unchanged.
    """
    if session.processing or session.state != ConversationState.EXECUTING or session.linked_run_id:
        return False
    observations = [message for message in session.context_messages()
                    if message.role == "tool" and message.tool_result]
    if any(isinstance((message.tool_result or {}).get("run_id"), str)
           and bool((message.tool_result or {}).get("run_id")) for message in observations):
        return False
    return any(message.tool_name in RUN_DISPATCH_TOOLS and (message.tool_result or {}).get("ok") is False
               for message in observations)


def run_dispatch_receipt(tool: str, result: dict[str, Any]) -> DispatchReceipt | None:
    """Render only the result of a known creation/start operation.

    Callers pass the actual tool return, never a model-authored observation.
    Starting the workflow does not certify that training has started or passed.
    """
    run_id = result.get("run_id")
    if tool not in RUN_DISPATCH_TOOLS or result.get("ok") is not True or not isinstance(run_id, str) or not run_id:
        return None
    if tool == "run.existing_model":
        reply = existing_model_turn_reply(tool, result)
        return DispatchReceipt(reply, result.get("status") == "preparation_started") if reply else None
    if tool == "run.create":
        if not isinstance(result.get("entrypoint"), str) or not isinstance(result.get("states"), dict):
            return None
        return DispatchReceipt(f"已创建任务：{run_id}。任务尚未启动。", False)
    if result.get("status") not in {"started", "already_running"}:
        return None
    if result["status"] == "already_running":
        return DispatchReceipt(f"任务正在运行：{run_id}，无需重复启动。", True)
    return DispatchReceipt(f"已启动任务流程：{run_id}。训练进度和结果以实际执行记录为准。", True)


def retained_dispatch_failure_reply(tool: str, result: dict[str, Any]) -> str | None:
    """A failed start with an allocated run ends admission, not its history."""
    run_id = result.get("run_id")
    if result.get("status") == "existing_run_recovery_required":
        return None  # No creation/start was attempted; inspect the existing run.
    if tool in RUN_DISPATCH_TOOLS and result.get("ok") is False and isinstance(run_id, str) and run_id:
        return truthful_unstarted_reply("", retained_run_id=run_id)
    return None


def _turn_observations(session: CommanderSession) -> list[ChatMessage]:
    if session.active_turn_id is None:
        return []
    return [message for message in session.context_messages()
            if message.turn_id == session.active_turn_id and message.role == "tool" and message.tool_result]


def _has_created_run(observations: list[ChatMessage]) -> bool:
    # Even an unsuccessful start can return the run it already created. Never
    # restart admission or create a replacement for that retained task.
    return any(message.tool_name in RUN_DISPATCH_TOOLS
               and isinstance((message.tool_result or {}).get("run_id"), str)
               and bool((message.tool_result or {}).get("run_id")) for message in observations)


def existing_model_fallback_action(session: CommanderSession) -> dict[str, Any] | None:
    """Supply at most one real, read-only fallback after shortcut admission fails.

    A failed lookup says nothing about source absence. Candidate selection and
    other genuine clarification statuses are deliberately outside this policy.
    """
    observations = _turn_observations(session)
    if _has_created_run(observations):
        return None
    failed_indices = [index for index, message in enumerate(observations)
                      if message.tool_name == "run.existing_model" and (message.tool_result or {}).get("ok") is False
                      and (message.tool_result or {}).get("status") in _FALLBACK_STATUSES]
    if failed_indices and any(message.tool_name in _SOURCE_TOOLS
                              for message in observations[failed_indices[0] + 1:]):
        return None
    for index in range(len(observations) - 1, -1, -1):
        message = observations[index]
        result = message.tool_result or {}
        if message.tool_name != "run.existing_model":
            continue
        if result.get("ok") is not False or result.get("status") not in _FALLBACK_STATUSES:
            return None
        if result.get("candidates"):
            return None
        # One ordinary inspection attempt, including an actual failed read, is
        # enough to consume this host fallback for the entire turn.
        raw_path = (message.tool_args or {}).get("config_path")
        if isinstance(raw_path, str) and raw_path.strip():
            path = raw_path.replace("\\", "/")
            relative = PurePosixPath(path)
            if (not relative.is_absolute() and ".." not in relative.parts and ":" not in path
                    and relative.suffix.lower() in {".yaml", ".yml", ".json", ".toml"}):
                return {"tool": "code.repo_read_lines", "args": {"path": relative.as_posix()}}
        return {"tool": "code.repo_list", "args": {"path": ".", "depth": 1}}
    return None


def pending_existing_admission(session: CommanderSession) -> dict[str, Any] | None:
    """Restore one explicitly continued, never-created request before the model.

    Exact args come from its actual failed admission. The real tool re-reads
    current configuration and validates original user intent, registration,
    budget and seed; this policy cannot grant an execution capability.
    """
    from app.bridge.commander_existing_sources import pending_existing_request

    pending = pending_existing_request(session)
    if pending is None or any(message.tool_name == "run.existing_model" for message in _turn_observations(session)):
        return None
    args = pending.get("args")
    if not isinstance(args, dict):
        return None
    path = args.get("config_path")
    if (not isinstance(path, str) or not path or ":" in path
            or any(character in path for character in "*?[")
            or PurePosixPath(path.replace("\\", "/")).is_absolute()
            or ".." in PurePosixPath(path.replace("\\", "/")).parts
            or type(args.get("max_iters")) is not int or args["max_iters"] < 1
            or not isinstance(args.get("budget_unit"), str) or args["budget_unit"] not in {"steps", "epochs"}):
        return None
    return {"tool": "run.existing_model", "args": deepcopy(args)}


def existing_run_recovery_guard(session: CommanderSession, actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A real recovery redirect cannot be followed by another creation attempt."""
    if not any(action.get("tool") in {"run.existing_model", "run.create", "create_and_start_run"} for action in actions):
        return None
    for message in reversed(_turn_observations(session)):
        result = message.tool_result or {}
        if (message.tool_name == "run.existing_model" and result.get("ok") is False
                and result.get("status") == "existing_run_recovery_required"
                and isinstance(result.get("run_id"), str) and result["run_id"]
                and result["run_id"] == session.linked_run_id):
            return {"tool": "run.recovery_status", "args": {"run_id": result["run_id"]}}
    return None


def unfulfilled_run_dispatch(session: CommanderSession) -> bool:
    observations = [message for message in _turn_observations(session) if message.tool_name in RUN_DISPATCH_TOOLS]
    if observations and (observations[-1].tool_result or {}).get("status") == "existing_run_recovery_required":
        return False
    return bool(observations) and run_dispatch_receipt(
        observations[-1].tool_name or "", observations[-1].tool_result or {}) is None


def retained_dispatch_run_id(session: CommanderSession) -> str | None:
    for message in reversed(_turn_observations(session)):
        if message.tool_name in RUN_DISPATCH_TOOLS:
            run_id = (message.tool_result or {}).get("run_id")
            return run_id if isinstance(run_id, str) and run_id else None
    return None


def dispatch_failure_reason(session: CommanderSession) -> str:
    for message in reversed(_turn_observations(session)):
        if message.tool_name in RUN_DISPATCH_TOOLS:
            result = message.tool_result or {}
            error = result.get("error")
            return error.strip()[:500] if result.get("ok") is False and isinstance(error, str) else ""
    return ""


def truthful_unstarted_reply(reply: str, *, retained_run_id: str | None = None, failure_reason: str = "") -> str:
    """Keep concrete questions, but do not finish an operation with a promise.

    This applies only after this turn actually attempted admission/start without
    a success receipt. Ordinary conversation and prior turns do not enter it.
    """
    notice = (f"MARS 未收到本次启动成功回执；已有任务 {retained_run_id} 已保留，不会重复创建。"
              if retained_run_id else UNSTARTED_NOTICE)
    if failure_reason:
        notice += f"\n\n本次核对结果：{failure_reason}"
    claimed = re.search(r"已[^。！？\n]{0,16}(?:启动|创建|完成)|正在(?:运行|训练)|\b(?:started|created|running|completed)\b",
                        reply, re.IGNORECASE)
    question = ("?" in reply or "？" in reply
                or re.search(r"请(?:选择|提供|确认|补充|告诉)", reply) is not None)
    promise = re.search(r"(?:改走|接下来|稍后|我将|我会|我先|先读取|再创建|继续核对)", reply)
    retain = bool(reply.strip()) and not claimed and (question or not promise)
    return f"{notice}\n\n{reply}" if retain else notice
