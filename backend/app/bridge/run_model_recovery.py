"""Acknowledge orphaned model generations bound to real stage checkpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.bridge.node_key import parse_node_key
from app.bridge.task_runtime import task_contract_path
from app.harness.agent_loop.trace import LoopTrace, audit_trace
from app.harness.agent_loop.interrupted_model_resume import recoverable_model_metadata_state
from app.harness.llm.accounting import ResourceBudgetError, RunModelBudget
from app.harness.runtime.task_contract import TaskEnvelope
from app.storage.run_store import RunHandle


def _request_checkpoint(run: RunHandle, row: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    correlation = row.get("correlation")
    if not isinstance(correlation, dict) or correlation.get("trace_id") != run.run_id:
        raise ValueError("模型中断记录不属于当前任务。")
    node, invocation = correlation.get("node_id"), correlation.get("invocation_id")
    if (not isinstance(node, str) or Path(node).name != node or node in {".", ".."}
            or not isinstance(invocation, str) or not invocation.replace("-", "").isalnum()):
        raise ValueError("模型中断记录缺少可核对的阶段与调用信息。")
    archived = run.root / "input/task_contracts/invocations" / (invocation + ".json")
    path = archived if archived.is_file() else task_contract_path(run, node)
    task = TaskEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
    if (task.run_id != run.run_id or task.project != run.project or task.node_id != node
            or task.invocation_id != invocation or task.agent != parse_node_key(node).stage
            or task.agent not in {"idea", "experiment", "coding", "execution", "writing"}
            or correlation.get("task_id") != task.task_id
            or correlation.get("parent_task_id") != task.parent_task_id):
        raise ValueError("模型中断记录与阶段任务绑定不一致。")
    trace = run.root / "agent_traces" / task.agent / invocation
    if not trace.resolve().is_relative_to(run.root.resolve()):
        raise ValueError("模型检查点路径无法核对。")
    checkpoint = trace / "checkpoint.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("correlation") != correlation:
        raise ValueError("模型调用与阶段检查点归属不一致。")
    if state.get("pending") == "tool" or state.get("pending_batch"):
        raise ValueError("存在结果未确认的工具操作，需先核对执行回执，避免重复修改或运行。")
    audit = audit_trace(trace)
    if audit.get("facts", {}).get("correlation") != correlation:
        raise ValueError("阶段检查点与执行记录不一致，需先核对原始记录。")
    if not audit.get("consistent"):
        events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
        metadata = recoverable_model_metadata_state(state, audit["facts"], events)
        if metadata is None:
            raise ValueError("阶段检查点与执行记录不一致，需先核对原始记录。")
        state = metadata
    return checkpoint, state


def recover_interrupted_run_model_requests(run: RunHandle) -> dict[str, Any]:
    """Retain full unknown charges; never edit checkpoints or replay any tool.

    The caller must first establish that no owned run task is active. Normal
    checkpoint/fingerprint/retry admission remains authoritative afterwards.
    """
    result: dict[str, Any] = {"schema": "run.model_recovery.v1", "status": "idle",
                             "recovered_request_ids": [], "usage_unknown_preserved": True}
    ledger = run.root / "resources/model_budget.v1.json"
    if not ledger.exists():
        return result
    try:
        if ledger.is_symlink():
            raise ValueError("模型调用记录不能是符号链接。")
        budget = RunModelBudget(run.root, active_model_time=True)
        rows = {key: row for key, row in budget.recovery_snapshot()["requests"].items()
                if row["status"] in {"in_flight", "reconciliation_required"}}
        if not rows:
            return result
        checkpoints = {key: _request_checkpoint(run, row) for key, row in rows.items()}
        abandoned = budget.recover_abandoned(request_ids=tuple(rows))
        latest = budget.recovery_snapshot()
        if any(latest["requests"][key]["status"] == "in_flight" for key in rows):
            raise ValueError("原模型请求仍在运行，MARS不会重复发送。")
        for key in abandoned:
            checkpoint, recovered_state = checkpoints[key]
            if not audit_trace(checkpoint.parent).get("consistent"):
                # A lost owner can leave its last SDK progress/retry event ahead
                # of facts. Restore only metadata proven by real durable events;
                # never create a response, tool result or an accepted candidate.
                LoopTrace(checkpoint.parent, "full", resume=True,
                          correlation=recovered_state["correlation"]).snapshot(recovered_state)
                result.setdefault("metadata_snapshots_recovered", []).append(key)
            budget.reconcile_abandoned(key, actor="mars-run-recovery",
                reason="Stage model owner exited; response and usage remain unknown; full reservation retained.",
                evidence_refs=(f"resources/model_budget.v1.json#requests/{key}",
                               checkpoint.relative_to(run.root).as_posix(),
                               f"lease:resources/requests/{key}.lock:owner_not_held"))
            result["recovered_request_ids"].append(key)
        if result["recovered_request_ids"]:
            result.update(status="recovered", message="已恢复中断的阶段模型记录，原用量及工具检查点保留。")
        return result
    except (OSError, ValueError, KeyError, ResourceBudgetError) as exc:
        return {**result, "status": "blocked", "error_code": "stage_model_recovery_blocked", "message": str(exc)}
