"""Bind frozen declarations to the existing owner, without inventing execution.

Until runtime adapters enforce every declared boundary, contract-backed runs
are inspectable, persisted pending tasks whose execution is explicitly blocked.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.bridge.research_contract_service import (
    FrozenResearchTask, ProjectPreflightError, StaleResearchContractError,
    ResearchContractIntegrityError, validate_frozen_research_task,
)
from app.harness.persistence import atomic_write_json
from app.harness.runtime.research_contract import ContractModel, ResearchBudget
from app.storage.run_store import RunHandle, RunStore
from app.storage.run_state_store import RunStateStore
from app.harness.runtime.state_journal import StateJournal
from app.storage.research_creation_store import (
    CreationCatalogIntegrityError, CreationIntent, CreationRequestConflict, ResearchCreationStore, binding_sha256,
)

if TYPE_CHECKING:
    from app.bridge.orchestrator import Orchestrator, RunSession

CONTRACT_HASH_KEY = "research_task_sha256"
CONTRACT_FILE = "research_task.v1.json"


class ResearchExecutionBlocker(ContractModel):
    code: str
    message: str
    fields: tuple[str, ...] = ()


class ResearchExecutionAdmission(ContractModel):
    ready: Literal[False] = False
    enforced_budget_fields: tuple[str, ...] = ()
    blockers: tuple[ResearchExecutionBlocker, ...]


def _checked_input_path(run: RunHandle, name: str) -> Path:
    # Allow canonical system ancestor aliases such as /tmp, but never redirect
    # the run itself or one of its authority inputs to a different directory.
    if run.root.is_symlink():
        raise ResearchContractIntegrityError("Research contract run cannot be a symbolic link")
    root = run.root.resolve(strict=True)
    target = run.root / "input" / name
    if target.parent.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ResearchContractIntegrityError("Research contract input must remain inside its run")
    return target


def check_research_run_storage_paths(run: RunHandle) -> None:
    """Check declared contract paths before state loading can repair artifacts."""
    path = run.root / "input" / CONTRACT_FILE
    if CONTRACT_HASH_KEY not in run.meta and not path.exists() and not path.is_symlink():
        return
    _checked_input_path(run, CONTRACT_FILE)
    _checked_input_path(run, "run_request_options.v1.json")
    if (run.root / "run_meta.json").is_symlink():
        raise ResearchContractIntegrityError("Research contract metadata cannot be a symbolic link")


def load_run_research_contract(run: RunHandle, request_extra: dict[str, Any] | None = None) -> FrozenResearchTask | None:
    """Read persisted evidence only; do not preflight or fingerprint live code.

