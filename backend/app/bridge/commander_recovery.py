"""Recover interrupted model generation without replaying uncertain operations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.bridge.commander_session import CommanderSession
from app.harness.llm.accounting import ResourceBudgetError, RunModelBudget

_COMMANDER_MODEL_NODES = frozenset({"commander", "commander_native", "commander_completion"})


def _pending_mutations(root: Path) -> list[str]:
    path = root / "action_journal.json"
    if path.is_symlink():
        raise ValueError("操作记录不能是符号链接。")
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise ValueError("操作记录格式无法核对。")
    pending = []
    for row in raw["entries"]:
        if (not isinstance(row, dict) or type(row.get("mutation")) is not bool
                or row.get("state") not in {"running", "completed"}):
            raise ValueError("操作记录状态无法核对。")
        if row["mutation"] and row["state"] == "running":
            pending.append(str(row.get("id") or row.get("tool") or "unknown"))
    return pending


def recover_interrupted_model_requests(root: Path, session: CommanderSession) -> dict[str, Any]:
    """Called before a new turn; retain unknown usage and every reserved charge.

    Only model generations owned by this conversation are acknowledged after a
    real file-lock probe proves their owner is gone. This does not replay a
    request, execute an action, create an answer or reconcile another Agent.
    """
    result: dict[str, Any] = {"schema": "commander.model_recovery.v1", "status": "idle",
                             "recovered_request_ids": [], "usage_unknown_preserved": True}

    def blocked(code: str, message: str) -> dict[str, Any]:
        return {**result, "status": "blocked", "error_code": code, "message": message}

    if session.processing:
        return blocked("conversation_still_processing", "当前对话仍在处理，请等待已有操作结束。")
    ledger = root / "resources/model_budget.v1.json"
    if not ledger.exists():
        return result
    if ledger.is_symlink():
        return blocked("model_ledger_unreadable", "模型调用记录路径无法核对，已保留已有记录。")
    try:
        budget = RunModelBudget(root, active_model_time=True)
        state = budget.recovery_snapshot()
        rows = {identifier: row for identifier, row in state["requests"].items()
                if row["status"] in {"in_flight", "reconciliation_required"}}
        if not rows:
            return result
        # Pending writes remain the dispatch journal's responsibility. Model
        # recovery must still permit read-only observations to establish what
        # happened; it never marks a write completed or replays its arguments.
        try:
            pending = _pending_mutations(root)
        except (OSError, ValueError):
            pending = []
            result["action_journal_warning"] = "操作记录暂时无法核对；需要保留原记录并核对后再修改或启动。"
        if pending:
            result["pending_action_ids"] = pending
        user_turns = {message.id for message in session.messages if message.role == "user"}
        for row in rows.values():
            correlation = row.get("correlation")
            if (not isinstance(correlation, dict) or correlation.get("trace_id") != session.conv_id
                    or correlation.get("node_id") not in _COMMANDER_MODEL_NODES
                    or correlation.get("turn_id") and correlation["turn_id"] not in user_turns):
                return blocked("model_recovery_scope_mismatch",
                    "存在不属于当前对话或无法对应用户请求的中断记录，MARS保留记录并等待核对。")
        abandoned = budget.recover_abandoned(request_ids=tuple(rows))
        current = budget.recovery_snapshot()
        if any(current["requests"][identifier]["status"] == "in_flight" for identifier in rows):
            return blocked("model_request_still_running", "原模型请求仍在运行，MARS不会重复发送。")
        for identifier in abandoned:
            refs: tuple[str, ...] = (f"resources/model_budget.v1.json#requests/{identifier}",
                    f"lease:resources/requests/{identifier}.lock:owner_not_held",
                    f"conversation:{session.conv_id}")
            if (root / "action_journal.json").exists():
                refs += ("action_journal.json#pending_operations_not_replayed",)
            budget.reconcile_abandoned(identifier, actor="mars-conversation-recovery",
                reason="Model owner exited; original response and usage remain unknown; full reservation retained.",
                evidence_refs=refs)
            result["recovered_request_ids"].append(identifier)
        if result["recovered_request_ids"]:
            result.update(status="recovered", message="已恢复中断的模型调用记录；已有进度和原调用额度保留。")
            remaining = budget.remaining_seconds()
            result["model_activity_seconds_remaining"] = remaining
            if remaining <= 0:
                return blocked("model_activity_budget_exhausted",
                    "中断记录已恢复，历史模型调用耗时已达到当前预算；请调整预算后继续，已有用量不会清零。")
        return result
    except (OSError, ValueError, ResourceBudgetError) as exc:
        return blocked("model_ledger_unreadable", f"模型调用记录暂时无法核对，已保留已有记录：{exc}")
