"""Verify code already written by governed tools before approving its handoff."""
from __future__ import annotations

import json
from typing import Any

from app.bridge.completed_code_changes import completed_code_changes
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunHandle


def verify_written_code(run: RunHandle, text: str) -> bool:
    """True means the declared net changes are already present, not a patch replay.

    An empty ledger permits the legacy patch-only path. A partial, stale or
    unverifiable implementation must fail instead of applying an older patch.
    The caller binds the run's repository capability before invoking this.
    """
    validation = validate_document(text, expected_schema="code_spec.v1")
    if not validation.valid or validation.metadata.get("project") != run.project:
        raise ValueError("编码方案校验未通过或不属于当前项目，未批准。")
    rows: list[dict[str, Any]] = validation.metadata["files_changed"]
    declared = {row["path"]: row["type"] for row in rows}
    if len(declared) != len(rows):
        raise ValueError("编码方案存在重复的文件声明，未批准。")
    view = completed_code_changes(run, project=run.project)
    if view["warnings"]:
        raise ValueError("实际代码与成功写入记录不一致，请核对代码改动后重新审核；未批准。")
    actual = {item["path"]: item["change"] for item in view["items"]}
    if not actual and not view["write_records"]:
        return not declared
    if actual != declared:
        evidence = json.dumps({"files_changed": [{"path": path, "type": kind} for path, kind in actual.items()],
                               "unchanged": view["unchanged"]}, ensure_ascii=False)
        raise ValueError("编码方案的文件清单与实际写入改动不一致；相同内容的重写属于复用，不是新增。"
                         "请按已核验记录修正方案，不要为制造差异改写文件或重复应用补丁；未批准。" + evidence)
    return True


def coding_candidate_errors(run: RunHandle, text: str) -> list[str]:
    """Host evidence feeds the existing model correction loop before human review."""
    try:
        if not verify_written_code(run, text):
            return ["/files_changed: 没有真实写入记录；先完成受治理的代码写入，不能只提交补丁说明。"]
    except (OSError, ValueError) as exc:
        return ["/files_changed: " + str(exc)]
    from app.execution.handoff_validation import coding_handoff_errors, execution_delivery_required, experiment_plan_required
    validation = validate_document(text, expected_schema='code_spec.v1')
    if validation.metadata.get('execution_jobs') or execution_delivery_required(run, 'coding'):
        plan = run.root / 'experiment/experiment_plan.approved.md'
        return coding_handoff_errors(plan.read_text() if plan.is_file() else '', validation.metadata,
            project=run.project, plan_required=experiment_plan_required(run, 'coding'))
    return []
