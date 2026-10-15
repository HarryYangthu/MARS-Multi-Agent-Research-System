"""Read source and real check observations for the candidate being reviewed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.bridge.code_repository import run_code_repository
from app.bridge.task_runtime import task_contract_path
from app.harness.agent_loop.trace import digest
from app.harness.runtime.task_contract import TaskEnvelope
from app.harness.schema.frontmatter_parser import parse
from app.storage.run_store import RunHandle

_CHECKS = frozenset({"code.test_runner", "code.lint"})
_WRITES = frozenset({"code.write_file", "code.apply_patch", "code.delete_file"})


def _trace_file(run: RunHandle, path: Path) -> Path:
    if path.is_symlink() or not path.resolve().is_relative_to(run.root.resolve()):
        raise ValueError("编码审核记录越出当前任务")
    return path


def coding_check_evidence(run: RunHandle, node: str, candidate: str) -> dict[str, Any]:
    """Never mix earlier attempts or summary-only logs into current test results."""
    task_path = _trace_file(run, task_contract_path(run, node))
    if not task_path.is_file():
        return {"status": "unavailable", "reason": "当前方案没有编码调用记录", "checks": []}
    task = TaskEnvelope.model_validate_json(task_path.read_text(encoding="utf-8"))
    if (task.run_id, task.project, task.node_id, task.agent) != (run.run_id, run.project, node, "coding"):
        raise ValueError("编码审核调用身份不匹配")
    root = run.root / "agent_traces/coding" / task.invocation_id
    checkpoint = _trace_file(run, root / "checkpoint.json")
    events = _trace_file(run, root / "events.jsonl")
    if not checkpoint.is_file() or not events.is_file():
        return {"status": "unavailable", "reason": "当前调用未保存完整检查记录", "checks": []}
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    if state.get("status") != "passed" or state.get("candidate") != candidate:
        return {"status": "unavailable", "reason": "调用产物与当前审核方案不一致，未复用旧测试", "checks": []}
    checks: list[dict[str, Any]] = []
    mutations: list[int] = []
    fingerprint = hashlib.sha256()
    with events.open("rb") as stream:
        for expected, raw in enumerate(stream, 1):
            fingerprint.update(raw)
            row = json.loads(raw)
            correlation = row.get("correlation", {})
            if (row.get("event_seq") != expected or correlation.get("invocation_id") != task.invocation_id
                    or correlation.get("trace_id") != run.run_id or correlation.get("node_id") != node):
                raise ValueError("编码检查记录序列或调用身份不匹配")
            if row.get("kind") != "observation":
                continue
            tool = row.get("tool")
            if tool in _WRITES:
                mutations.append(expected)  # Include uncertain/failed writes conservatively.
            if tool not in _CHECKS:
                continue
            visible = row.get("visible")
            if not isinstance(visible, dict) or row.get("visible_sha256") != digest(visible):
                raise ValueError("编码检查原始结果缺失或摘要不匹配")
            if visible.get("tool") != tool:
                raise ValueError("编码检查工具身份不匹配")
            checks.append({"ref": events.relative_to(run.root).as_posix(), "event_seq": expected,
                           "time": row.get("time"), "observation": visible})
    for check in checks:
        check["later_code_mutation_events"] = [seq for seq in mutations if seq > check["event_seq"]]
    return {"status": "available", "invocation_id": task.invocation_id,
            "candidate_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
            "trace_sha256": fingerprint.hexdigest(), "checks": checks,
            "scope": "Original tool results from this candidate's invocation; any tool-level output truncation is retained. "
                     "Checks preceding later writes do not verify those writes. Test success is not experiment success."}


def coding_review_evidence(run: RunHandle, node: str, candidate: str) -> dict[str, Any]:
    """Complete current changed files, with stable versions and normal path policy."""
    checks = coding_check_evidence(run, node, candidate)
    repository = run_code_repository(run, project=run.project)
    metadata = parse(candidate).metadata
    files = []
    for change in metadata.get("files_changed", []):
        name = change["path"]
        if change["type"] == "deleted":
            files.append({"path": name, "type": "deleted", "content": None})
            continue
        page = repository.file(name)
        first = page
        lines = list(page["lines"])
        while page["next_start"] is not None:
            page = repository.file(name, start=page["next_start"], version=first["version"], token=first["repository_token"])
            lines.extend(page["lines"])
        files.append({"path": name, "type": change["type"], "sha256": first["version"],
                      "size_bytes": first["size_bytes"], "content": "".join(lines)})
    return {"actual_code_checks": checks.pop("checks"), "code_check_context": checks,
            "code_source_evidence": {"repository_token": repository.token,
                                     "coding_backend": metadata.get("coding_backend"),
                                     "files": files, "truncated": False}}
