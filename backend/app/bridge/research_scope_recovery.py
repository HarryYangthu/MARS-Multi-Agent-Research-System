"""Seal a host-created project capability in the existing run authority.

Restoration never re-reads live source or initializes a ledger. This does not
admit research execution, install a scheduler or grant command/data access.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal

from pydantic import Field

from app.bridge.research_run_service import load_run_research_contract, validate_saved_run_journal_schema
from app.harness.runtime.project_scope import ProjectScope, safe_scope_path, verify_candidate_scope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.research_contract import ContractModel, relative_scope
from app.harness.runtime.state_journal import StateJournal
from app.storage.run_store import RunHandle


class ScopeRecoveryError(ValueError):
    """Evidence is missing or changed; never reconstruct permissions by guessing."""


class _Binding(ContractModel):
    schema_id: Literal["research_project_scope_binding.v1"] = "research_project_scope_binding.v1"
    run_id: str
    project: str
    journal_id: str
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    readable_files: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    forbidden_patterns: tuple[str, ...]
    metadata_hashes: tuple[tuple[str, str], ...]
    scope_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _encoded(value: _Binding) -> str:
    return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(run: RunHandle, ledger: ResearchBudgetLedger) -> tuple[Path, dict[str, Any]]:
    root = run.root.resolve(strict=True)
    if run.root.is_symlink() or ledger.journal.path.parent != root or ledger.journal.run_id != run.run_id:
        raise ScopeRecoveryError("Project recovery belongs to another run")
    for relative in ("run_state.authority.json", "run_state.sqlite3", "run_meta.json",
                     "input/research_task.v1.json", "input/run_request_options.v1.json"):
        safe_scope_path(root, relative, must_exist=True)
    declared = StateJournal.from_authority(root, run_id=run.run_id)
    if declared is None or declared.journal_id != ledger.journal.journal_id or declared.path != ledger.journal.path:
        raise ScopeRecoveryError("Project recovery authority marker differs")
    # The caller supplies an already initialized ledger. No budgets or clocks
    # are reset as a side effect of capability restoration.
    with ledger.journal.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        validate_saved_run_journal_schema(connection)
        ledger.in_transaction(connection).snapshot()
        request = ledger.journal._read(connection).get("request")
    extra = request.get("extra") if isinstance(request, dict) else None
    if not isinstance(extra, dict):
        raise ScopeRecoveryError("Project recovery has no authoritative request")
    return root, extra


def _scope(run: RunHandle, ledger: ResearchBudgetLedger, binding: _Binding) -> ProjectScope:
    root, extra = _identity(run, ledger)
    frozen = load_run_research_contract(run, extra)
    if (frozen is None or frozen.task_sha256 != ledger.task_sha256
            or (binding.run_id, binding.project, binding.journal_id, binding.task_sha256)
            != (run.run_id, run.project, ledger.journal.journal_id, ledger.task_sha256)):
        raise ScopeRecoveryError("Project recovery frozen identity differs")
    project = frozen.task.project
    for name in (*binding.readable_files, *binding.excluded_paths):
        relative_scope(name)
    if tuple(sorted(set(binding.readable_files))) != binding.readable_files:
        raise ScopeRecoveryError("Project recovery read scope differs")
    required_metadata = {"AGENTS.md", "project.yaml", "repo_link.yaml"}
    allowed_metadata = required_metadata | ({"knowledge.md"} if project.paths.knowledge else set())
    metadata_names = set(name for name, _ in binding.metadata_hashes)
    if (not required_metadata <= metadata_names <= allowed_metadata
            or len(binding.metadata_hashes) != len(metadata_names)
            or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for _, digest in binding.metadata_hashes)):
        raise ScopeRecoveryError("Project recovery metadata evidence differs")
    scope = ProjectScope(run_id=run.run_id, project=run.project, task_sha256=ledger.task_sha256,
        run_root=root, metadata_root=root / "context/project_scope/projects" / binding.candidate_id,
        snapshot_root=root / "context/project_scope/snapshots" / binding.snapshot_id,
        candidate_root=root / "context/project_scope/candidates" / binding.candidate_id,
        snapshot_id=binding.snapshot_id, readable_files=binding.readable_files,
        allowed_write_paths=project.allowed_paths,
        protected_paths=tuple(sorted(set((*project.baseline_files, *project.protected_paths, "AGENTS.md")))),
        excluded_paths=binding.excluded_paths, data_references=project.paths.data,
        metadata_hashes=binding.metadata_hashes, forbidden_patterns=binding.forbidden_patterns)
    scope.validate_identity(run.project, run.run_id)
    record_path = scope.run_file(f"context/project_scope/{binding.candidate_id}.json", must_exist=True)
    if _sha(record_path.read_bytes()) != binding.scope_record_sha256:
        raise ScopeRecoveryError("Project recovery preparation record changed")
    # The older snapshot reader verifies content/mode and symbolic links, but
    # content equality alone does not establish physical inode isolation.
    for relative in ("snapshot_manifest.json", *scope.readable_files):
        safe_scope_path(scope.snapshot_root, relative, must_exist=True)
    verify_candidate_scope(scope)
    return scope


def seal_project_scope(run: RunHandle, scope: ProjectScope, ledger: ResearchBudgetLedger) -> None:
    """Host-only, explicit write-once binding, after preparation and before use.

    Ordinary preparation JSON alone can never authorize recovery. A crash before
    this transaction commits leaves unsealed files which cannot be restored.
    """
    root, _extra = _identity(run, ledger)
    scope.validate_identity(run.project, run.run_id)
    if scope.run_root != root:
        raise ScopeRecoveryError("Project capability root differs")
    record = scope.run_file(f"context/project_scope/{scope.candidate_root.name}.json", must_exist=True)
    binding = _Binding(run_id=run.run_id, project=run.project, journal_id=ledger.journal.journal_id,
        task_sha256=ledger.task_sha256, candidate_id=scope.candidate_root.name, snapshot_id=scope.snapshot_id,
        readable_files=scope.readable_files, excluded_paths=scope.excluded_paths,
        forbidden_patterns=scope.forbidden_patterns, metadata_hashes=scope.metadata_hashes,
        scope_record_sha256=_sha(record.read_bytes()))
    if _scope(run, ledger, binding) != scope:
        raise ScopeRecoveryError("Host capability differs from the frozen restoration contract")
    encoded = _encoded(binding)
    try:
        with ledger.journal.transaction() as connection:
            ledger.in_transaction(connection).snapshot()
            # Binding is an explicit extension of the same authority. It owns
            # permissions only, never a parallel copy of run/stage/job state.
            connection.execute("""CREATE TABLE IF NOT EXISTS research_project_scopes (
                candidate_id TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version=1),
                binding TEXT NOT NULL, binding_sha256 TEXT NOT NULL)""")
            row = connection.execute("SELECT version,binding,binding_sha256 FROM research_project_scopes WHERE candidate_id=?",
                                     (binding.candidate_id,)).fetchone()
            expected = (1, encoded, _sha(encoded.encode()))
            if row is not None:
                if row != expected:
                    raise ScopeRecoveryError("Sealed project capability is immutable")
                return
            if ledger.journal._read(connection).get("status") != "created":
                raise ScopeRecoveryError("New project capability must be sealed before execution")
            connection.execute("INSERT INTO research_project_scopes VALUES (?,?,?,?)", (binding.candidate_id, *expected))
    except sqlite3.Error as exc:
        raise ScopeRecoveryError("Project capability authority is unavailable") from exc


def restore_project_scope(run: RunHandle, *, candidate_id: str, ledger: ResearchBudgetLedger) -> ProjectScope:
    """Read-only restoration from SQL and saved evidence; never uses live source."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", candidate_id) is None:
        raise ScopeRecoveryError("Invalid candidate identity")
    _identity(run, ledger)
    try:
        with ledger.journal.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            ledger.in_transaction(connection).snapshot()
            row = connection.execute("SELECT version,binding,binding_sha256 FROM research_project_scopes WHERE candidate_id=?",
                                     (candidate_id,)).fetchone()
        if (row is None or row[0] != 1 or not isinstance(row[1], str)
                or row[2] != _sha(row[1].encode())):
            raise ScopeRecoveryError("Project capability has no verified SQL binding")
        binding = _Binding.model_validate_json(row[1])
        if binding.candidate_id != candidate_id or _encoded(binding) != row[1]:
            raise ScopeRecoveryError("Project capability binding differs")
        return _scope(run, ledger, binding)
    except sqlite3.Error as exc:
        raise ScopeRecoveryError("Project capability authority is unavailable") from exc
