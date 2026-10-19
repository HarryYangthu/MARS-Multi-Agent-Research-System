"""Factual publication and bounded continuation around real Commander tools.

These policies inspect observations; they never execute a model, create a run,
infer training parameters, or turn a source inspection into a success receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
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


@dataclass(frozen=True)
class ActionContinuation:
    """An unfinished conversational step, never an inferred tool operation."""

    reason: str
    fingerprint: str
    prompt: str


_READ_ONLY_TOOLS = _SOURCE_TOOLS | frozenset({
    "routing.repository_check", "context.read_material", "run.status", "get_run_status",
    "run.recovery_status", "artifact.read", "metrics.evaluate", "diagnosis.failure_analysis",
    "get_diagnosis", "query_kb", "list_runs", "discovery.status", "knowledge.research_memory_read",
})
_ACTION_VERBS = (
    r"读取|查找|搜索|检查|核对|修改|调整|配置|创建|启动|运行|执行|训练|仿真|"
    r"保存|提交|导出|生成|停止|恢复|重试|取消|删除|批准|"
    r"read|inspect|search|check|modify|configure|create|start|run|train|save|export|resume|retry"
)


def _operation_promise(reply: str) -> bool:
    # Advice addressed to the user is not the MARS controller taking an action.
    return bool(re.search(
        rf"(?:MARS|主控|我)(?:[^。！？\n]{{0,45}}?)(?:正在|将|会|先|接下来|下一步|现在)"
        rf"(?:[^。！？\n]{{0,25}}?)(?:{_ACTION_VERBS})|"
        rf"^(?:接下来|下一步|现在|先)(?:[^。！？\n]{{0,15}}?)(?:{_ACTION_VERBS})|"
        rf"\b(?:I|MARS)\s+(?:will|am\s+going\s+to|am\s+now)\b[^.!?\n]{{0,60}}(?:{_ACTION_VERBS})",
        reply.strip(), re.IGNORECASE))


def _concrete_question(reply: str, response_mode: str) -> bool:
    if response_mode == "clarification":
        return True
    return bool(re.search(r"请(?:你|您)?(?:选择|提供|补充|确认|告诉)|"
                          r"(?:哪(?:个|种|些)|多少|还是|是否|需要.{0,20}(?:信息|参数|路径))[^。\n]*[？?]|"
                          r"\b(?:which|what|please\s+(?:choose|provide|confirm))\b[^\n]*[?]",
                          reply, re.IGNORECASE))


def _operation_requested(session: CommanderSession) -> bool:
    users = [message.content for message in session.context_messages()
             if message.role == "user" and message.turn_id == session.active_turn_id]
    if not users:
        return False
    text = users[-1].strip()
    if re.search(r"(?:先|暂时)?不要(?:修改|执行|运行|启动|行动|继续)|先别|别再|"
                 r"(?:先|仅|只)(?:讨论|解释|建议)|只(?:要|想)(?:建议|了解|知道)|"
                 r"^(?:为什么|为何|什么(?:是|叫|意思)|如何|怎么|解释|说明|讲解)|"
                 r"^(?:why|what|how|explain)\b|(?:do\s+not|don't)\s+(?:execute|run|start|modify)|"
                 r"(?:only|just)\s+(?:explain|discuss|advice)", text, re.IGNORECASE):
        return False
    information_request = re.search(
        r"^(?:帮我|请|给我)?(?:查看|查询|看一下|看下|看看|解释|说明|分析|总结|告诉)|"
        r"(?:状态|进展|结果|情况).{0,16}(?:怎么样|如何|是什么|说明|含义|意思)|"
        r"(?:启动|运行|训练)(?:了|完)?吗[？?]?$", text)
    if information_request and not re.search(
            r"(?:然后|之后|再|并)(?:[^。！？\n]{0,15})(?:启动|运行|训练|修改|停止|恢复|创建)", text):
        return False
    return bool(re.search(r"^(?:请|帮我)?(?:先)?(?:读取|查找|搜索|检查|核对)|"
                          r"运行|启动|仿真|训练|修改|改成|调整|增加|减半|创建|导出|保存|停止|"
                          r"恢复|重试|重新|继续|生成|删除|取消|批准|"
                          r"\b(?:run|start|train|modify|create|export|save|stop|resume|retry|generate)\b",
                          text, re.IGNORECASE))


def _progress_fingerprint(session: CommanderSession, observations: list[ChatMessage]) -> str:
    signatures = set()
    for message in observations:
        result = {key: value for key, value in (message.tool_result or {}).items()
                  if key not in {"tool_call_id", "request_id"}}
        signatures.add(json.dumps({"tool": message.tool_name, "args": message.tool_args or {},
                                   "result": result}, ensure_ascii=False, sort_keys=True, default=str))
    # Re-reading the same bytes with a new transport call ID is not new evidence.
    return hashlib.sha256(json.dumps(sorted(signatures), ensure_ascii=False).encode()).hexdigest()


def action_continuation(session: CommanderSession, reply: str, *,
                        response_mode: str = "structured") -> ActionContinuation | None:
    """Detect a pending action without supplying parameters or performing it.

    The caller corrects each distinct evidence state at most once and retains
    the existing turn budget. A genuine question or successful side effect is
    not a reason to replay anything. Previous turns confer no completion here.
    """
    observations = _turn_observations(session)
    if _concrete_question(reply, response_mode):
        return None
    users = [message.content.strip().rstrip("。！？!? ") for message in session.context_messages()
             if message.role == "user" and message.turn_id == session.active_turn_id]
    if (users and re.fullmatch(r"(?:请|帮我)?(?:继续|重试|恢复)(?:一下|吧|任务|运行)?", users[-1])
            and not _operation_promise(reply) and re.search(r"无需|不需要|不用|不必", reply)
            and re.search(r"重复|重启|重新启动", reply)
            and any(message.tool_name == "run.recovery_status"
                    and (message.tool_result or {}).get("ok") is True
                    and (message.tool_result or {}).get("status") in {"running", "already_running"}
                    and (message.tool_result or {}).get("run_id") == session.linked_run_id
                    and session.linked_run_id for message in observations)):
        return None
    if any((message.tool_result or {}).get("ok") is True
           and message.tool_name not in _READ_ONLY_TOOLS for message in observations):
        return None
    if any((message.tool_result or {}).get("requires_approval") is True
           or (message.tool_result or {}).get("blocked_by_gate")
           or (message.tool_result or {}).get("status") in {
               "model_selection_required", "entrypoint_selection_required", "user_intent_confirmation_required",
               "needs_user", "blocked", "waiting_approval", "waiting_review"}
           for message in observations):
        return None
    promise = _operation_promise(reply)
    requested = _operation_requested(session)
    if not requested or not (promise or observations or not reply.strip()):
        return None
    # A clear failed read is a real blocking result, not idle success. The model
    # may explain it or ask for the exact missing information without a retry.
    if observations and (observations[-1].tool_result or {}).get("ok") is False and not promise:
        return None
    return ActionContinuation(
        reason="operation_promise_without_action" if promise else "operation_pending_after_inspection",
        fingerprint=_progress_fingerprint(session, observations),
        prompt=("MARS 本轮尚未完成用户的操作请求；上一条只有下一步说明，没有派发后续操作。"
                "请依据原始用户请求和已有真实 Observation 继续尚未完成的操作，使用可用工具，"
                "不要重放已成功的修改、创建、启动、审批或恢复。不要自行猜测缺失参数、代替用户选择候选或绕过审核。"
                "若确实缺少必要信息，直接提出具体问题；普通问答或工具结果解释已经完成时可自然语言回答。"
                "没有实际成功回执时不得宣称任务已创建、已启动或已完成，也不要只说接下来会做什么。"),
    )


def action_continuation_exhausted_reply(session: CommanderSession) -> str:
    observations = _turn_observations(session)
    successes = [message.tool_name for message in observations if (message.tool_result or {}).get("ok") is True]
    if session.linked_run_id:
        notice = f"MARS 本轮未完成后续操作；已有任务 {session.linked_run_id} 已保留，未重复创建或重放操作。"
    else:
        notice = "MARS 本轮尚未完成操作，也未创建或启动任务；原始请求和实际核对记录已保留。"
    if successes:
        notice += "本轮仅完成了工具核对：" + "、".join(dict.fromkeys(str(name) for name in successes)) + "。"
    return notice + "后续操作没有取得新的进展，已停止本轮自动续行。"


def _current_user_request(session: CommanderSession) -> str:
    return next((message.content for message in reversed(session.context_messages())
                 if message.role == "user" and message.turn_id == session.active_turn_id), "")


def _safe_source_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return None
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or path.as_posix() != value
            or any(character in value for character in "*?[\x00\n\r")
            or path.suffix.lower() not in {".yaml", ".yml", ".json", ".toml", ".py", ".md"}):
        return None
    return value


def _user_source_files(session: CommanderSession) -> list[str]:
    pattern = (r"(?<![A-Za-z0-9_./\\:*?\[])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+"
               r"\.(?:yaml|yml|json|toml|py|md)(?![A-Za-z0-9_./\\*?\[])")
    paths = [_safe_source_path(match.group())
             for match in re.finditer(pattern, _current_user_request(session), re.IGNORECASE)]
    return list(dict.fromkeys(path for path in paths if path is not None))


def _mentioned_files(text: str, files: set[str]) -> list[str]:
    matches = []
    for path in files:
        match = re.search(r"(?<![A-Za-z0-9_./])" + re.escape(path) + r"(?![A-Za-z0-9_./])", text)
        if match is not None:
            matches.append((match.start(), path))
    return [path for _, path in sorted(matches)]


def promised_source_read_action(session: CommanderSession, reply: str, *,
                                response_mode: str = "structured") -> dict[str, Any] | None:
    """Fulfil one read of a complete path given by the actual current user.

    The ordinary reader must resolve it against the current project, verify
    existence, reject symlinks and enforce its read policy. A missing file is a
    real failed observation, never an invitation to invent a replacement path.
    No path, source content, line range, or scientific parameter is invented.
    """
    if (session.linked_run_id or _has_created_run(_turn_observations(session))
            or action_continuation(session, reply, response_mode=response_mode) is None):
        return None
    attempted = {(message.tool_args or {}).get("path") for message in _turn_observations(session)
                 if message.tool_name == "code.repo_read_lines"}
    for path in _user_source_files(session):
        if path not in attempted:
            return {"tool": "code.repo_read_lines", "args": {"path": path}}
    return None


def ordinary_coding_handoff_actions(session: CommanderSession, reply: str, *,
                                    response_mode: str = "structured") -> list[dict[str, Any]] | None:
    """Delegate an explicit change-and-run request verbatim after a real read.

    This supplies the normal creation/start operations only. Ordinary Coding
    owns implementation, scientific interpretation and execution jobs. Neither
    artifact approval nor execution confirmation is supplied here.
    """
    if (session.linked_run_id or _has_created_run(_turn_observations(session))
            or action_continuation(session, reply, response_mode=response_mode) is None):
        return None
    request = _current_user_request(session)
    if (not re.search(r"改(?:动|代码|参数)|修改|调整|增加|减半|\b(?:modify|change|adjust)\b", request, re.IGNORECASE)
            or not re.search(r"仿真|训练|运行|执行|\b(?:run|train|execute)\b", request, re.IGNORECASE)
            or re.search(r"(?:不要|不|先别)(?:启动|运行|训练|执行)|\b(?:do\s+not|don't)\s+(?:run|start|execute)",
                         request, re.IGNORECASE)):
        return None
    reads = set()
    for message in _turn_observations(session):
        result = message.tool_result or {}
        output = result.get("output")
        if (message.tool_name == "code.repo_read_lines" and result.get("ok") is True
                and isinstance(output, dict) and isinstance(output.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", output["sha256"])):
            path = _safe_source_path(output.get("path"))
            if path is not None:
                reads.add(path)
    explicit = [path for path in _mentioned_files(request, reads)
                if PurePosixPath(path).suffix.lower() in {".yaml", ".yml", ".json", ".toml"}]
    if not explicit:
        return None
    return [
        {"tool": "run.create", "args": {"entrypoint": "coding", "repository_scope": "required",
            "routing_reason": "本轮已实际读取用户明确的配置：" + "、".join(explicit)
                + "；按原文要求修改现有实现并运行，由普通 Coding 校验和准备交付。",
            "user_request": request}},
        {"tool": "run.start", "args": {}},
    ]


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
