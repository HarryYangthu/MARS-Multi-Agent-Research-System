"""Reject corrupt filesystem receipts; positive import needs a real passed run."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.bridge.cli_research_service import initialize
from app.cli import parser
from app.harness.agent_loop.trace import atomic_json
from app.harness.research_reuse import (
    RECEIPT_PATH, STAGE_PATH, _no_final_test, _passed_checkpoint, _same_inputs,
    _tree_files, reuse_research, verify_research_reuse,
)
from app.harness.research_trial import ResearchBudget, file_sha256, read_record


def test_reuse_flag_parses_source_path() -> None:
    options = parser().parse_args(["research", "--repo", "/research", "--data", "/capture.pth",
                                  "--reuse-research-from", "/previous_run"])
    assert options.reuse_research_from == Path("/previous_run")


@pytest.mark.parametrize("key", ["task", "project", "data", "data_sha256", "protocol_sha256",
                                 "context_sha256", "budget", "source_snapshot_files"])
def test_scientific_input_difference_is_rejected(key: str) -> None:
    source = {key: "original"}
    with pytest.raises(ValueError, match=key):
        _same_inputs(source, {key: "different"})


@pytest.mark.parametrize("state", [
    {"trials": {"final_baseline": {"status": "failed"}}},
    {"trials": {"renamed": {"operation": "finalize"}}},
    {"trials": {"renamed": {"test": {}}}},
    {"trials": {}, "trial_history": {"final_candidate": [{"status": "failed"}]}},
    {"trials": {}, "final_comparison": {}},
    {"trials": {}, "artifacts": {"execution/final_baseline/attempt_01/result.json": "old-archive-hash"}},
])
def test_source_with_final_execution_is_rejected(tmp_path: Path, state: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="final-test"):
        _no_final_test(tmp_path, state)


def test_unrecorded_final_directory_is_also_rejected(tmp_path: Path) -> None:
    (tmp_path / "execution/final_baseline/attempt_01").mkdir(parents=True)
    with pytest.raises(ValueError, match="final-test"):
        _no_final_test(tmp_path, {"trials": {}})


def test_trace_symlink_is_rejected(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    reference = tmp_path / "outside.json"
    reference.write_text("{}")
    (stage / "escape.json").symlink_to(reference)
    with pytest.raises(ValueError, match="symlinks"):
        _tree_files(stage)


@pytest.mark.parametrize("status,candidate,error", [
    ("failed", "unaccepted file", "final idea checkpoint to be passed"),
    ("passed", "different untrusted file", "differs from the passed checkpoint"),
])
def test_corrupt_checkpoint_claims_fail_before_import(tmp_path: Path, status: str, candidate: str, error: str) -> None:
    # Deliberately invalid file claims, never a substitute for successful model execution.
    trace = tmp_path / "agent_traces/idea/rejected_receipt"
    atomic_json(trace / "checkpoint.json", {"status": status, "candidate": candidate})
    atomic_json(trace / "facts.json", {"status": "passed"})
    with pytest.raises(ValueError, match=error):
        _passed_checkpoint(tmp_path, "different proposal bytes")


def test_source_manifest_corruption_is_rejected(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    target.mkdir()
    atomic_json(source / "input/manifest.json", {"schema": "invalid-integrity-fixture"})
    atomic_json(source / "state.json", {"manifest_sha256": "not-the-file-hash"})
    with pytest.raises(ValueError, match="source manifest changed"):
        reuse_research(target, {}, {"artifacts": {}}, source)
    assert not (target / "idea/proposal.md").exists()


@pytest.fixture(scope="module")
def real_reuse(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any], dict[str, Any], Path]:
    raw = os.environ.get("MARS_TEST_REUSE_RUN")
    if not raw:
        pytest.skip("Set MARS_TEST_REUSE_RUN to an actual accepted pre-test research run")
    source = Path(raw).resolve()
    old = read_record(source / "input/manifest.json")
    target = tmp_path_factory.mktemp("real_research_reuse") / "run"
    state = initialize(Path(old["repo"]), Path(old["data"]), target, old["task"], old["model"],
                       ResearchBudget.model_validate(old["budget"]), reuse_research_from=source)
    return target, read_record(target / "input/manifest.json"), state, source


def test_actual_passed_research_can_be_imported_without_a_new_call(real_reuse: tuple[Path, dict[str, Any], dict[str, Any], Path]) -> None:
    root, manifest, state, source = real_reuse
    receipt = read_record(root / RECEIPT_PATH)
    assert receipt["source_run_root"] == str(source)
    assert (root / "idea/proposal.md").read_bytes() == (source / "idea/proposal.md").read_bytes()
    assert not (root / "stages/idea/proposal").exists()
    assert receipt["stage_files"] == _tree_files(source / "stages/idea/proposal")
    assert file_sha256(root / receipt["checkpoint_path"]) == receipt["checkpoint_sha256"]
    assert state["trials"] == {} and state["attempts"] == {}
    verify_research_reuse(root, manifest, state)
    # Runtime compatibility is intentionally not a scientific-input equality gate.
    changed_runtime = {**manifest, "runtime_hashes": {"new_runtime.py": "different-runtime-version"}}
    verify_research_reuse(root, changed_runtime, state)


@pytest.mark.parametrize("target", ["proposal", "trace", "receipt", "manifest"])
def test_imported_real_evidence_tampering_is_rejected(real_reuse: tuple[Path, dict[str, Any], dict[str, Any], Path], target: str) -> None:
    root, manifest, state, _ = real_reuse
    receipt = read_record(root / RECEIPT_PATH)
    relative = {"proposal": "idea/proposal.md", "trace": receipt["checkpoint_path"],
                "receipt": RECEIPT_PATH, "manifest": "reused_research/source_manifest.json"}[target]
    path = root / relative
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="changed"):
            verify_research_reuse(root, manifest, state)
    finally:
        path.write_bytes(original)


def test_extra_trace_file_and_changed_task_are_rejected(real_reuse: tuple[Path, dict[str, Any], dict[str, Any], Path]) -> None:
    root, manifest, state, _ = real_reuse
    extra = root / STAGE_PATH / "unrecorded.json"
    try:
        extra.write_text("{}")
        with pytest.raises(ValueError, match="trace changed"):
            verify_research_reuse(root, manifest, state)
    finally:
        extra.unlink()
    changed = deepcopy(manifest)
    changed["task"] += " Different research question."
    with pytest.raises(ValueError, match="inputs differ: task"):
        verify_research_reuse(root, changed, state)


@pytest.mark.parametrize("corruption", ["checkpoint_review", "last_review", "unfinished"])
def test_actual_trace_requires_independent_acceptance(real_reuse: tuple[Path, dict[str, Any], dict[str, Any], Path], corruption: str) -> None:
    root, _, _, _ = real_reuse
    receipt = read_record(root / RECEIPT_PATH)
    checkpoint = root / receipt["checkpoint_path"]
    path = checkpoint if corruption == "checkpoint_review" else checkpoint.parent / "events.jsonl"
    original = path.read_bytes()
    try:
        if corruption == "checkpoint_review":
            data = read_record(path)
            data["reflection_accepted"] = False
            atomic_json(path, data)
        else:
            rows = [json.loads(line) for line in original.decode().splitlines()]
            kind = "reflection" if corruption == "last_review" else "finished"
            final = next(row for row in reversed(rows) if row.get("kind") == kind)
            if corruption == "last_review":
                final["visible"]["accept"] = False
            else:
                final["status"] = "interrupted"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with pytest.raises(ValueError, match="accepted independent review"):
            _passed_checkpoint(root / STAGE_PATH, (root / "idea/proposal.md").read_text())
    finally:
        path.write_bytes(original)
