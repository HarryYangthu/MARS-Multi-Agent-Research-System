"""Contract-bound trusted CPU jobs; SQLite admission precedes durable dispatch.

This service does not start an Agent, admit a complete research workflow, or
grant data/GPU/network access. Commands remain trusted, not OS-sandboxed.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.bridge.research_run_service import load_run_research_contract
from app.execution.local.runner import LocalJobSpec, LocalJobStatus, LocalRunner, _deadline, _spec_payload
from app.harness.discovery.snapshots import SnapshotPolicy, create_snapshot, verify_snapshot
from app.harness.persistence import path_lock
from app.harness.runtime.project_scope import ProjectScope, current_project_scope, safe_scope_path, verify_candidate_scope
from app.harness.runtime.research_budget_ledger import BudgetAmounts, BudgetReservation, BudgetSettlement, ResearchBudgetLedger
from app.harness.runtime.research_contract import relative_scope
from app.harness.runtime.research_execution_scope import bound_research_execution
from app.settings import repo_root
from app.storage.run_store import RunHandle

_TABLES = frozenset({"research_job_extension", "research_jobs"})
_UNKNOWN_REASON = "contract job requires independent execution reconciliation"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ResearchJobPolicy(_Record):
    max_output_bytes: int = Field(gt=0, strict=True)
    snapshot_max_file_bytes: int = Field(gt=0, strict=True)
    snapshot_max_total_bytes: int = Field(gt=0, strict=True)
    snapshot_max_files: int = Field(gt=0, le=100_000, strict=True)


def load_research_job_policy(path: Path | None = None) -> ResearchJobPolicy:
    raw = yaml.safe_load((path or repo_root() / "configs/research_jobs.yaml").read_text(encoding="utf-8"))
    return ResearchJobPolicy.model_validate(raw["research_jobs"])


class ResearchJobRequest(_Record):
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    attempt_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    experiment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    command_name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    config: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = Field(default=None, strict=True)
    steps: int = Field(gt=0, strict=True)


class ResearchJobView(_Record):
    run_id: str
    job_id: str
    attempt_id: str
    experiment_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "unknown"]
    owner_active: bool
    stop_requested: bool
    budget_state: Literal["reserved", "settled", "unknown", "retained"]
    receipt: dict[str, Any] | None = None


class ResearchJobRejected(ValueError):
    """A contract, environment or budget refused the job before dispatch."""


class _Binding(_Record):
    task_sha256: str
    request: ResearchJobRequest
    spec: LocalJobSpec
    snapshot_path: str
    snapshot_id: str
    candidate_id: str
    candidate_fingerprint: str
    environment: dict[str, str]
    reservation_id: str
    reserved_at_us: int


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Runner timing must contain finite numbers")
    return float(value)


class ResearchJobService:
    def __init__(self, run: RunHandle, ledger: ResearchBudgetLedger, policy: ResearchJobPolicy) -> None:
        if (run.root.is_symlink() or run.root.resolve() != ledger.journal.path.parent.resolve()
                or run.run_id != ledger.journal.run_id):
            raise ValueError("Job service requires the exact run budget authority")
        self.run, self.ledger, self.policy = run, ledger, policy
        self.root = run.root.resolve(strict=True)
        self.runner = LocalRunner(self.root)

    def _directory(self, relative: str) -> Path:
        path = self.root
        for part in Path(relative_scope(relative)).parts:
            path /= part
            if path.is_symlink() or path.exists() and not path.is_dir():
                raise ValueError("Job service storage cannot be redirected")
            path.mkdir(exist_ok=True)
        return path

    def _extension(self, connection: sqlite3.Connection) -> None:
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not _TABLES <= present:
            raise ValueError("Contract job extension requires explicit initialization")
        row = connection.execute("SELECT version,run_id,task_sha256 FROM research_job_extension WHERE id=1").fetchone()
        if row != (1, self.run.run_id, self.ledger.task_sha256):
            raise ValueError("Contract job extension identity or version is invalid")

    def initialize(self) -> None:
        """Explicit table installation; status never installs or migrates tables."""
        with self.ledger.transaction() as budget:
            present = {row[0] for row in budget.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if present & _TABLES:
                self._extension(budget.connection)
                return
            budget.connection.execute("CREATE TABLE research_job_extension (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, run_id TEXT NOT NULL, task_sha256 TEXT NOT NULL)")
            budget.connection.execute("INSERT INTO research_job_extension VALUES (1,1,?,?)", (self.run.run_id, self.ledger.task_sha256))
            budget.connection.execute("CREATE TABLE research_jobs (job_id TEXT PRIMARY KEY, binding TEXT NOT NULL, binding_sha256 TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('reserved','submitted','unknown','settled')), receipt_sha256 TEXT)")
        self._directory("execution/research_job_controls")

    @contextmanager
    def _lock(self, job_id: str) -> Iterator[None]:
        path = safe_scope_path(self.root, f"execution/research_job_controls/{job_id}.lock")
        with path_lock(path):
            yield

    def _read(self, connection: sqlite3.Connection, job_id: str) -> tuple[_Binding, str, str | None] | None:
        self._extension(connection)
        row = connection.execute("SELECT binding,binding_sha256,phase,receipt_sha256 FROM research_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        binding = _Binding.model_validate_json(row[0])
        if (row[1] != _digest(binding.model_dump(mode="json")) or binding.task_sha256 != self.ledger.task_sha256
                or binding.spec.run_id != self.run.run_id or binding.spec.project != self.run.project
                or binding.request.job_id != job_id or binding.spec.job_id != job_id
                or binding.spec.attempt_id != binding.request.attempt_id
                or binding.spec.experiment_id != binding.request.experiment_id
                or row[2] not in {"reserved", "submitted", "unknown", "settled"}
                or (row[2] == "settled") != (row[3] is not None)):
            raise ValueError("Contract job binding is invalid")
        return binding, row[2], row[3]

    def _existing(self, job_id: str) -> tuple[_Binding, str, str | None] | None:
        with self.ledger.transaction() as budget:
            return self._read(budget.connection, job_id)

    def _control_record(self, connection: sqlite3.Connection, job_id: str) -> tuple[_Binding, str, str | None] | None:
        """Stop authority is the existing SQL owner, never a new file admission."""
        self.ledger.journal.validate_transaction(connection)
        payload = self.ledger.journal._read(connection)
        request = payload.get("request")
        extra = request.get("extra") if isinstance(request, dict) else None
        if (payload.get("run_id") != self.run.run_id or payload.get("project") != self.run.project
                or not isinstance(extra, dict) or extra.get("research_task_sha256") != self.ledger.task_sha256):
            raise ValueError("Job control does not match the authoritative run identity")
        return self._read(connection, job_id)

    def _authorize(self, scope: ProjectScope) -> None:
        execution = bound_research_execution()
        if (execution is None or execution.stage != "execution" or execution.ledger is not self.ledger
                or current_project_scope(self.run.project, self.run.run_id) != scope
                or scope.task_sha256 != self.ledger.task_sha256 or scope.run_root != self.root):
            raise ResearchJobRejected("Job dispatch requires matching host-bound execution and project scopes")

    def _prepare(self, request: ResearchJobRequest, scope: ProjectScope) -> tuple[Path, str, str, dict[str, str], tuple[str, ...]]:
        self._authorize(scope)
        frozen = load_run_research_contract(self.run)
        if frozen is None or frozen.task_sha256 != self.ledger.task_sha256:
            raise ResearchJobRejected("Frozen job contract is unavailable")
        project = frozen.task.project
        if project.execution.kind != "local" or project.execution.device != "cpu" or project.paths.data:
            raise ResearchJobRejected("Only local CPU contracts without external data declarations are admitted")
        command = next((item for item in project.commands if item.name == request.command_name), None)
        if command is None:
            raise ResearchJobRejected("Command is absent from the frozen contract")
        executable = Path(command.executable).resolve(strict=True)
        if (executable != Path(sys.executable).resolve(strict=True) or not executable.is_file()
                or not os.access(executable, os.X_OK) or len(command.arguments) != 1):
            raise ResearchJobRejected("This adapter requires the available host Python and one declared relative script")
        script = relative_scope(command.arguments[0])
        if Path(script).suffix != ".py":
            raise ResearchJobRejected("Only a declared Python file entrypoint is admitted")
        entry = (Path(command.cwd) / script).as_posix() if command.cwd != "." else script
        if entry not in command.entrypoint_files:
            raise ResearchJobRejected("Python entrypoint is not a declared frozen command dependency")
        changes = verify_candidate_scope(scope)
        scope.resolve_file(entry, must_exist=True)
        files = tuple(sorted(path.relative_to(scope.candidate_root).as_posix()
            for path in scope.candidate_root.rglob("*") if path.is_file() and path.name != ".mars_candidate_workspace.json"))
        candidate_fingerprint = _digest({"snapshot": scope.snapshot_id,
            "changes": [change.__dict__ for change in changes]})
        snapshot = create_snapshot(source_root=scope.candidate_root,
            cache_root=self._directory("execution/research_job_snapshots"), project=self.run.project,
            source_ref=candidate_fingerprint,
            policy=SnapshotPolicy(allowed_paths=files, max_file_bytes=self.policy.snapshot_max_file_bytes,
                max_total_bytes=self.policy.snapshot_max_total_bytes, max_files=self.policy.snapshot_max_files))
        if verify_candidate_scope(scope) != changes:
            raise ResearchJobRejected("Candidate changed while preparing job snapshot")
        if any(_file_hash(scope.resolve_file(item.path, must_exist=True)) != item.sha256 for item in snapshot.manifest.files):
            raise ResearchJobRejected("Execution snapshot differs from the admitted candidate")
        cwd = snapshot.root if command.cwd == "." else snapshot.root / command.cwd
        if not cwd.is_dir() or cwd.is_symlink():
            raise ResearchJobRejected("Frozen command working directory is unavailable")
        environment = {"python_executable": str(executable), "executable_sha256": _file_hash(executable),
            "python_version": sys.version, "platform": sys.platform, "device": "cpu", "os_isolated": "false"}
        return snapshot.root, command.cwd, candidate_fingerprint, environment, tuple(metric.name for metric in project.metrics)

    def submit(self, request: ResearchJobRequest, scope: ProjectScope) -> ResearchJobView:
        request = ResearchJobRequest.model_validate(request.model_dump(mode="json"))
        _canonical(request.model_dump(mode="json"))
        self._authorize(scope)
        # The same per-job owner lock spans COMMIT and spawn. Concurrent status
        # cannot declare the commit/spawn gap unknown while its owner is alive.
        with self._lock(request.job_id):
            existing = self._existing(request.job_id)
            if existing is not None:
                binding = existing[0]
                if binding.request != request or binding.candidate_id != scope.candidate_root.name:
                    raise ResearchJobRejected("Job identity already has different inputs")
                return self._status_locked(request.job_id)
            snapshot, command_cwd, fingerprint, environment, metrics = self._prepare(request, scope)
            frozen = load_run_research_contract(self.run)
            assert frozen is not None
            command = next(item for item in frozen.task.project.commands if item.name == request.command_name)
            rejection: str | None = None
            with self.ledger.transaction() as budget:
                self._extension(budget.connection)
                now_us = time.time_ns() // 1000
                remaining = budget.snapshot(now_us=now_us)
                duration_us = self.ledger.budget.training_job_seconds * 1_000_000
                operation = _digest({"task": self.ledger.task_sha256, "candidate": fingerprint,
                    "command": command.model_dump(mode="json"), "environment": environment,
                    "config": request.config, "seed": request.seed, "steps": request.steps})
                reservation = BudgetReservation(reservation_id="job:" + request.job_id,
                    operation_id="job-" + operation.removeprefix("sha256:"), operation_fingerprint=operation,
                    kind="job", amounts=BudgetAmounts(training_process_us=duration_us), job_duration_us=duration_us, gpus=0)
                admission = budget.reserve(reservation, now_us=now_us)
                if not admission.admitted or admission.replay:
                    rejection = admission.reason or "existing_operation_requires_reconciliation"
                else:
                    cwd = snapshot if command_cwd == "." else snapshot / command_cwd
                    spec = LocalJobSpec(run_id=self.run.run_id, attempt_id=request.attempt_id, job_id=request.job_id,
                        experiment_id=request.experiment_id, project=self.run.project,
                        argv=(environment["python_executable"], command.arguments[0]), cwd=str(cwd),
                        timeout_seconds=float(self.ledger.budget.training_job_seconds),
                        not_after_epoch_seconds=(now_us + min(duration_us, remaining.activity_remaining_us)) / 1_000_000,
                        max_output_bytes=self.policy.max_output_bytes, required_metrics=metrics,
                        config=request.config, seed=request.seed, steps=request.steps)
                    binding = _Binding(task_sha256=self.ledger.task_sha256, request=request, spec=spec,
                        snapshot_path=snapshot.relative_to(self.root).as_posix(), snapshot_id=snapshot.name,
                        candidate_id=scope.candidate_root.name, candidate_fingerprint=fingerprint,
                        environment=environment, reservation_id=reservation.reservation_id, reserved_at_us=now_us)
                    payload = binding.model_dump(mode="json")
                    budget.connection.execute("INSERT INTO research_jobs VALUES (?,?,?,'reserved',NULL)",
                        (request.job_id, _canonical(payload), _digest(payload)))
            if rejection is not None:
                raise ResearchJobRejected("Job budget refused: " + rejection)
            try:
                self._verify_snapshot(binding, check_environment=True)
                self.runner.submit(binding.spec)
            except Exception:
                self._unknown(binding)
                raise
            with self.ledger.transaction() as budget:
                budget.connection.execute("UPDATE research_jobs SET phase='submitted' WHERE job_id=?", (request.job_id,))
            return self._status_locked(request.job_id)

    def _verify_snapshot(self, binding: _Binding, *, check_environment: bool = False) -> None:
        path = self.root / relative_scope(binding.snapshot_path)
        probe = safe_scope_path(self.root, binding.snapshot_path + "/snapshot_manifest.json", must_exist=True)
        snapshot = verify_snapshot(probe.parent)
        if (snapshot.root != path or snapshot.manifest.snapshot_id != binding.snapshot_id
                or snapshot.manifest.source_ref != binding.candidate_fingerprint
                or snapshot.manifest.project != self.run.project
                or check_environment and _file_hash(Path(binding.environment["python_executable"])) != binding.environment["executable_sha256"]):
            raise ValueError("Execution snapshot or environment fingerprint changed")

    def _unknown(self, binding: _Binding) -> None:
        with self.ledger.transaction() as budget:
            current = self._read(budget.connection, binding.request.job_id)
            if current is None or current[1] == "settled":
                raise ValueError("Cannot replace a terminal or missing job with an unknown result")
            budget.mark_unknown(binding.reservation_id, reason=_UNKNOWN_REASON)
            budget.connection.execute("UPDATE research_jobs SET phase='unknown' WHERE job_id=?", (binding.request.job_id,))

    def _view(self, binding: _Binding, status: LocalJobStatus | None, budget_state: str) -> ResearchJobView:
        return ResearchJobView.model_validate({"run_id": self.run.run_id, "job_id": binding.request.job_id,
            "attempt_id": binding.request.attempt_id, "experiment_id": binding.request.experiment_id,
            "status": status.status if status is not None else "unknown",
            "owner_active": status.owner_active if status else False,
            "stop_requested": status.stop_requested if status else False,
            "budget_state": budget_state, "receipt": status.receipt if status else None})

    def _status_locked(self, job_id: str) -> ResearchJobView:
        current = self._existing(job_id)
        if current is None:
            raise ValueError("Job is not owned by this contract run")
        binding, phase, receipt_hash = current
        status: LocalJobStatus | None = None
        try:
            status = self.runner.status(job_id)
            submission = safe_scope_path(self.root, f"execution/local_jobs/{job_id}/submission.json", must_exist=True)
            raw = json.loads(submission.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("spec_sha256") != _digest(_spec_payload(binding.spec)):
                raise ValueError("Runner submission does not match the authoritative job")
            if status.status == "unknown":
                raise ValueError("Runner lost its execution owner")
            if status.receipt is None:
                return self._view(binding, status, "unknown" if phase == "unknown" else "reserved")
            self._verify_snapshot(binding)
            receipt_path = safe_scope_path(self.root, f"execution/local_jobs/{job_id}/execution_receipt.json", must_exist=True)
            actual_hash = _file_hash(receipt_path)
            if receipt_hash is not None and actual_hash != receipt_hash:
                raise ValueError("Settled job receipt changed")
            receipt = status.receipt
            duration, started, finished = (_finite_number(receipt.get(name)) for name in ("duration_seconds", "started_at", "finished_at"))
            if (duration < 0 or started < binding.reserved_at_us / 1_000_000 or finished < started
                    or finished > time.time() or receipt.get("argv") != list(binding.spec.argv)
                    or receipt.get("device") != "cpu" or receipt.get("os_isolated") is not False
                    or receipt.get("deadline_at") != _deadline(binding.spec, raw["submitted_at"])):
                raise ValueError("Runner receipt timing or environment is invalid")
            expected_files = [{"path": binding.spec.argv[0], "sha256": binding.environment["executable_sha256"]},
                {"path": str(Path(binding.spec.cwd) / binding.spec.argv[1]),
                 "sha256": _file_hash(Path(binding.spec.cwd) / binding.spec.argv[1])}]
            if receipt.get("command_files") != expected_files:
                raise ValueError("Executed command files differ from the frozen environment")
            ended_us = math.ceil(finished * 1_000_000)
            amounts = BudgetAmounts(training_process_us=math.ceil(duration * 1_000_000))
        except (OSError, ValueError, TypeError, KeyError):
            if phase == "settled":
                raise ValueError("Persisted terminal job evidence is unavailable or invalid") from None
            self._unknown(binding)
            if status is not None:
                status = status.model_copy(update={"status": "unknown", "receipt": None})
            return self._view(binding, status, "unknown")
        assert status is not None
        evidence = (receipt_path.relative_to(self.root).as_posix(),)
        with self.ledger.transaction() as budget:
            state = budget.connection.execute("SELECT state FROM research_reservations WHERE reservation_id=?", (binding.reservation_id,)).fetchone()
            if state is None:
                raise ValueError("Job budget reservation is missing")
            if receipt_hash is not None:
                if state[0] not in {"settled", "retained"}:
                    raise ValueError("Terminal job and budget lifecycle disagree")
                # A verified repeat read cannot move the clock, replace its
                # settlement, or change the immutable execution receipt.
                return self._view(binding, status, state[0])
            if phase == "unknown" and state[0] == "reserved":
                # Stop may have found an unavailable frozen file. Its SQL job
                # uncertainty persists even before the full ledger can reopen.
                budget.mark_unknown(binding.reservation_id, reason=_UNKNOWN_REASON)
                state = ("unknown",)
            if state[0] in {"unknown", "retained"}:
                if state[0] == "unknown":
                    budget.mark_unknown(binding.reservation_id, reason=_UNKNOWN_REASON, observed_lower_bound=amounts)
                budget.reconcile_stopped(binding.reservation_id, actor="verified-local-runner", evidence_refs=evidence,
                    activity_ended_us=ended_us)
                budget_state = "retained"
            else:
                success = status.status == "completed"
                budget.settle(binding.reservation_id, BudgetSettlement(actual=amounts,
                    outcome="success" if success else "failure", evidence_refs=evidence,
                    evidence_fingerprint=actual_hash,
                    error_fingerprint=None if success else _digest({"status": status.status, "error": receipt.get("error")})),
                    activity_ended_us=ended_us)
                budget_state = "settled"
            budget.connection.execute("UPDATE research_jobs SET phase='settled',receipt_sha256=? WHERE job_id=?", (actual_hash, job_id))
        return self._view(binding, status, budget_state)

    def status(self, job_id: str) -> ResearchJobView:
        if self._existing(job_id) is None:
            raise ValueError("Job is not owned by this contract run")
        with self._lock(job_id):
            return self._status_locked(job_id)

    def stop(self, job_id: str) -> ResearchJobView:
        with self.ledger.journal.transaction() as connection:
            existing = self._control_record(connection, job_id)
        if existing is None:
            raise ValueError("Job is not owned by this contract run")
        with self._lock(job_id):
            # Stop uses the saved SQL/runner identity even if a damaged source
            # snapshot prevents result acceptance. It never signals a saved PID.
            binding = existing[0]
            submission = safe_scope_path(self.root, f"execution/local_jobs/{job_id}/submission.json", must_exist=True)
            raw = json.loads(submission.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("spec_sha256") != _digest(_spec_payload(binding.spec)):
                raise ValueError("Runner submission does not match the authoritative job")
            current = self.runner.status(job_id)
            if current.status in {"queued", "running"}:
                current = self.runner.stop(job_id)
            try:
                return self._status_locked(job_id)
            except (OSError, ValueError):
                # A missing external declaration must not prevent an owned
                # process being stopped. Never refund or mark it settled here.
                with self.ledger.journal.transaction() as connection:
                    row = self._control_record(connection, job_id)
                    if row is None:
                        raise ValueError("Owned job disappeared while stopping")
                    if row[1] != "settled":
                        connection.execute("UPDATE research_jobs SET phase='unknown' WHERE job_id=?", (job_id,))
                return self._view(binding, current.model_copy(update={"receipt": None}), "unknown")

    def reconcile_all(self) -> tuple[ResearchJobView, ...]:
        with self.ledger.transaction() as budget:
            self._extension(budget.connection)
            identifiers = [row[0] for row in budget.connection.execute("SELECT job_id FROM research_jobs ORDER BY job_id")]
        return tuple(self.status(identifier) for identifier in identifiers)
