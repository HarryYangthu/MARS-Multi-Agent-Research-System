"""Bind frozen declarations to the existing owner, without inventing execution.

Until runtime adapters enforce every declared boundary, contract-backed runs
are inspectable, persisted pending tasks whose execution is explicitly blocked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.bridge.research_contract_service import (
    FrozenResearchTask, ResearchContractIntegrityError, validate_frozen_research_task,
)
from app.harness.persistence import atomic_write_json
from app.harness.runtime.research_contract import ContractModel, ResearchBudget
from app.storage.run_store import RunHandle

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


def create_research_run(orchestrator: Orchestrator, *, name: str, contract: FrozenResearchTask) -> RunSession:
    """Create one real session under the same API/CLI owner; never start it."""
    from app.bridge.orchestrator import RunRequest
    frozen = validate_frozen_research_task(contract, check_live_files=True)
    if not name.strip() or len(name) > 120:
        raise ValueError("Research run name must contain between 1 and 120 characters")
    return orchestrator.create_session(RunRequest(task=name.strip(), project=frozen.task.project.project_id,
        entrypoint="pipeline", user_request=frozen.task.goal, auto_approve=False), research_contract=frozen)
