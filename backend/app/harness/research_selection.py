"""Immutable validation-only selection and exact worker input identities."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from app.harness.agent_loop.trace import atomic_json
from app.harness.research_trial import file_sha256, read_record


_SERVICE_FIELDS = frozenset({"candidate_id", "candidate_path", "worker_identity"})


def verify_trial_archive(root: Path, state: dict[str, Any], name: str,
                         trial: dict[str, Any]) -> None:
    """Match cached measurements to their receipt, retaining legacy raw archives."""
    output = trial.get("output")
    if output is None:
        # Gate/materialization failures precede a worker and contain no measurements.
        if (trial.get("status") != "failed" or trial.get("candidate_id") != name
                or not isinstance(trial.get("error"), str)
                or set(trial) - {"status", "candidate_id", "error"}):
            raise ValueError("Worker evidence has no archived output")
        return
    if not isinstance(output, str):
        raise ValueError("Worker output path is invalid")
    directory = Path(output)
    expected_parent = (root / "execution" / name).resolve()
    if (directory.is_symlink() or directory.resolve().parent != expected_parent
            or re.fullmatch(r"attempt_[0-9]{2}", directory.name) is None):
        raise ValueError("Worker output does not belong to the recorded trial")
    path = directory / "result.json"
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    if path.is_symlink() or state["artifacts"].get(relative) != file_sha256(path):
        raise ValueError("Archived worker result changed or lacks a receipt")
    archived = read_record(path)
    if trial.get("candidate_id") != name:
        raise ValueError("Cached worker name changed")
    # Existing result.json files omit the three fields added by the service.
    # Accept enriched archives too, but never discard a conflicting envelope.
    for key in _SERVICE_FIELDS.intersection(archived):
        if key not in trial or archived[key] != trial[key]:
            raise ValueError("Archived worker envelope differs from state")
    cached_payload = {k: v for k, v in trial.items() if k not in _SERVICE_FIELDS}
    archived_payload = {k: v for k, v in archived.items() if k not in _SERVICE_FIELDS}
    if json.dumps(cached_payload, sort_keys=True, allow_nan=False) != json.dumps(
            archived_payload, sort_keys=True, allow_nan=False):
        raise ValueError("Cached worker measurements differ from archived results")


def load_selection(root: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    relative = "experiment/selection.json"
    path = root / relative
    if not path.exists():
        if "selected" in state or any(name.startswith("final_") for name in state["trials"]):
            raise ValueError("Finalization requires a frozen selection; refusing to return to search")
        return None
    if state["artifacts"].get(relative) != file_sha256(path):
        raise ValueError("Frozen selection changed or was not durably recorded")
    selection = read_record(path)
    candidate = selection["candidate"]
    if state.get("selected") != (candidate["candidate_id"] if candidate else None):
        raise ValueError("Selected candidate identity changed")
    for trial in (selection["baseline"], candidate):
        if trial is None:
            continue
        if state["trials"].get(trial["candidate_id"]) != trial:
            raise ValueError("Selected training evidence changed")
        if file_sha256(Path(trial["output"]) / "best.pt") != trial["checkpoint_sha256"]:
            raise ValueError("Selected checkpoint changed")
        if trial.get("candidate_path") and file_sha256(Path(trial["candidate_path"])) != trial["candidate_sha256"]:
            raise ValueError("Selected source changed")
    return selection


def freeze_selection(root: Path, state: dict[str, Any], baseline: dict[str, Any],
                     candidate: dict[str, Any] | None) -> None:
    relative = "experiment/selection.json"
    if (root / relative).exists() or "selected" in state:
        raise ValueError("Selection is immutable once frozen")
    selection = {"schema": "research.selection.v1", "basis": "validation only",
                 "baseline": baseline, "candidate": candidate}
    atomic_json(root / relative, selection)
    state["selected"] = candidate["candidate_id"] if candidate else None
    state["artifacts"][relative] = file_sha256(root / relative)
    state["status"] = "finalizing"
    atomic_json(root / "state.json", state)


def worker_identity(manifest: dict[str, Any], candidate: Path | None,
                    checkpoint: dict[str, Any] | None, operation: str) -> dict[str, Any]:
    candidate_hash = file_sha256(candidate) if candidate else None
    if checkpoint:
        if file_sha256(Path(checkpoint["output"]) / "best.pt") != checkpoint["checkpoint_sha256"]:
            raise ValueError("Selected checkpoint changed")
        if candidate_hash != checkpoint.get("candidate_sha256"):
            raise ValueError("Selected candidate code changed")
    return {"operation": "finalize" if checkpoint else operation,
            "source_snapshot": manifest["source_snapshot"], "protocol_sha256": manifest["protocol_sha256"],
            "candidate_sha256": candidate_hash,
            "checkpoint_sha256": checkpoint["checkpoint_sha256"] if checkpoint else None}
