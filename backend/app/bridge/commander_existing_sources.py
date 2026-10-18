"""Actual user provenance for one unfinished existing-model admission.

Only explicit retry messages may span successful read-only inspection turns.
This never interprets scientific parameters or grants an execution capability.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.bridge.commander_session import ChatMessage, CommanderSession


_SOURCE_TOOLS = frozenset({"code.repo_list", "code.repo_search", "code.repo_read_lines"})
_FAILED_ADMISSIONS = frozenset({
    "model_not_resolved", "capability_unavailable", "execution_registration_required",
})


def existing_retry(text: str) -> bool:
    return re.fullmatch(
        r"\s*(?:好(?:的)?[，,\s]*)?(?:请)?(?:继续|重试|重新尝试|再试一次)(?:一下|吧)?[。.!！?？\s]*", text) is not None


def _has_run_reference(value: Any) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if any(key in {"run_id", "linked_run_id"} and item is not None and item != ""
                   for key, item in current.items()):
                return True
            pending.extend(item for item in current.values() if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            pending.extend(item for item in current if isinstance(item, (dict, list)))
    return False


def _turn_admissions(messages: list[ChatMessage]) -> list[dict[str, Any]] | None:
    """Allow successful source observations and unsuccessful admission only."""
    admissions = []
    for message in messages:
        if message.role != "tool":
            continue
        result = message.tool_result or {}
        if (message.tool_name == "run.existing_model" and result.get("ok") is False
                and isinstance(result.get("status"), str) and result["status"] in _FAILED_ADMISSIONS
                and not result.get("candidates")
                and isinstance(message.tool_args, dict) and message.tool_args):
            admissions.append(message.tool_args)
        elif (message.tool_name in {"run.create", "create_and_start_run"} and result.get("ok") is False
              and result.get("status") == "repository_inspection_required"
              and isinstance(message.tool_args, dict)):
            continue  # Real source-admission rejection; no task was allocated.
        elif message.tool_name not in _SOURCE_TOOLS or result.get("ok") is not True:
            return None
    return admissions


def _same_unexecuted_creation(args: dict[str, Any], original: dict[str, Any]) -> bool:
    """Generic failed admission is a boundary, never a new parameter source."""
    if args.get("entrypoint", "coding") != "coding" or args.get("standalone") or args.get("seed_artifact"):
        return False
    job = args.get("existing_execution_job", {})
    if not isinstance(job, dict):
        return False
    for values in (args, job):
        for key in ("config_path", "model_name", "max_iters", "budget_unit", "seed"):
            if values.get(key) is None:
                continue
            if values[key] != original.get(key) or type(values[key]) is not type(original.get(key)):
                return False
    if job.get("entrypoint") is not None and original.get("entrypoint") is not None:
        return bool(job["entrypoint"] == original["entrypoint"])
    return True


def pending_existing_request(session: CommanderSession) -> dict[str, Any] | None:
    """Return original failed args across one explicit, read-only retry chain.

    Successful/retained run identities, candidate questions, changed admission
    args, unrelated tools and independent user instructions end this policy.
    A caller must still read/resolve current source before any new admission.
    """
    messages = session.context_messages()
    users = [message for message in messages if message.role == "user"]
    if (session.linked_run_id or len(users) < 2 or not existing_retry(users[-1].content)
            or any(_has_run_reference(message.tool_result) or _has_run_reference(message.tool_args)
                   for message in messages if message.role == "tool")):
        return None
    chain: list[ChatMessage] = []
    seen_admissions: list[dict[str, Any]] = []
    failed_creations: list[dict[str, Any]] = []
    for index in range(len(users) - 1, -1, -1):
        user = users[index]
        turn = [message for message in messages if message.turn_id == user.id]
        admissions = _turn_admissions(turn)
        if admissions is None:
            return None
        seen_admissions.extend(admissions)
        failed_creations.extend(message.tool_args or {} for message in turn
                                if message.role == "tool" and message.tool_name in {"run.create", "create_and_start_run"})
        chain.insert(0, user)
        if not existing_retry(user.content):
            if not admissions:
                return None
            original = admissions[-1]
            if any(args != original for args in seen_admissions):
                return None
            if any(not _same_unexecuted_creation(args, original) for args in failed_creations):
                return None
            return {"args": deepcopy(original), "source_turn_id": user.id,
                    "source_messages": [{"id": item.id, "content": item.content} for item in chain]}
        if index != len(users) - 1 and not any(message.role == "tool" for message in turn):
            return None  # Do not invent a missing inspection/admission history.
    return None


def existing_user_sources(session: CommanderSession) -> list[dict[str, str]]:
    """Preserve ordinary immediate choice handling and scoped retry evidence."""
    messages = session.context_messages()
    users = [message for message in messages if message.role == "user"]
    if not users:
        return []
    pending = pending_existing_request(session)
    if pending is not None:
        return list(pending["source_messages"])
    selected = [users[-1]]
    if (len(users) > 1 and not session.linked_run_id and not existing_retry(users[-1].content)
            and not any(_has_run_reference(message.tool_result) or _has_run_reference(message.tool_args)
                        for message in messages if message.role == "tool")):
        previous = users[-2]
        if any(message.tool_name == "run.existing_model" and message.turn_id == previous.id
               and message.tool_result and message.tool_result.get("status") in {
                   "model_selection_required", "entrypoint_selection_required"}
               for message in messages):
            selected.insert(0, previous)
    return [{"id": message.id, "content": message.content} for message in selected]
