"""Best-effort control of every SQL-owned job without losing later jobs to errors.

This reports an observed set, not a completed run transition. The orchestrator
still owns dispatch barriers, stage state and polling; no LLM is involved.
"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.bridge.research_job_service import ResearchJobService, ResearchJobView
from app.harness.persistence import path_lock
from app.harness.runtime.project_scope import safe_scope_path
from app.settings import repo_root


class JobControlPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lock_timeout_seconds: float = Field(gt=0, le=5, strict=True)


def load_job_control_policy(path: Path | None = None) -> JobControlPolicy:
    raw = yaml.safe_load((path or repo_root() / "configs/research_jobs.yaml").read_text())
    return JobControlPolicy.model_validate(raw["research_job_control"])


class JobControlFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: str
    code: Literal["job_control_unconfirmed"] = "job_control_unconfirmed"


class JobControlBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    action: Literal["stop", "reconcile"]
    observed_job_ids: tuple[str, ...]
    jobs: tuple[ResearchJobView, ...]
    failures: tuple[JobControlFailure, ...]
    unverified_catalog_rows: int = Field(ge=0)
    dispatch_barrier_observed: bool
    catalog_unchanged: bool
    all_observed_jobs_terminal: bool
    # This helper never transitions or certifies the whole research lifecycle.
    run_stop_confirmed: Literal[False] = False


def _catalog(service: ResearchJobService) -> tuple[tuple[str, ...], str, int]:
    # Stop must remain possible when external frozen input is missing. Require
    # actual existing SQL ownership, not a fresh full budget/file admission.
    with service.ledger.journal.transaction() as connection:
        service._extension(connection)
        payload = service.ledger.journal._read(connection)
        request = payload.get("request")
        extra = request.get("extra") if isinstance(request, dict) else None
        if (payload.get("run_id") != service.run.run_id or payload.get("project") != service.run.project
                or not isinstance(extra, dict) or extra.get("research_task_sha256") != service.ledger.task_sha256):
            raise ValueError("Job catalog does not match the authoritative run")
        rows = tuple(row[0] for row in connection.execute("SELECT job_id FROM research_jobs ORDER BY job_id"))
        identifiers = tuple(identifier for identifier in rows if isinstance(identifier, str)
                            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", identifier) is not None)
        invalid_rows = len(rows) - len(identifiers)
        status = payload.get("status")
        if not isinstance(status, str):
            raise ValueError("Job catalog has no run state")
        return identifiers, status, invalid_rows


def control_owned_jobs(service: ResearchJobService, *, action: Literal["stop", "reconcile"],
                       policy: JobControlPolicy) -> JobControlBatch:
    """One explicit bounded-lock pass; never submits, retries or guesses a PID.

    A failed job does not prevent control of other verified SQL-owned jobs.
    Errors are deliberately generic, without copied command/credential text.
    SQLite/filesystem access is synchronous; lock timeout is not a total SLA.
    """
    if action not in {"stop", "reconcile"}:
        raise ValueError("Unknown job control action")
    identifiers, initial_status, invalid_rows = _catalog(service)
    views: list[ResearchJobView] = []
    failures: list[JobControlFailure] = []
    for job_id in identifiers:
        try:
            outer = safe_scope_path(service.root, f"execution/research_job_controls/{job_id}.lock")
            inner = safe_scope_path(service.root, f"execution/local_jobs/{job_id}/control.lock")
            # Acquire the exact shared reentrant locks before entering existing
            # methods, so a busy first job cannot block every following job.
            with ExitStack() as stack:
                stack.enter_context(path_lock(outer, timeout=policy.lock_timeout_seconds))
                if inner.parent.is_dir():
                    stack.enter_context(path_lock(inner, timeout=policy.lock_timeout_seconds))
                views.append(service.stop(job_id) if action == "stop" else service.status(job_id))
        except Exception:
            # This per-job boundary must isolate malformed persisted evidence
            # and adapter failures too. Process interrupts still propagate.
            failures.append(JobControlFailure(job_id=job_id))
    try:
        final_ids, final_status, final_invalid_rows = _catalog(service)
        invalid_rows = max(invalid_rows, final_invalid_rows)
        unchanged = final_ids == identifiers and not invalid_rows
    except Exception:
        final_status, unchanged = "unknown", False
    # Observation only: a future owner must hold its own dispatch lifecycle.
    barriers = {"pausing", "paused", "cancelling", "cancelled", "blocked", "failed", "completed"}
    barrier = initial_status in barriers and final_status in barriers
    terminal = (not failures and not invalid_rows and unchanged and len(views) == len(identifiers)
                and all(not view.owner_active and view.status in {"completed", "failed", "cancelled"} for view in views))
    return JobControlBatch(run_id=service.run.run_id, action=action, observed_job_ids=identifiers,
        jobs=tuple(views), failures=tuple(failures), dispatch_barrier_observed=barrier,
        unverified_catalog_rows=invalid_rows, catalog_unchanged=unchanged, all_observed_jobs_terminal=terminal)