The independent run metadata, request options, and (when supplied) journal
request must all name the same frozen content. A missing file is never legacy
when any of those records still identifies a contract-backed run.
"""
    path = run.root / "input" / CONTRACT_FILE
    marker = run.meta.get(CONTRACT_HASH_KEY)
    requested = None if request_extra is None else request_extra.get(CONTRACT_HASH_KEY)
    if not (marker is not None or requested is not None or path.exists() or path.is_symlink()):
        return None
    try:
        path = _checked_input_path(run, CONTRACT_FILE)
        options = _checked_input_path(run, "run_request_options.v1.json")
        meta_path = run.root / "run_meta.json"
        if meta_path.is_symlink():
            raise ResearchContractIntegrityError("Research contract metadata cannot be a symbolic link")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if (not isinstance(metadata, dict) or metadata.get(CONTRACT_HASH_KEY) != marker
                or metadata.get("run_id") != run.run_id or metadata.get("project") != run.project
                or metadata.get("entrypoint") != run.entrypoint):
            raise ResearchContractIntegrityError("Research contract run identity differs from persisted metadata")
        frozen = validate_frozen_research_task(json.loads(path.read_text(encoding="utf-8")))
        raw = json.loads(options.read_text(encoding="utf-8"))
        if (not isinstance(raw, dict) or raw.get("schema_id") != "run_request_options.v1"
                or not isinstance(raw.get("extra"), dict)):
            raise ResearchContractIntegrityError("Research contract request binding is unavailable")
        if (marker != frozen.task_sha256 or raw["extra"].get(CONTRACT_HASH_KEY) != marker
                or request_extra is not None and requested != marker):
            raise ResearchContractIntegrityError("Research contract identity differs across persisted run records")
        if frozen.task.project.project_id != run.project:
            raise ResearchContractIntegrityError("Research contract belongs to a different project")
        if run.entrypoint != "pipeline":
            raise ResearchContractIntegrityError("Research contract is not bound to the supported pipeline topology")
        return frozen
    except ResearchContractIntegrityError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ResearchContractIntegrityError("Saved research contract is missing or invalid") from exc


def persist_run_research_contract(run: RunHandle, contract: FrozenResearchTask) -> None:
    """Write once before the new session receives executable state authority."""
    # The owner checked live files before reserving a run directory. Persist
    # that declaration unchanged; this is not an atomic source-code snapshot.
    frozen = validate_frozen_research_task(contract)
    if frozen.task.project.project_id != run.project or run.entrypoint != "pipeline":
        raise ResearchContractIntegrityError("Research contract does not match the new run")
    target = _checked_input_path(run, CONTRACT_FILE)
    with target.open("x", encoding="utf-8") as stream:
        stream.write(frozen.model_dump_json(indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    run.meta[CONTRACT_HASH_KEY] = frozen.task_sha256
    atomic_write_json(run.root / "run_meta.json", run.meta)


def research_execution_admission(run: RunHandle, request_extra: dict[str, Any] | None = None) -> ResearchExecutionAdmission | None:
    """No run may treat an unsupported declared budget as an enforced limit."""
    try:
        frozen = load_run_research_contract(run, request_extra)
    except ResearchContractIntegrityError:
        return ResearchExecutionAdmission(blockers=(ResearchExecutionBlocker(
            code="research_contract_integrity_error", message="Saved research contract binding is missing or invalid"),))
    if frozen is None:
        return None
    return ResearchExecutionAdmission(blockers=(
        ResearchExecutionBlocker(code="project_execution_adapter_pending",
            message="Declared commands, metrics, input scopes and write protection are not yet bound to the runtime",
            fields=("project.commands", "project.metrics", "project.paths", "project.allowed_paths", "project.protected_paths", "project.baseline_files")),
        ResearchExecutionBlocker(code="research_budget_enforcement_pending",
            message="The runtime has not yet bound all finite contract budgets; no model, tool or training call is authorized",
            fields=tuple("budget." + name for name in ResearchBudget.model_fields)),
    ))


def create_research_run(orchestrator: Orchestrator, *, name: str, contract: FrozenResearchTask,
                        on_run_allocated: Callable[[RunHandle], None] | None = None) -> RunSession:
    """Create one real session under the same API/CLI owner; never start it."""
    from app.bridge.orchestrator import RunRequest
    frozen = validate_frozen_research_task(contract, check_live_files=True)
    if not name.strip() or len(name) > 120:
        raise ValueError("Research run name must contain between 1 and 120 characters")
    return orchestrator.create_session(RunRequest(task=name.strip(), project=frozen.task.project.project_id,
        entrypoint="pipeline", user_request=frozen.task.goal, auto_approve=False), research_contract=frozen, on_run_allocated=on_run_allocated)


class ResearchRunCreated(ContractModel):
    run_id: str
    project: str
    task: str
    entrypoint: Literal["pipeline"] = "pipeline"
    created_at: str
    task_sha256: str
    status: Literal["created"] = "created"
    research_started: Literal[False] = False
    execution_admission: ResearchExecutionAdmission
    request_id: str | None = None
    idempotent: bool = False


class ResearchCreationStatus(ContractModel):
    request_id: str
    task_sha256: str | None = None
    status: Literal["pending", "created", "unknown", "rejected"]
    admitted: bool | None = None
    run_id: str | None = None
    research_started: Literal[False] = False
    run: ResearchRunCreated | None = None
    reason: str | None = None


def _created_receipt(run: RunHandle, extra: dict[str, Any], task_sha256: str,
                     request_id: str | None) -> ResearchRunCreated:
    admission = research_execution_admission(run, extra)
    if admission is None or any(item.code == "research_contract_integrity_error" for item in admission.blockers):
        raise ResearchContractIntegrityError("Saved creation evidence is unavailable")
    return ResearchRunCreated(run_id=run.run_id, project=run.project, task=run.task,
        created_at=run.created_at, task_sha256=task_sha256, execution_admission=admission,
        request_id=request_id, idempotent=request_id is not None)


def _verify_created_run(store: RunStore, intent: CreationIntent) -> ResearchRunCreated:
    """Read the actual authority without recovery, projection repair or live preflight."""
    if intent.run_id is None:
        raise ResearchContractIntegrityError("Creation allocation was not recorded")
    root = store.runs_root / intent.run_id
    if root.is_symlink() or (root / "run_meta.json").is_symlink():
        raise ResearchContractIntegrityError("Creation run metadata cannot be a symbolic link")
    run = store.get(intent.run_id)
    if run is None:
        raise ResearchContractIntegrityError("Allocated run is unavailable")
    check_research_run_storage_paths(run)
    for name in ("run_state.authority.json", "run_state.sqlite3"):
        path = run.root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ResearchContractIntegrityError("Creation authority must be an owned ordinary file")
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    if journal is None:
        raise ResearchContractIntegrityError("Creation authority is unavailable")
    # StateJournal.read() currently opens mode=rw; this request deliberately
    # uses a readonly connection and the same pure snapshot validators instead.
    connection = sqlite3.connect(journal.path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("SELECT run_id,journal_id,schema_version FROM identity WHERE id=1").fetchone() != (journal.run_id, journal.journal_id, 1):
            raise ResearchContractIntegrityError("Creation authority identity differs")
        snapshot = RunStateStore(run)._snapshot(StateJournal._read(connection))
    finally:
        connection.close()
    extra = snapshot.request.get("extra")
    if (not isinstance(extra, dict) or run.task != intent.name
            or snapshot.request.get("task") != intent.name
            or snapshot.request.get("project") != run.project
            or snapshot.request.get("entrypoint") != "pipeline"):
        raise ResearchContractIntegrityError("Creation request binding differs")
    frozen = load_run_research_contract(run, extra)
    if frozen is None or frozen.task_sha256 != intent.task_sha256 or snapshot.request.get("user_request") != frozen.task.goal:
        raise ResearchContractIntegrityError("Creation contract binding differs")
    return _created_receipt(run, extra, frozen.task_sha256, intent.request_id)


def _creation_status(store: RunStore, catalog: ResearchCreationStore, intent: CreationIntent,
                     *, owner_active: bool | None = None) -> ResearchCreationStatus:
    if intent.phase == "rejected":
        return ResearchCreationStatus(request_id=intent.request_id, task_sha256=intent.task_sha256, status="rejected", admitted=False,
                                      reason="creation_preflight_rejected")
    active = catalog.owner_active(intent.request_id) if owner_active is None else owner_active
    if active:
        return ResearchCreationStatus(request_id=intent.request_id, task_sha256=intent.task_sha256, status="pending", admitted=True, run_id=intent.run_id,
                                      reason="creation_in_progress")
    if intent.run_id is None:
        return ResearchCreationStatus(request_id=intent.request_id, task_sha256=intent.task_sha256, status="unknown",
                                      reason="creation_allocation_unconfirmed")
    try:
        run = _verify_created_run(store, intent)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return ResearchCreationStatus(request_id=intent.request_id, task_sha256=intent.task_sha256, status="unknown", run_id=intent.run_id,
                                      reason="creation_evidence_unavailable")
    return ResearchCreationStatus(request_id=intent.request_id, task_sha256=intent.task_sha256, status="created", admitted=True, run_id=run.run_id, run=run)


def lookup_research_creation(store: RunStore, request_id: str) -> ResearchCreationStatus | None:
    catalog = ResearchCreationStore(store.runs_root)
    intent = catalog.get(request_id)
    return None if intent is None else _creation_status(store, catalog, intent)


def save_research_run(orchestrator: Orchestrator, *, name: str, contract: FrozenResearchTask,
                      request_id: str | None = None) -> ResearchRunCreated | ResearchCreationStatus:
    """At most one allocation attempt per durable request; never starts research.

    A crash before allocation is bound can leave an orphan directory. Its ID
    cannot be safely inferred, so this request remains unknown and is not retried.
    """
    if request_id is None:
        session = create_research_run(orchestrator, name=name, contract=contract)
        return _created_receipt(session.run, session.request.extra, contract.task_sha256, None)
    frozen = validate_frozen_research_task(contract)  # Replay does not depend on mutable source files.
    if not name.strip() or len(name) > 120:
        raise ValueError("Research run name must contain between 1 and 120 characters")
    normalized_name = name.strip()
    catalog = ResearchCreationStore(orchestrator.run_store.runs_root)
    from app.storage.research_creation_store import validate_request_id
    validate_request_id(request_id)
    catalog.initialize()
    with catalog.lease(request_id) as acquired:
        if not acquired:
            existing = catalog.get(request_id)
            if existing is not None and existing.binding_sha256 != binding_sha256(normalized_name, frozen.task_sha256):
                raise CreationRequestConflict("Creation request identity already bound")
            if existing is None:
                # The other owner may still be preflighting. No accepted intent
                # is visible yet, so do not claim admission or invite a resend.
                return ResearchCreationStatus(request_id=request_id, status="unknown",
                    reason="creation_preflight_in_progress")
            return _creation_status(orchestrator.run_store, catalog, existing, owner_active=True)
        existing = catalog.get(request_id)
        if existing is not None:
            if existing.binding_sha256 != binding_sha256(normalized_name, frozen.task_sha256):
                raise CreationRequestConflict("Creation request identity already bound")
            result = _creation_status(orchestrator.run_store, catalog, existing, owner_active=False)
            return result.run if result.run is not None else result
        try:
            validate_frozen_research_task(frozen, check_live_files=True)
        except (ProjectPreflightError, StaleResearchContractError):
            rejected, _ = catalog.reserve(request_id, name=normalized_name, task_sha256=frozen.task_sha256, rejected=True)
            return _creation_status(orchestrator.run_store, catalog, rejected, owner_active=False)
        _, fresh = catalog.reserve(request_id, name=normalized_name, task_sha256=frozen.task_sha256)
        if not fresh:
            raise CreationCatalogIntegrityError("Creation intent changed while its lease was held")
        try:
            create_research_run(orchestrator, name=normalized_name, contract=frozen,
                on_run_allocated=lambda run: catalog.bind_run(request_id, run.run_id))
            bound = catalog.get(request_id)
            if bound is None:
                raise CreationCatalogIntegrityError("Creation intent disappeared")
            receipt = _verify_created_run(orchestrator.run_store, bound)
        except Exception:
            catalog.finish(request_id, created=False)
            raise
        catalog.finish(request_id, created=True)
        return receipt
