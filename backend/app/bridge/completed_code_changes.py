"""Net coding changes from successful writes, checked against the bound code files."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

import yaml

from app.bridge.code_changes import _read, parse_diff
from app.bridge.code_repository import CodeRepository, run_code_repository
from app.settings import repo_root
from app.storage.run_store import RunHandle


@dataclass
class FileChange:
    before: str | None
    after: str | None
    timestamp: str
    record_id: str
    valid: bool = True


def _snapshots(run: RunHandle, record: dict[str, Any], limit: int) -> dict[str, str | None]:
    ref = record.get("rollback_ref")
    if not isinstance(ref, str) or not ref:
        raise ValueError("missing write snapshot")
    target = Path(ref)
    if not target.is_absolute():
        target = run.root / target
    data = json.loads(_read(target, run.root, limit))
    if (data.get("schema") != "tool_rollback.v1" or data.get("run_id") != run.run_id
            or data.get("project") != run.project or data.get("tool") != record["tool"]):
        raise ValueError("snapshot identity mismatch")
    snapshots: dict[str, str | None] = {}
    for snapshot in data["snapshots"]:
        path, content, existed = snapshot["path"], snapshot["content"], snapshot["existed"]
        if not isinstance(path, str) or not isinstance(content, str) or type(existed) is not bool or path in snapshots:
            raise ValueError("invalid snapshot")
        expected = "sha256:" + hashlib.sha256(content.encode()).hexdigest() if existed else ""
        if snapshot["sha256"] != expected:
            raise ValueError("snapshot hash mismatch")
        snapshots[path] = content if existed else None
    return snapshots


def _patched(before: str | None, item: dict[str, Any]) -> str | None:
    """Replay archived text hunks without executing a command or changing files."""
    if item.get("warning") or item["change"] == "renamed":
        raise ValueError("patch cannot be reconstructed")
    original = (before or "").splitlines(keepends=True)
    result: list[str] = []
    cursor = 0
    previous_kind = ""
    newline = "\r\n" if any(line.endswith("\r\n") for line in original) else "\n"
    for row in item["lines"]:
        kind, text = row["kind"], row["text"]
        if kind == "hunk":
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", text)
            if match is None:
                raise ValueError("invalid hunk")
            count = int(match[2]) if match[2] is not None else 1
            start = max(0, int(match[1]) - (1 if count else 0))
            if not cursor <= start <= len(original):
                raise ValueError("invalid hunk offset")
            result.extend(original[cursor:start])
            cursor = start
        elif kind in {"context", "delete"}:
            if cursor >= len(original) or original[cursor].rstrip("\r\n") != text:
                raise ValueError("patch before-state mismatch")
            if kind == "context":
                result.append(original[cursor])
            cursor += 1
        elif kind == "add":
            result.append(text + newline)
        elif text.startswith("\\ No newline") and previous_kind in {"add", "context"}:
            result[-1] = result[-1].rstrip("\r\n")
        previous_kind = kind
    result.extend(original[cursor:])
    after = "".join(result)
    if item["change"] == "deleted":
        if after:
            raise ValueError("incomplete deletion")
        return None
    return after


def _operations(run: RunHandle, record: dict[str, Any], limit: int) -> list[tuple[str, str | None, str | None]]:
    snapshots = _snapshots(run, record, limit)
    args, tool = record["args"], record["tool"]
    if tool in {"code.write_file", "code.delete_file"}:
        path = PurePosixPath(args["path"].strip()).as_posix()
        before = snapshots[path]
        after = None if tool == "code.delete_file" else args["content"]
        if after is not None and not isinstance(after, str):
            raise ValueError("invalid written content")
        return [(path, before, after)]
    files = parse_diff(args["diff"])
    if not files:
        raise ValueError("missing applied patch")
    return [(item["path"], snapshots[item["path"]], _patched(snapshots[item["path"]], item)) for item in files]


def _net_diff(path: str, before: str | None, after: str | None, context: int) -> dict[str, Any]:
    parts = difflib.unified_diff((before or "").splitlines(keepends=True), (after or "").splitlines(keepends=True),
        fromfile="/dev/null" if before is None else "a/" + path,
        tofile="/dev/null" if after is None else "b/" + path, n=context)
    text = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in parts)
    files = parse_diff(text)
    if files:
        # The validated receipt path is authoritative, including whitespace.
        return {**files[0], "path": path}
    return {"path": path, "change": "added" if before is None else "deleted", "additions": 0,
            "deletions": 0, "lines": [{"kind": "meta", "text": "新增空文件" if before is None else "删除空文件",
                                      "old_line": None, "new_line": None}]}


def completed_code_changes(run: RunHandle, *, project: str, change_id: str | None = None,
                           repository: CodeRepository | None = None) -> dict[str, Any]:
    if run.project != project:
        raise ValueError("任务所属项目不匹配")
    policy = yaml.safe_load((repo_root() / "configs/code_review.yaml").read_text())["code_review"]
    for key in ("max_record_bytes", "max_records", "max_scan_records", "max_preview_lines", "context_lines"):
        if type(policy.get(key)) is not int or policy[key] <= 0:
            raise ValueError("invalid code review policy")
    paths = sorted(p for p in (run.root / "coding/tool_applications").glob("*.json") if not p.name.startswith("rollback_"))
    if len(paths) > policy["max_scan_records"]:
        raise ValueError("改动记录超出核对范围，不能展示不完整的改动统计")
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    for record_path in paths:
        try:
            record = json.loads(_read(record_path, run.root, policy["max_record_bytes"]))
            if (record.get("run_id") == run.run_id and record.get("project") == project
                    and record.get("agent") == "coding" and record.get("status") == "success"
                    and record.get("tool") in {"code.write_file", "code.delete_file", "code.apply_patch"}):
                records.append(record)
        except (OSError, ValueError, AttributeError):
            warnings.append(f"{record_path.name} 无法安全核对。")
    # Reads and unsuccessful attempts never consume the budget for real writes.
    if len(records) > policy["max_records"]:
        raise ValueError("写入记录超出核对范围，不能展示不完整的改动统计")
    records.sort(key=lambda record: (str(record.get("ended_at") or record.get("started_at") or ""), str(record["call_id"])))
    changes: dict[str, FileChange] = {}
    unverified_paths: set[str] = set()
    for record in records:
        try:
            operations = _operations(run, record, policy["max_record_bytes"])
            for path, before, after in operations:
                previous = changes.get(path)
                if previous is not None:
                    previous.valid = previous.valid and previous.after == before
                    previous.after = after
                    previous.timestamp = str(record.get("ended_at") or record.get("started_at") or "")
                    previous.record_id = str(record["call_id"])
                else:
                    changes[path] = FileChange(before, after, str(record.get("ended_at") or record.get("started_at") or ""), str(record["call_id"]), path not in unverified_paths)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            args = record.get("args", {})
            affected = ([PurePosixPath(args["path"].strip()).as_posix()] if isinstance(args.get("path"), str)
                        else [item["path"] for item in parse_diff(str(args.get("diff", "")))])
            if not affected:
                raise ValueError("存在无法核对的成功写入记录，不能展示不完整的改动统计")
            unverified_paths.update(affected)
            for path in affected:
                if path in changes:
                    changes[path].valid = False
            warnings.append(f"{record['call_id']} 缺少可验证的写入快照，未计入已完成改动。")
    browser = repository or (run_code_repository(run, project=project) if changes else None)
    if browser is not None and browser.repo.project != project:
        raise ValueError("代码工程所属项目不匹配")
    items: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for path, change in sorted(changes.items()):
        try:
            if not change.valid:
                raise ValueError("write chain mismatch")
            if browser is None:
                raise ValueError("missing bound repository")
            try:
                current = browser.file(path)
            except FileNotFoundError:
                if change.after is not None:
                    raise
            else:
                if change.after is None or current["version"] != hashlib.sha256(change.after.encode()).hexdigest():
                    raise ValueError("current file differs from the completed write")
            if change.before == change.after:
                unchanged.append({"path": path, "sha256": hashlib.sha256(change.after.encode()).hexdigest()
                                  if change.after is not None else None,
                                  "exists": change.after is not None})
                continue
            item = _net_diff(path, change.before, change.after, policy["context_lines"])
            lines = item.pop("lines")
            fingerprint = hashlib.sha256(json.dumps([path, change.before, change.after, change.record_id]).encode()).hexdigest()
            item.update(id="completed:" + fingerprint, status="applied", source="verified_tool_writes",
                        timestamp=change.timestamp, truncated=len(lines) > policy["max_preview_lines"])
            if change_id == item["id"]:
                return {**item, "run_id": run.run_id, "project": project, "lines": lines[:policy["max_preview_lines"]]}
            items.append(item)
        except (OSError, ValueError):
            warnings.append(f"{path} 与写入记录不一致或不可读取，未作为已完成改动展示。")
    if change_id is not None:
        raise KeyError("代码改动已更新或无法核对，请刷新后重试")
    return {"run_id": run.run_id, "project": project, "items": items, "warnings": warnings,
            "write_records": len(records), "unchanged": unchanged}
