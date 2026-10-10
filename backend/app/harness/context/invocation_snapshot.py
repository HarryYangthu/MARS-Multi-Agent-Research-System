"""Immutable focused Idea configuration, scoped to each fresh invocation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import atomic_json
from app.harness.persistence import path_lock


def invocation_snapshot_path(run_root: Path, invocation: str) -> Path:
    if not invocation.replace("-", "").isalnum():
        raise ValueError("invalid focused Idea invocation ID")
    return run_root / "input/idea_focused" / (invocation + ".json")


def load_focused_snapshot(run_root: Path, invocation: str) -> dict[str, Any]:
    """Old invocations retain the original run receipt; new ones pin their own."""
    path = invocation_snapshot_path(run_root, invocation)
    if not path.exists():
        path = run_root / "input/idea_focused.v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid focused Idea runtime profile")
    return value


def bind_focused_snapshot(run_root: Path, snapshot: dict[str, Any], *,
                          invocation: str | None = None, resume: str | None = None,
                          revision_reason: str = "") -> None:
    """A retry may adopt current settings only in a new, trace-free invocation.

    The original run receipt and all prior invocation receipts stay immutable.
    Checkpoint resumes must still match their own receipt and loop fingerprint.
    """
    if resume and invocation and resume != invocation:
        raise ValueError("focused Idea resume invocation differs from task")
    identity = resume or invocation
    current = invocation_snapshot_path(run_root, identity) if identity else None
    original = run_root / "input/idea_focused.v1.json"
    with path_lock(original.with_suffix(".lock")):
        if current is not None and current.exists():
            if load_focused_snapshot(run_root, str(identity)) != snapshot:
                raise ValueError("focused Idea configuration changed; retry as a new invocation")
            return
        if original.exists():
            previous = json.loads(original.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                raise ValueError("invalid focused Idea runtime profile")
            # An explicit retry creates a fresh invocation at the bridge. Never
            # rebind a checkpoint, even if its per-invocation receipt is absent.
            fresh_retry = (bool(revision_reason.strip()) and current is not None and not resume
                           and not (run_root / "agent_traces/idea" / str(identity)).exists())
            if previous != snapshot and not fresh_retry:
                raise ValueError("focused Idea configuration changed; retry as a new invocation")
        else:
            if resume or any((run_root / "agent_traces").glob("*/*/checkpoint.json")):
                raise ValueError("cannot change a historical run to focused Idea")
            atomic_json(original, snapshot)
        if current is not None:
            atomic_json(current, snapshot)
