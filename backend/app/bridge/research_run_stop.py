"""Persist a dispatch barrier before stopping and reconciling SQL-owned work.

Only the existing RunGraph/StateJournal owns lifecycle. Unknown work remains
cancelling; a free local coroutine alone never proves that a run has stopped.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from filelock import FileLock, Timeout
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.bridge.owned_run_tasks import OwnedRunTasks
from app.bridge.research_job_control import control_owned_jobs, load_job_control_policy
from app.bridge.research_job_service import ResearchJobService, load_research_job_policy
from app.bridge.research_run_service import validate_saved_run_journal_schema
from app.harness.runtime.project_scope import safe_scope_path
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger, _BUDGET_EXTENSION_VERSION
from app.harness.runtime.research_contract import ResearchBudget
from app.harness.runtime.state_journal import StateJournal
from app.harness.runtime.state_machine import NodeState
from app.settings import repo_root
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle


class ResearchStopPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sqlite_timeout_seconds: float = Field(gt=0, le=1, strict=True)
    poll_interval_seconds: float = Field(gt=0, le=1, strict=True)
    reconciliation_seconds: float = Field(gt=0, le=30, strict=True)


def load_research_stop_policy() -> ResearchStopPolicy:
    data = yaml.safe_load((repo_root() / "configs/research_jobs.yaml").read_text())
    return ResearchStopPolicy.model_validate(data["research_run_stop"])


def _authority(run: RunHandle) -> StateJournal:
    for name in ("run_state.authority.json", "run_state.sqlite3", "run_meta.json"):
        safe_scope_path(run.root, name, must_exist=True)
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    if journal is None:
        raise ValueError("Research stop requires existing SQL authority")
    return journal


def _payload(run: RunHandle, journal: StateJournal, connection: sqlite3.Connection) -> dict[str, Any]:
    validate_saved_run_journal_schema(connection)
    payload = journal._read(connection)
    snapshot = RunStateStore(run)._snapshot(payload)
    extra = snapshot.request.get("extra")
    if (not isinstance(extra, dict) or not isinstance(extra.get("research_task_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", extra["research_task_sha256"]) is None):
        raise ValueError("Research stop requires the SQL contract identity")
    return payload


def request_stop_barrier(run: RunHandle, *, policy: ResearchStopPolicy, reason: str) -> dict[str, Any]:
    """A bounded transaction forbids new ledger/model/tool/job admissions first."""
    if reason not in {"user_request", "server_shutdown"}:
        raise ValueError("Unsupported research stop reason")
    journal = _authority(run)
    with journal.transaction(timeout=policy.sqlite_timeout_seconds) as connection:
        payload = _payload(run, journal, connection)
        if payload["status"] == "completed":
            raise ValueError("Completed research is not an active stop target")
        termination = payload.get("termination")
        if isinstance(termination, dict) and termination.get("scope") == "research_contract":
            if payload["status"] not in {"cancelling", "cancelled"}:
                raise ValueError("Research stop lifecycle is inconsistent")
            return payload
        payload = {**payload, "status": "cancelling", "updated_at": datetime.now(timezone.utc).isoformat(),
            "termination": {"type": "cancelled", "scope": "research_contract", "reason": reason,
                "requested_at": datetime.now(timezone.utc).isoformat(), "cleanup_complete": False,
                "automatic_resume": False}}
        committed = journal.commit_in_transaction(connection, payload, expected_revision=payload["revision"])
    RunStateStore(run)._projection(committed)
    return committed


def _control_ledger(run: RunHandle, journal: StateJournal, connection: sqlite3.Connection, task_hash: str) -> ResearchBudgetLedger | None:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "research_budget" not in tables:
        if any(name.startswith("research_") for name in tables):
            raise ValueError("Research extensions exist without their budget authority")
        return None
    row = connection.execute("SELECT version,run_id,journal_id,task_sha256,policy,policy_sha256 FROM research_budget WHERE id=1").fetchone()
    if row is None or row[:4] != (_BUDGET_EXTENSION_VERSION, run.run_id, journal.journal_id, task_hash):
        raise ValueError("Research stop budget identity differs")
    policy = json.loads(row[4])
    ledger = ResearchBudgetLedger(journal, task_sha256=task_hash,
        budget=ResearchBudget.model_validate(policy["budget"]), price_reference_sha256=policy.get("price_reference_sha256"))
    if ledger.policy != policy or ledger.policy_sha256 != row[5]:
        raise ValueError("Research stop budget policy differs")
    # No initialization or frozen-file requirement here: stopping an owned job
    # must still be attempted when an external contract file was lost.
    return ledger


def reconcile_research_stop(run: RunHandle, *, policy: ResearchStopPolicy, owned_task_active: bool) -> dict[str, Any]:
    """One actual control pass. Repeating it never submits work or refunds usage."""
    journal = _authority(run)
    with journal.connection(timeout=policy.sqlite_timeout_seconds) as connection:
        connection.execute("BEGIN")
        payload = _payload(run, journal, connection)
        if payload["status"] not in {"cancelling", "cancelled"} or payload.get("termination", {}).get("scope") != "research_contract":
            raise ValueError("Research stop barrier has not committed")
        ledger = _control_ledger(run, journal, connection, payload["request"]["extra"]["research_task_sha256"])
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    problems: list[str] = []
    batch = None
    if {"research_job_extension", "research_jobs"} <= tables and ledger is not None:
        batch = control_owned_jobs(ResearchJobService(run, ledger, load_research_job_policy()),
                                   action="stop", policy=load_job_control_policy())
        if not batch.all_observed_jobs_terminal or not batch.dispatch_barrier_observed:
            problems.append("owned_jobs_unconfirmed")
    elif tables & {"research_job_extension", "research_jobs"}:
        problems.append("job_catalog_incomplete")
    elif (run.root / "execution/local_jobs").exists():
        problems.append("jobs_without_sql_catalog")
    if owned_task_active:
        problems.append("owned_coroutine_active")
    with ExitStack() as locks, journal.transaction(timeout=policy.sqlite_timeout_seconds) as connection:
        current = _payload(run, journal, connection)
        if current["status"] not in {"cancelling", "cancelled"} or current.get("termination", {}).get("scope") != "research_contract":
            raise ValueError("Research stop barrier changed during reconciliation")
        # New stage claims cannot cross the committed SQL barrier. Holding every
        # known lease through terminal COMMIT closes the late-owner race.
        if "research_stage_dispatches" in tables:
            for invocation, in connection.execute("SELECT invocation_id FROM research_stage_dispatches"):
                if not isinstance(invocation, str) or re.fullmatch(r"[A-Za-z0-9-]+", invocation) is None:
                    problems.append("stage_identity_unverified")
                    continue
                try:
                    path = safe_scope_path(run.root, f"execution/stage_owners/{invocation}.lock", must_exist=True)
                    locks.enter_context(FileLock(path, timeout=0, thread_local=False))
                except (OSError, ValueError, Timeout):
                    problems.append("stage_owner_unconfirmed")
        if ledger is not None:
            try:
                snapshot = ledger.in_transaction(connection).snapshot()
                active = connection.execute("SELECT COUNT(*) FROM research_activity WHERE ended_us IS NULL").fetchone()[0]
                if active or snapshot.active_jobs or snapshot.active_readers or snapshot.active_gpus:
                    problems.append("accounted_activity_unconfirmed")
            except (ValueError, KeyError):
                problems.append("budget_evidence_unavailable")
        if batch is not None:
            now = tuple(row[0] for row in connection.execute("SELECT job_id FROM research_jobs ORDER BY job_id"))
            if now != batch.observed_job_ids:
                problems.append("job_catalog_changed")
        if not problems and current["status"] != "cancelled":
            graph = RunStateStore(run)._snapshot(current).graph
            for key, state in graph.all_states().items():
                if state == NodeState.RUNNING:
                    graph.transition(key, NodeState.FAILED)
                    graph.nodes[key].metadata["termination"] = {"type": "cancelled", "reason": current["termination"]["reason"]}
            failed = sorted(key for key, state in graph.all_states().items() if state == NodeState.FAILED)
            current = {**current, "status": "cancelled", "graph": graph.to_dict(), "failed_nodes": failed,
                "failure_summary": f"{len(failed)} run node(s) failed" if failed else None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "termination": {**current["termination"], "cleanup_complete": True,
                                "finished_at": datetime.now(timezone.utc).isoformat()}}
            current = journal.commit_in_transaction(connection, current, expected_revision=current["revision"])
    if not problems:
        RunStateStore(run)._projection(current)
    return {"ok": not problems, "run_id": run.run_id, "status": "stopped" if not problems else "stop_incomplete",
        "run_stop_confirmed": not problems, "state_persisted": True, "termination": current["termination"],
        "unconfirmed": sorted(set(problems)), "jobs": batch.model_dump(mode="json") if batch else None}


def _stop_jobs_without_barrier(run: RunHandle) -> dict[str, Any] | None:
    policy = load_research_stop_policy()
    journal = _authority(run)
    with journal.connection(timeout=policy.sqlite_timeout_seconds) as connection:
        connection.execute("BEGIN")
        payload = _payload(run, journal, connection)
        ledger = _control_ledger(run, journal, connection, payload["request"]["extra"]["research_task_sha256"])
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if ledger is None or not {"research_job_extension", "research_jobs"} <= tables:
        return None
    batch = control_owned_jobs(ResearchJobService(run, ledger, load_research_job_policy()),
                               action="stop", policy=load_job_control_policy())
    return batch.model_dump(mode="json")


def _notify_state(callback: Callable[[dict[str, Any]], None] | None, state: dict[str, Any]) -> None:
    if callback is not None:
        try:
            callback(state)
        except Exception:
            # A stale UI cache cannot prevent actual cancellation or change the
            # authoritative stop result. Never copy arbitrary exception secrets.
            logger.warning("Research stop cache refresh failed")


class ResearchRunStops:
    """Own bounded reconciliation coroutines; they never own another run graph."""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, asyncio.Task[dict[str, Any]]] = {}

    async def request(self, run: RunHandle, owners: OwnedRunTasks, *, reason: str = "user_request",
                      on_state: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        pending = self.requests.get(run.run_id)
        if pending is None or pending.done():
            pending = asyncio.create_task(self._request(run, owners, reason=reason, on_state=on_state),
                                          name=f"research_stop_request:{run.run_id}")
            self.requests[run.run_id] = pending
        # A disconnected HTTP waiter must not abandon a barrier that commits in
        # its worker thread, leaving the actual job-stop pass unscheduled.
        return await asyncio.shield(pending)

    async def _request(self, run: RunHandle, owners: OwnedRunTasks, *, reason: str,
                       on_state: Callable[[dict[str, Any]], None] | None) -> dict[str, Any]:
        try:
            policy = load_research_stop_policy()
            state = await asyncio.to_thread(request_stop_barrier, run, policy=policy, reason=reason)
        except Exception:
            # Durable failure must not strand an already owned local coroutine.
            owners.cancel_once(run.run_id)
            error = {"ok": False, "run_id": run.run_id, "status": "stop_state_error", "state_persisted": False}
            self.results[run.run_id] = error
            existing = self.tasks.get(run.run_id)
            if existing is None or existing.done():
                async def stop_verified_jobs() -> None:
                    # Failure to persist a barrier must not strand jobs whose
                    # ownership can still be verified. This cannot certify run
                    # quiescence: another dispatcher may remain active.
                    try:
                        batch = await asyncio.to_thread(_stop_jobs_without_barrier, run)
                        self.results[run.run_id] = {**error, "jobs": batch,
                                                   "run_stop_confirmed": False}
                    except Exception:
                        self.results[run.run_id] = {**error, "run_stop_confirmed": False,
                                                   "unconfirmed": ["control_evidence_unavailable"]}
                self.tasks[run.run_id] = asyncio.create_task(stop_verified_jobs(), name=f"research_stop_failed_state:{run.run_id}")
            return error
        owners.cancel_once(run.run_id)
        _notify_state(on_state, state)
        existing = self.tasks.get(run.run_id)
        if existing is None or existing.done():
            self.results[run.run_id] = {"ok": True, "run_id": run.run_id, "status": "stop_requested",
                "state_persisted": True, "run_stop_confirmed": False, "termination": state["termination"]}
            async def reconcile() -> None:
                deadline = asyncio.get_running_loop().time() + policy.reconciliation_seconds
                while True:
                    try:
                        result = await asyncio.to_thread(reconcile_research_stop, run, policy=policy,
                                                        owned_task_active=owners.active(run.run_id) is not None)
                        if result["ok"] and on_state is not None:
                            _notify_state(on_state, await asyncio.to_thread(_authority(run).read))
                    except Exception:
                        result = {"ok": False, "run_id": run.run_id, "status": "stop_incomplete",
                                  "run_stop_confirmed": False, "unconfirmed": ["control_evidence_unavailable"]}
                    self.results[run.run_id] = result
                    if result["ok"] or asyncio.get_running_loop().time() >= deadline:
                        return
                    await asyncio.sleep(policy.poll_interval_seconds)
            self.tasks[run.run_id] = asyncio.create_task(reconcile(), name=f"research_stop:{run.run_id}")
        return {"ok": True, "run_id": run.run_id, "status": "stop_requested", "state_persisted": True,
                "run_stop_confirmed": False, "termination": state["termination"]}

    async def wait(self, *, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            active: set[asyncio.Task[Any]] = {task for task in (*self.requests.values(), *self.tasks.values()) if not task.done()}
            remaining = deadline - asyncio.get_running_loop().time()
            if not active or remaining <= 0:
                return
            await asyncio.wait(active, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
