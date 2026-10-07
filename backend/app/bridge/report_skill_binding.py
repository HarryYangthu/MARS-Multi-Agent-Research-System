"""Freeze report instructions at admission; resumes never reload preferences."""
from __future__ import annotations

import json
from contextlib import nullcontext
from app.harness.agent_loop.trace import digest
from app.harness.persistence import atomic_write_json, path_lock
from app.harness.runtime.project_scope import safe_scope_path
from app.harness.skills.registry import load_selected_skills
from app.storage.report_skill_store import selected_report_skills
from app.storage.run_store import RunHandle
from app.bridge.report_service import writing_tools


def snapshot_ref(node: str) -> str:
    return f"input/report_skills/{digest(node)}.json"


def frozen_report_skills(run: RunHandle, node: str, ids: list[str] | None = None, *, create: bool = False) -> list[str]:
    path = safe_scope_path(run.root, snapshot_ref(node))
    with path_lock(path.with_suffix(".lock")) if create else nullcontext():
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("run_id") != run.run_id or record.get("project") != run.project or record.get("node") != node:
                raise ValueError("报告 skill 快照身份无效")
            selection = load_selected_skills(record["ids"], granted_tools=writing_tools(), project=run.project)
            if selection.manifest != record["manifest"]:
                raise ValueError("报告 skill 内容或权限已改变，不能继续此写作调用")
            return list(record["ids"])
        if not create:
            return []  # Existing runs predating skill admission keep their original context.
        selection = load_selected_skills(ids if ids is not None else selected_report_skills(run.project),
                                         granted_tools=writing_tools(), project=run.project)
        pinned = [f"{entry['id']}@{entry['version']}" for entry in selection.manifest["skills"]]
        record = {"run_id": run.run_id, "project": run.project, "node": node, "ids": pinned, "manifest": selection.manifest}
        atomic_write_json(path, record)
        return pinned
