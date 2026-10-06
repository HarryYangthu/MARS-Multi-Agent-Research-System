"""Verify code already written by governed tools before approving its handoff."""
from __future__ import annotations

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
    if not actual:
        return not declared
    if actual != declared:
        raise ValueError("编码方案的文件清单与实际写入改动不一致，请更新方案后重新审核；未批准。")
    return True
