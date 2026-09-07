"""File-level resume guards and immutable source journals for real evaluations."""
from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.harness.agent_loop.trace import atomic_json, audit_trace


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def exclusive_run(root: Path) -> Iterator[None]:
    if not root.is_dir():
        raise ValueError("resume run directory does not exist")
    with (root / "evaluation.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another evaluation process holds this run") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load_resume(root: Path) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    initial = json.loads((root / "input/request.json").read_text())
    previous = json.loads((root / "summary.json").read_text())
    checkpoints = list((root / "agent_traces/idea").glob("*/checkpoint.json"))
    if len(checkpoints) != 1:
        raise ValueError("resume requires exactly one Idea invocation checkpoint")
    checkpoint = checkpoints[0]
    state = json.loads(checkpoint.read_text())
    if state["status"] not in {"model_error", "interrupted"}:
        raise ValueError("only interrupted or model-error runs can resume; budgets are never reset")
    if state["pending"] == "tool":
        raise ValueError("unknown tool outcome: reconcile the existing receipt before resuming")
    if initial["loop_policy"]["trace"] != "full" or not audit_trace(checkpoint.parent)["consistent"]:
        raise ValueError("resume requires a complete, consistent full trace")
    if initial["run_id"] != root.name or previous["run_id"] != root.name:
        raise ValueError("run identity does not match its directory")
    if state["counts"]["model_requests"] >= initial["loop_policy"]["max_model_calls"]:
        raise ValueError("model request budget already exhausted")
    return initial, previous, checkpoint, state


def record_resumption(root: Path, checkpoint: Path, source: dict[str, Any]) -> Path:
    """Freeze the actual pre-resume files before any checkpoint/summary update."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:6]
    journal = root / "input/resumptions" / stamp
    journal.mkdir(parents=True, exist_ok=False)
    files = {"checkpoint.json": checkpoint, "facts.json": checkpoint.parent / "facts.json",
             "summary.json": root / "summary.json"}
    hashes = {}
    for name, path in files.items():
        shutil.copyfile(path, journal / name)
        hashes[name] = sha256_file(journal / name)
    events = checkpoint.parent / "events.jsonl"
    manifest = {**source, "invocation": checkpoint.parent.name,
                "input_sha256": sha256_file(root / "input/request.json"),
                "prior_event_bytes": events.stat().st_size, "prior_events_sha256": sha256_file(events),
                "prior_files_sha256": hashes, "budgets_reset": False}
    atomic_json(journal / "manifest.json", manifest)
    return journal


def audit_resumptions(root: Path, trace_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    events = (trace_root / "events.jsonl").read_bytes()
    for path in sorted((root / "input/resumptions").glob("*/manifest.json")):
        row = json.loads(path.read_text())
        rows.append(row)
        if row["invocation"] != trace_root.name or row.get("budgets_reset") is not False:
            errors.append("resumption changed invocation or reset budgets")
        if row.get("source_dirty"):
            errors.append("resumption started with a dirty source tree")
        if sha256_file(root / "input/request.json") != row["input_sha256"]:
            errors.append("original evaluation input changed after resumption")
        size = row["prior_event_bytes"]
        if size > len(events) or hashlib.sha256(events[:size]).hexdigest() != row["prior_events_sha256"]:
            errors.append("pre-resumption events were changed")
        for name, expected in row["prior_files_sha256"].items():
            if name not in {"checkpoint.json", "facts.json", "summary.json"}:
                errors.append("invalid journal file name")
            elif not (path.parent / name).is_file() or sha256_file(path.parent / name) != expected:
                errors.append("pre-resumption snapshot differs from journal hash")
    return rows, errors
