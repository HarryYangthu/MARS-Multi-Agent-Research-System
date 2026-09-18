"""Import an already accepted real research stage with immutable provenance.

Reused traces live outside the new run's stages directory, so their model/tool
calls cannot be mistaken for newly executed work. Runtime changes are permitted;
the scientific inputs and accepted proposal must remain identical.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any

from filelock import FileLock

from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.discovery.snapshots import verify_snapshot
from app.harness.research_trial import file_sha256, read_record
from app.harness.schema.validator import validate_document


RECEIPT_PATH = "context/research_reuse.json"
STAGE_PATH = "reused_research/stage"
PROPOSAL_PATH = "idea/proposal.md"


def _tree_files(root: Path) -> dict[str, str]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Research trace must be a regular directory")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Research trace cannot contain symlinks or escaping paths")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_sha256(path)
        elif not path.is_dir():
            raise ValueError("Research trace contains a nonregular file")
    if not files:
        raise ValueError("Research trace is empty")
    return files


def _scientific_inputs(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    protocol_path, context_path = root / "experiment/protocol.json", root / "context/source.json"
    if file_sha256(protocol_path) != manifest["protocol_sha256"] or file_sha256(context_path) != manifest["context_sha256"]:
        raise ValueError("Research reuse protocol or context changed")
    protocol = read_record(protocol_path)
    data = Path(manifest["data"]).resolve()
    if Path(protocol["data_path"]).resolve() != data or file_sha256(data) != protocol["data_sha256"]:
        raise ValueError("Research reuse dataset changed")
    snapshot = verify_snapshot(Path(manifest["source_snapshot"]))
    return {"task": manifest["task"], "project": manifest["project"], "data": str(data),
            "data_sha256": protocol["data_sha256"], "protocol_sha256": manifest["protocol_sha256"],
            "context_sha256": manifest["context_sha256"], "budget": manifest["budget"],
            "folder_context_files": manifest.get("folder_context_files", {}),
            "source_snapshot_files": [item.model_dump(mode="json") for item in snapshot.manifest.files]}


def _no_final_test(root: Path, state: dict[str, Any]) -> None:
    if (state.get("final_comparison") is not None
            or any(str(name).startswith("execution/final_") for name in state.get("artifacts", {}))
            or any((root / "execution").glob("final_*"))):
        raise ValueError("Research reuse source contains final-test execution")
    records = list(state.get("trials", {}).items())
    for name, history in state.get("trial_history", {}).items():
        records.extend((name, trial) for trial in history)
    if any(name.startswith("final_") or trial.get("operation") == "finalize" or "test" in trial
           for name, trial in records):
        raise ValueError("Research reuse source contains final-test evidence")


def _passed_checkpoint(stage: Path, proposal: str) -> Path:
    paths = list(stage.glob("agent_traces/idea/*/checkpoint.json"))
    if not paths:
        raise ValueError("Research reuse has no actual idea checkpoint")
    path = max(paths, key=lambda p: (p.stat().st_mtime_ns, p.as_posix()))
    checkpoint = read_record(path)
    facts = read_record(path.parent / "facts.json")
    if checkpoint.get("status") != "passed" or facts.get("status") != "passed":
        raise ValueError("Research reuse requires the final idea checkpoint to be passed")
    if checkpoint.get("candidate") != proposal:
        raise ValueError("Research proposal differs from the passed checkpoint candidate")
    events = [json.loads(line) for line in (path.parent / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    reflections = [event for event in events if event.get("kind") == "reflection"]
    finished = [event for event in events if event.get("kind") == "finished"]
    review = reflections[-1].get("visible") if reflections else None
    if (checkpoint.get("reflection_accepted") is not True
            or checkpoint.get("reviewed_candidate_sha") != digest(proposal)
            or not isinstance(review, dict) or review.get("accept") is not True
            or reflections[-1].get("accept") is not True
            or reflections[-1].get("visible_sha256") != digest(review)
            or not finished or finished[-1].get("status") != "passed"):
        raise ValueError("Research reuse requires an accepted independent review and final passed event")
    for counter, kind in (("model_responses", "model_response"), ("tool_dispatches", "tool_dispatch")):
        count = sum(event.get("kind") == kind for event in events)
        if count < 1 or count != checkpoint.get("counts", {}).get(counter) or count != facts.get("counts", {}).get(counter):
            raise ValueError("Research reuse lacks consistent real model/tool trace evidence")
    for event in events:
        if event.get("kind") == "model_response" and (not event.get("provider")
                or str(event["provider"]).lower() in {"mock", "fake", "stub"}):
            raise ValueError("Research reuse requires real provider responses")
    return path


def _same_inputs(left: dict[str, Any], right: dict[str, Any]) -> None:
    changed = [key for key in sorted(set(left) | set(right))
               if json.dumps(left.get(key), sort_keys=True) != json.dumps(right.get(key), sort_keys=True)]
    if changed:
        raise ValueError("Research reuse inputs differ: " + ", ".join(changed))


def reuse_research(root: Path, manifest: dict[str, Any], state: dict[str, Any], source: Path) -> None:
    root, source = root.resolve(), source.resolve()
    if root == source or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("Research reuse needs separate, non-nested run directories")
    if not source.is_dir():
        raise ValueError("Research reuse source run does not exist")
    with FileLock(str(source / "run.lock"), timeout=0):
        source_manifest = read_record(source / "input/manifest.json")
        source_state = read_record(source / "state.json")
        manifest_hash = file_sha256(source / "input/manifest.json")
        if source_state.get("manifest_sha256") != manifest_hash:
            raise ValueError("Research reuse source manifest changed")
        _no_final_test(source, source_state)
        proposal_path = source / PROPOSAL_PATH
        proposal_hash = file_sha256(proposal_path)
        if source_state.get("artifacts", {}).get(PROPOSAL_PATH) != proposal_hash:
            raise ValueError("Research reuse proposal lacks a matching accepted artifact hash")
        proposal = proposal_path.read_text(encoding="utf-8")
        validation = validate_document(proposal, expected_schema="proposal.v1")
        if not validation.valid or validation.metadata.get("project") != source_manifest["project"]:
            raise ValueError("Research reuse proposal schema or project is invalid")
        source_stage = source / "stages/idea/proposal"
        stage_files = _tree_files(source_stage)
        checkpoint = _passed_checkpoint(source_stage, proposal)
        binding = _scientific_inputs(source, source_manifest)
        _same_inputs(binding, _scientific_inputs(root, manifest))
        destination = root / STAGE_PATH
        shutil.copytree(source_stage, destination)
        shutil.copy2(proposal_path, root / PROPOSAL_PATH)
        copies = {"source_manifest": (source / "input/manifest.json", "reused_research/source_manifest.json"),
                  "source_state": (source / "state.json", "reused_research/source_state.json")}
        receipt: dict[str, Any] = {
            "schema": "research.reuse.v1", "reused_at": datetime.now(timezone.utc).isoformat(),
            "source_run_root": str(source), "source_stage_root": str(source_stage), "copied_stage_root": STAGE_PATH,
            "source_artifact_path": PROPOSAL_PATH, "source_artifact_sha256": proposal_hash,
            "copied_artifact_path": PROPOSAL_PATH,
            "checkpoint_path": f"{STAGE_PATH}/{checkpoint.relative_to(source_stage).as_posix()}",
            "checkpoint_sha256": file_sha256(checkpoint), "stage_files": stage_files,
            "stage_tree_sha256": digest(stage_files), "scientific_inputs": binding,
            "runtime_compatibility": "runtime differences allowed; research was executed only in the source run",
        }
        for name, (old, relative) in copies.items():
            shutil.copy2(old, root / relative)
            receipt[f"{name}_path"] = relative
            receipt[f"{name}_sha256"] = file_sha256(root / relative)
            state["artifacts"][relative] = receipt[f"{name}_sha256"]
        if _tree_files(destination) != stage_files or _tree_files(source_stage) != stage_files:
            raise ValueError("Research trace changed during reuse")
        atomic_json(root / RECEIPT_PATH, receipt)
        for name, sha in stage_files.items():
            state["artifacts"][f"{STAGE_PATH}/{name}"] = sha
        state["artifacts"][PROPOSAL_PATH] = proposal_hash
        state["artifacts"][RECEIPT_PATH] = file_sha256(root / RECEIPT_PATH)
        verify_research_reuse(root, manifest, state)


def verify_research_reuse(root: Path, manifest: dict[str, Any], state: dict[str, Any]) -> None:
    declaration = manifest.get("research_reuse")
    if declaration is None:
        if (root / RECEIPT_PATH).exists():
            raise ValueError("Research reuse receipt has no manifest declaration")
        return
    if declaration.get("receipt") != RECEIPT_PATH:
        raise ValueError("Research reuse receipt path changed")
    receipt_path = root / RECEIPT_PATH
    if state.get("artifacts", {}).get(RECEIPT_PATH) != file_sha256(receipt_path):
        raise ValueError("Research reuse receipt changed or lacks an artifact record")
    receipt = read_record(receipt_path)
    if (receipt.get("schema") != "research.reuse.v1" or receipt.get("copied_stage_root") != STAGE_PATH
            or receipt.get("copied_artifact_path") != PROPOSAL_PATH
            or receipt.get("source_run_root") != declaration.get("source_run_root")):
        raise ValueError("Research reuse provenance declaration changed")
    stage_files = _tree_files(root / STAGE_PATH)
    if stage_files != receipt["stage_files"] or digest(stage_files) != receipt["stage_tree_sha256"]:
        raise ValueError("Reused research trace changed")
    for name, sha in stage_files.items():
        if state["artifacts"].get(f"{STAGE_PATH}/{name}") != sha:
            raise ValueError("Reused trace file lacks an artifact record")
    for name in ("source_manifest", "source_state"):
        relative = f"reused_research/{name}.json"
        if (receipt.get(f"{name}_path") != relative
                or file_sha256(root / relative) != receipt[f"{name}_sha256"]
                or state["artifacts"].get(relative) != receipt[f"{name}_sha256"]):
            raise ValueError("Research reuse source receipt changed")
    old_manifest = read_record(root / "reused_research/source_manifest.json")
    old_state = read_record(root / "reused_research/source_state.json")
    if old_state.get("manifest_sha256") != receipt["source_manifest_sha256"]:
        raise ValueError("Research reuse archived manifest identity changed")
    _no_final_test(root / "reused_research", old_state)
    proposal_path = root / PROPOSAL_PATH
    if (file_sha256(proposal_path) != receipt["source_artifact_sha256"]
            or state["artifacts"].get(PROPOSAL_PATH) != receipt["source_artifact_sha256"]
            or old_state.get("artifacts", {}).get(PROPOSAL_PATH) != receipt["source_artifact_sha256"]):
        raise ValueError("Reused proposal changed or lacks its source artifact record")
    checkpoint = _passed_checkpoint(root / STAGE_PATH, proposal_path.read_text(encoding="utf-8"))
    if checkpoint.relative_to(root).as_posix() != receipt["checkpoint_path"] or file_sha256(checkpoint) != receipt["checkpoint_sha256"]:
        raise ValueError("Reused research checkpoint changed")
    current = _scientific_inputs(root, manifest)
    _same_inputs(receipt["scientific_inputs"], current)
    for key in ("task", "project", "data", "protocol_sha256", "context_sha256", "budget", "folder_context_files"):
        if old_manifest.get(key) != manifest.get(key):
            raise ValueError("Research reuse archived inputs differ: " + key)
