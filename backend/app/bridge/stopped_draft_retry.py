"""Admission for an explicit new draft after confirmed cancellation cleanup."""
from __future__ import annotations

import json
from typing import Any, Mapping

from app.bridge.task_runtime import task_contract_path
from app.harness.agent_loop.trace import audit_trace
from app.harness.llm.accounting import ResourceBudgetError, RunModelBudget
from app.harness.runtime.task_contract import TaskEnvelope
from app.storage.run_store import RunHandle

DRAFT_STAGES = frozenset({"idea", "experiment", "writing"})


def stopped_draft_retry_blocker(run: RunHandle, node: str, *, stage: str,
                                termination: Mapping[str, Any], states: Mapping[str, str]) -> str | None:
    if (stage not in DRAFT_STAGES or termination.get("type") != "cancelled"
            or termination.get("scope") != "owned_async_tasks"
            or termination.get("cleanup_complete") is not True
            or termination.get("interrupted_nodes") != [node]
            or states.get(node) != "failed" or "running" in states.values()):
        return "停止清理或节点状态尚未确认，不能开始新的方案尝试。"
    path = task_contract_path(run, node)
    if path.exists():
        task = TaskEnvelope.model_validate_json(path.read_text())
        if (task.run_id != run.run_id or task.project != run.project
                or task.node_id != node or task.agent != stage):
            return "已停止任务的所属信息不匹配。"
        trace = run.root / "agent_traces" / stage / task.invocation_id
        if not trace.resolve().is_relative_to(run.root.resolve()):
            return "检查点路径异常。"
        checkpoint = trace / "checkpoint.json"
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text())
            if state.get("pending") == "tool" or state.get("pending_batch"):
                return "存在结果未确认的工具操作，需先核对原始回执。"
            if not audit_trace(trace).get("consistent"):
                return "检查点与执行记录不一致，需先核对。"
    if (run.root / "resources/model_budget.v1.json").exists():
        try:
            ledger = RunModelBudget(run.root).recovery_snapshot()
        except ResourceBudgetError:
            return "资源记录或预算配置无法校验，请先核对记录。"
        if any(row.get("status") in {"in_flight", "reconciliation_required"}
               for row in ledger.get("requests", {}).values()):
            return "存在结果未确认的模型请求，需先核对原始回执。"
    return None
