"""Real saved source, SQL transactions and subprocess restoration; no doubles."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from app.bridge.research_scope_recovery import restore_project_scope, seal_project_scope
from app.harness.runtime.project_scope import ProjectScope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.storage.run_store import RunHandle, RunStore
from tests.unit.test_research_tool_accounting import call, setup


def fixture(root: Path) -> tuple[RunHandle, ProjectScope, ResearchBudgetLedger]:
    scope, execution, _ = setup(root)
    run = RunStore(scope.run_root.parent).get(scope.run_id)
    assert run is not None
    return run, scope, execution.ledger


def files(root: Path) -> dict[str, tuple[str, int]]:
    return {path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def test_unsealed_json_cannot_grant_recovery_and_lookup_is_readonly(tmp_path: Path) -> None:
    run, scope, ledger = fixture(tmp_path)
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_project_scope(run, candidate_id="one", ledger=ledger)
    assert files(run.root) == before
    assert not (run.root / "resources/model_budget.v1.json").exists()


def test_seal_and_restore_do_not_read_live_source_or_reset_actual_tool_usage(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    run = RunStore(scope.run_root.parent).get(scope.run_id)
    assert run is not None
    seal_project_scope(run, scope, execution.ledger)
    result = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "VALUE = 2\n"})
    assert result.ok
    (tmp_path / "source").rename(tmp_path / "source-removed")
    before = files(run.root)
    restored = restore_project_scope(run, candidate_id="one", ledger=execution.ledger)
    assert restored == scope
    assert restored.resolve_file("candidate.py").read_text() == "VALUE = 2\n"
    assert execution.ledger.snapshot().used["tool_executions"] == 1
    assert execution.ledger.snapshot().used["implemented_candidates"] == 1
    assert files(run.root) == before
    with pytest.raises(ValueError):
        restored.resolve_file("baseline.py", write=True)


def test_new_process_reconstructs_only_saved_capability(tmp_path: Path) -> None:
    run, scope, ledger = fixture(tmp_path)
    seal_project_scope(run, scope, ledger)
    (tmp_path / "source").rename(tmp_path / "gone")
    script = """
