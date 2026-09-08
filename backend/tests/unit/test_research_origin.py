"""Actual file hashing of authored reconciliation metadata; no runtime claims."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.idea.research_origin import research_origins
from app.agents.idea.research_delegate import resumed_delegation_count


def put(root: Path, relative: str, value: Any) -> str:
    path = root/relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lineage(tmp_path: Path) -> tuple[Path, Path, dict[str, Any], list[dict[str, Any]]]:
    # These files describe a reconciliation contract, not a synthetic execution.
    source, target = tmp_path/"source", tmp_path/"target"
    identifier, invocation = "a"*32, "b"*32
    args = {"gap": "authored negative reconciliation contract"}
    history = [{"tool": "idea.research_delegate", "args": args, "ok": False,
                "output": {"delegation_id": identifier}}]
    relative = f"idea/research_delegations/{identifier}/request.json"
    request = {"delegation_id": identifier, "parent_run_id": "source", "arguments": args,
               "parent_invocation": str(source/"agent_traces"/"idea"/invocation)}
    hashes = {relative: put(source, relative, request),
              "input/request.json": put(source, "input/request.json", {"run_id": "source"}),
              f"agent_traces/idea/{invocation}/checkpoint.json": put(source, f"agent_traces/idea/{invocation}/checkpoint.json",
                                                                     {"status": "model_error", "history": history})}
    put(target, relative, request)
    manifest = {"schema": "idea.research_continuation.v1", "source_run_root": str(source),
                "source_run_id": "source", "continuation_run_id": "target", "invocation": invocation,
                "budgets_reset": False, "source_files_sha256": hashes}
    put(target, "input/continuation.json", manifest)
    return source, target, manifest, history


def test_verified_old_parent_identity_is_usable_without_rewriting_request(tmp_path: Path) -> None:
    source, target, manifest, history = lineage(tmp_path)
    parent = str(target/"agent_traces"/"idea"/manifest["invocation"])
    assert resumed_delegation_count(target, history, run_id="target", parent_invocation=parent) == 1
    request = f"idea/research_delegations/{'a'*32}/request.json"
    assert (source/request).read_bytes() == (target/request).read_bytes()


@pytest.mark.parametrize("damage", ["history", "source_checkpoint", "target_request", "source_request", "budget", "identity", "cycle"])
def test_lineage_damage_fails_closed(tmp_path: Path, damage: str) -> None:
    source, target, manifest, history = lineage(tmp_path)
    invocation = manifest["invocation"]
    if damage == "history":
        history = []
    elif damage == "source_checkpoint":
        put(source, f"agent_traces/idea/{invocation}/checkpoint.json", {"status": "model_error", "history": []})
    elif damage in {"target_request", "source_request"}:
        root = target if damage == "target_request" else source
        path = root/f"idea/research_delegations/{'a'*32}/request.json"
        path.write_text(path.read_text()+" ")
    elif damage == "budget":
        manifest["budgets_reset"] = True
    elif damage == "identity":
        manifest["source_run_id"] = "unverified"
    else:
        manifest["source_run_root"] = str(target)
    put(target, "input/continuation.json", manifest)
    with pytest.raises(ValueError):
        resumed_delegation_count(target, history, run_id="target",
                                 parent_invocation=str(target/"agent_traces"/"idea"/invocation))


def test_no_lineage_does_not_infer_an_old_identity(tmp_path: Path) -> None:
    assert research_origins(tmp_path, run_id="current", parent_invocation="parent", history=[]) == []
