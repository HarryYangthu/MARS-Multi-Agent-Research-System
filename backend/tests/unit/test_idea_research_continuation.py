"""Continuation guards and actual archive copies; no replacement model/tool results."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.idea.research_delegate import load_delegated_research, resumed_delegation_count
from app.agents.idea.research_handoff import load_research_handoff
from app.harness.agent_loop.trace import atomic_json
from scripts.idea_research_continuation import (
    check_terminal_state, copy_verified_files, external_evidence, file_manifest,
    load_continuation, remaining_seconds,
)


@pytest.mark.parametrize("status", ["gap", "budget_exhausted", "validation_exhausted", "passed", "running"])
def test_terminal_outcomes_cannot_acquire_new_budgets(status: str) -> None:
    with pytest.raises(ValueError, match="only supports"):
        check_terminal_state({"status": status}, {"trace": "full", "max_model_calls": 14})


@pytest.mark.parametrize("pending,batch", [("tool", None), (None, {"calls": ["unfinished"]})])
def test_unknown_tool_outcome_is_not_replayed(pending: str | None, batch: Any) -> None:
    with pytest.raises(ValueError, match="automatic replay is forbidden"):
        check_terminal_state({"status": "interrupted", "pending": pending, "pending_batch": batch}, {})


def test_guards_preserve_candidate_history_and_unknown_usage() -> None:
    # Authored input to a pure guard, not a generated model/tool response.
    state = {"status": "model_error", "pending": "model", "counts": {"model_requests": 13},
             "candidate": "caller-owned bytes", "history": [], "usage_complete": False}
    before = json.dumps(state)
    check_terminal_state(state, {"trace": "full", "max_model_calls": 14})
    assert json.dumps(state) == before
    with pytest.raises(ValueError, match="exhausted"):
        check_terminal_state(state, {"trace": "full", "max_model_calls": 13})


@pytest.mark.parametrize("maximum,elapsed", [(None, 12), (1200, None), (1200, 1200),
                                             (1200, -1), (float("inf"), 10), (True, 0),
                                             (1800, 1800), (3601, 0)])
def test_runtime_budget_cannot_be_invented_or_reset(maximum: Any, elapsed: Any) -> None:
    with pytest.raises(ValueError, match="runtime budget"):
        remaining_seconds({"resource_limits": {"max_seconds": maximum}}, {"duration_seconds": elapsed})


def test_chained_continuation_uses_cumulative_runtime() -> None:
    assert remaining_seconds({"resource_limits": {"max_seconds": 1200}},
                             {"duration_seconds": 300, "cumulative_duration_seconds": 1100}) == 100
    # A longer newly declared run may continue, but the earlier 1200-second
    # run above remains exhausted; the global ceiling never adds old budget.
    assert remaining_seconds({"resource_limits": {"max_seconds": 1800}},
                             {"duration_seconds": 1200}) == 600


def test_copy_preserves_authored_file_bytes_and_rejects_source_changes(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "fork"
    source.mkdir()
    (source / "reference.txt").write_bytes(b"Caller-owned reference bytes.\x00\n")
    hashes = file_manifest(source)
    copy_verified_files(source, target, hashes)
    assert file_manifest(source) == file_manifest(target) == hashes
    (source / "reference.txt").write_text("changed outside the copy")
    with pytest.raises(ValueError, match="changed before"):
        copy_verified_files(source, tmp_path / "second", hashes)


def test_copy_rejects_aliases_to_original_files(tmp_path: Path) -> None:
    (tmp_path / "reference").write_text("original")
    (tmp_path / "alias").symlink_to(tmp_path / "reference")
    with pytest.raises(ValueError, match="regular files"):
        file_manifest(tmp_path)


def test_existing_real_archive_copies_with_unchanged_receipts_and_handoff(tmp_path: Path) -> None:
    supplied = os.environ.get("MARS_TEST_CONTINUATION_ARCHIVE")
    if not supplied:
        pytest.skip("requires an existing real research archive; no replacement execution")
    source = Path(supplied).resolve()
    before = file_manifest(source)
    target = tmp_path / "different-continuation-run-id"
    original_external = external_evidence(source)
    copy_verified_files(source, target, before)
    checkpoint = next(target.glob("agent_traces/idea/*/checkpoint.json"))
    state = json.loads(checkpoint.read_text())
    history = state["history"]
    assert load_delegated_research(target, history) == load_delegated_research(source, history)
    assert external_evidence(target) == original_external
    proposal = source / "idea/idea_proposal.v1.md"
    if proposal.exists():
        initial = json.loads((source / "input/request.json").read_text())
        project = initial["scenario"]["project"]
        assert load_research_handoff(target, proposal.read_text(), project=project) == load_research_handoff(source, proposal.read_text(), project=project)
    assert file_manifest(source) == file_manifest(target) == before
    if state["status"] in {"model_error", "interrupted"}:
        original_count = resumed_delegation_count(source, history, run_id=source.name,
            parent_invocation=str(source / checkpoint.parent.relative_to(target)))
        with pytest.raises(ValueError, match="parent observation"):
            resumed_delegation_count(target, history, run_id=target.name, parent_invocation=str(checkpoint.parent))
        # A host lineage witness over actual bytes, not a replacement Agent run.
        manifest: dict[str, Any] = {"schema": "idea.research_continuation.v1", "source_run_root": str(source),
                    "source_run_id": source.name, "continuation_run_id": target.name,
                    "invocation": checkpoint.parent.name, "source_files_sha256": dict(before), "budgets_reset": False}
        atomic_json(target / "input/continuation.json", manifest)
        assert resumed_delegation_count(target, history, run_id=target.name,
            parent_invocation=str(checkpoint.parent)) == original_count
        manifest["source_files_sha256"][checkpoint.relative_to(target).as_posix()] = "0" * 64
        atomic_json(target / "input/continuation.json", manifest)
        with pytest.raises(ValueError, match="checkpoint hash mismatch"):
            resumed_delegation_count(target, history, run_id=target.name, parent_invocation=str(checkpoint.parent))
        assert file_manifest(source) == before
    # Current source compatibility is deliberately not claimed by this file-copy test.
    with pytest.raises(ValueError, match="original source commit"):
        load_continuation(source, source_commit="different-source-rejected", source_tree="different-tree")