import sys
from pathlib import Path
from app.storage.run_store import RunStore
from app.bridge.research_run_service import load_run_research_contract
from app.bridge.research_scope_recovery import restore_project_scope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.state_journal import StateJournal
root = Path(sys.argv[1])
run = RunStore(root.parent).get(root.name)
assert run is not None
frozen = load_run_research_contract(run)
assert frozen is not None
journal = StateJournal.from_authority(root, run_id=run.run_id)
assert journal is not None
ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget)
scope = restore_project_scope(run, candidate_id='one', ledger=ledger)
assert scope.resolve_file('candidate.py').read_text() == 'VALUE = 1\\n'
assert ledger.snapshot().used['model_requests'] == 0
"""
    before = files(run.root)
    result = subprocess.run([sys.executable, "-c", script, str(run.root)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert files(run.root) == before


@pytest.mark.parametrize("target", ["metadata", "record", "snapshot", "marker", "baseline", "extra", "candidate_marker"])
def test_tampered_evidence_fails_without_repair(tmp_path: Path, target: str) -> None:
    run, scope, ledger = fixture(tmp_path)
    seal_project_scope(run, scope, ledger)
    targets = {"metadata": scope.metadata_root / "project.yaml",
        "record": run.root / "context/project_scope/one.json", "snapshot": scope.snapshot_root / "baseline.py",
        "marker": run.root / "run_state.authority.json", "baseline": scope.candidate_root / "baseline.py",
        "extra": scope.candidate_root / "unauthorized.py", "candidate_marker": scope.candidate_root / ".mars_candidate_workspace.json"}
    path = targets[target]
    if path.exists():
        path.chmod(0o644)
    path.write_text("changed\n")
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_project_scope(run, candidate_id="one", ledger=ledger)
    assert files(run.root) == before


@pytest.mark.parametrize("damage", ["hash", "version", "delete", "drop", "bad_table"])
def test_missing_or_corrupt_sql_binding_does_not_fall_back(tmp_path: Path, damage: str) -> None:
    run, scope, ledger = fixture(tmp_path)
    seal_project_scope(run, scope, ledger)
    with sqlite3.connect(ledger.journal.path) as connection:
        if damage == "hash":
            connection.execute("UPDATE research_project_scopes SET binding_sha256=?", ("0" * 64,))
        elif damage == "version":
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE research_project_scopes SET version=2")
        elif damage == "delete":
            connection.execute("DELETE FROM research_project_scopes")
        else:
            connection.execute("DROP TABLE research_project_scopes")
            if damage == "bad_table":
                connection.execute("CREATE TABLE research_project_scopes (wrong TEXT)")
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_project_scope(run, candidate_id="one", ledger=ledger)
    assert files(run.root) == before


@pytest.mark.parametrize("change", ["writes", "protected", "outside_metadata", "foreign_run", "foreign_ledger"])
def test_seal_cannot_expand_frozen_permissions_or_change_identity(tmp_path: Path, change: str) -> None:
    run, scope, ledger = fixture(tmp_path / "a")
    other_run, other_scope, other_ledger = fixture(tmp_path / "b")
    if change == "writes":
        scope = replace(scope, allowed_write_paths=("baseline.py", "candidate.py"))
    elif change == "protected":
        scope = replace(scope, protected_paths=())
    elif change == "outside_metadata":
        scope = replace(scope, metadata_root=other_scope.metadata_root)
    elif change == "foreign_run":
        run = other_run
    else:
        ledger = other_ledger
    before = files(tmp_path)
    with pytest.raises(ValueError):
        seal_project_scope(run, scope, ledger)
    assert files(tmp_path) == before


def test_repeated_seal_is_immutable_and_preserves_current_candidate(tmp_path: Path) -> None:
    run, scope, ledger = fixture(tmp_path)
    seal_project_scope(run, scope, ledger)
    (scope.candidate_root / "candidate.py").write_text("VALUE = 7\n")
    before = files(run.root)
    seal_project_scope(run, scope, ledger)
    assert files(run.root) == before
    with pytest.raises(ValueError):
        seal_project_scope(run, replace(scope, forbidden_patterns=("*",)), ledger)
    assert files(run.root) == before


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_redirected_metadata_rejected_without_external_write(tmp_path: Path, link: str) -> None:
    run, scope, ledger = fixture(tmp_path)
    seal_project_scope(run, scope, ledger)
    path = scope.metadata_root / "project.yaml"
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    if link == "symlink":
        path.symlink_to(outside)
    else:
        os.link(outside, path)
    original = outside.read_bytes()
    with pytest.raises(ValueError):
        restore_project_scope(run, candidate_id="one", ledger=ledger)
    assert outside.read_bytes() == original


@pytest.mark.parametrize("relative", ["baseline.py", "snapshot_manifest.json"])
def test_snapshot_hardlink_alias_cannot_restore_even_with_identical_bytes(tmp_path: Path, relative: str) -> None:
    run, scope, ledger = fixture(tmp_path)
    seal_project_scope(run, scope, ledger)
    path = scope.snapshot_root / relative
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    outside.chmod(path.stat().st_mode & 0o777)
    scope.snapshot_root.chmod(0o755)
    path.unlink()
    os.link(outside, path)
    before = files(tmp_path)
    with pytest.raises(ValueError):
        restore_project_scope(run, candidate_id="one", ledger=ledger)
    assert files(tmp_path) == before


def test_new_binding_after_execution_is_refused_atomically(tmp_path: Path) -> None:
    run, scope, ledger = fixture(tmp_path)
    with ledger.journal.transaction() as connection:
        row = connection.execute("SELECT payload FROM run_state WHERE id=1").fetchone()
        payload: dict[str, Any] = json.loads(row[0])
        payload["status"] = "running"
        connection.execute("UPDATE run_state SET payload=? WHERE id=1", (json.dumps(payload),))
    before = files(run.root)
    with pytest.raises(ValueError):
        seal_project_scope(run, scope, ledger)
    assert files(run.root) == before
    with pytest.raises(ValueError):
        restore_project_scope(run, candidate_id="one", ledger=ledger)
