"""Immutable validation-only selection and exact worker input identities."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import atomic_json
from app.harness.research_trial import file_sha256, read_record


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
