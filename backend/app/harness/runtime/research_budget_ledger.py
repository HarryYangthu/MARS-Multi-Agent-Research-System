"""Contract budget extension in the existing StateJournal SQLite authority.

Persistence primitives only. No model/tool execution, scheduler, automatic
schema upgrade, inferred completion, or retry is implemented here.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import sqlite3
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.harness.runtime.research_contract import ResearchBudget, ResearchTaskContract
from app.harness.runtime.state_journal import RunStateIntegrityError, StateJournal

Count = Annotated[int, Field(strict=True, ge=0)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")]
_MICRO = 1_000_000
_BUDGET_EXTENSION_VERSION = 2
_TABLES = frozenset({"research_budget", "research_operations", "research_reservations", "research_activity", "research_leases"})


class BudgetNotInitialized(ValueError):
    """The caller must explicitly initialize the budget extension."""


class BudgetConflictError(ValueError):
    """A frozen policy or operation identity differs from its persisted value."""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class _PolicyIdentity(_Record):
    # Matches FrozenResearchTask and authoritative request.extra exactly.
    task_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    price_reference_sha256: Digest | None = None


class BudgetAmounts(_Record):
    """Cumulative quantities; durations and CNY use integer micro units."""
    search_candidates: Count = 0
    deep_read_papers: Count = 0
    proposal_candidates: Count = 0
    implemented_candidates: Count = 0
    debate_rounds: Count = 0
    automatic_iterations: Count = 0
    model_requests: Count = 0
    tool_executions: Count = 0
    input_tokens: Count = 0
    billed_output_tokens: Count = 0
    training_process_us: Count = 0
    gpu_us: Count = 0
    model_cost_micro_cny: Count | None = None

    def units(self) -> dict[str, int]:
        return {key: int(value) for key, value in self.model_dump().items() if value is not None}


def _validate_amount_family(kind: str, amounts: BudgetAmounts, *, require_attempt: bool) -> None:
    if kind != "model" and (amounts.model_requests or amounts.input_tokens or amounts.billed_output_tokens
                            or amounts.model_cost_micro_cny not in (None, 0)):
        raise ValueError("model quantities require a model operation")
    if kind != "job" and (amounts.training_process_us or amounts.gpu_us):
        raise ValueError("job quantities require a job operation")
    if require_attempt and kind == "model" and amounts.model_requests < 1:
        raise ValueError("model operation must account for at least one request")
    if require_attempt and kind == "tool" and amounts.tool_executions < 1:
        raise ValueError("tool operation must account for at least one execution")


def _maximum_amounts(first: BudgetAmounts, second: BudgetAmounts) -> BudgetAmounts:
    values: dict[str, int | None] = dict(first.model_dump())
    for key, value in second.units().items():
        values[key] = max(int(values[key] or 0), value)
    return BudgetAmounts.model_validate(values)


class BudgetReservation(_Record):
    reservation_id: Identifier
    operation_id: Identifier
    operation_fingerprint: Digest
    attempt_index: Count = 0
    kind: Literal["model", "tool", "reader", "job", "search", "proposal", "implementation", "debate", "iteration", "activity"]
    amounts: BudgetAmounts
    request_input_tokens: Count = 0
    request_output_tokens: Count = 0
    output_class: Literal["ordinary", "coding"] = "ordinary"
    job_duration_us: Count = 0
    gpus: Count = 0

    @model_validator(mode="after")
    def typed_quantities(self) -> BudgetReservation:
        _validate_amount_family(self.kind, self.amounts, require_attempt=True)
        if self.kind == "model":
            if (self.amounts.model_requests < 1 or self.request_input_tokens < 1 or self.request_output_tokens < 1
                    or self.amounts.input_tokens < self.request_input_tokens * self.amounts.model_requests
                    or self.amounts.billed_output_tokens < self.request_output_tokens * self.amounts.model_requests):
                raise ValueError("model reservation must cover all request attempts and token bounds")
        elif (self.request_input_tokens or self.request_output_tokens or self.output_class != "ordinary"
              or self.amounts.model_requests or self.amounts.input_tokens or self.amounts.billed_output_tokens
              or self.amounts.model_cost_micro_cny not in (None, 0)):
            raise ValueError("per-request model bounds require a model operation")
        if self.kind == "job":
            if (self.job_duration_us < 1 or self.amounts.training_process_us < self.job_duration_us
                    or self.amounts.gpu_us < self.job_duration_us * self.gpus):
                raise ValueError("job reservation must cover its complete duration and GPU allocation")
        elif self.job_duration_us or self.gpus or self.amounts.training_process_us or self.amounts.gpu_us:
            raise ValueError("job bounds require a job operation")
        return self


class BudgetSettlement(_Record):
    actual: BudgetAmounts
    outcome: Literal["success", "failure"]
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    error_fingerprint: Digest | None = None
    evidence_fingerprint: Digest

    @model_validator(mode="after")
    def error_identity(self) -> BudgetSettlement:
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("settlement requires nonempty evidence references")
        if (self.outcome == "failure") != (self.error_fingerprint is not None):
            raise ValueError("failure requires an error fingerprint; success cannot carry one")
        return self


class BudgetAdmission(_Record):
    admitted: bool
    reservation_id: str
    replay: bool = False
    reason: str | None = None


class BudgetSnapshot(_Record):
    task_sha256: str
    used: dict[str, int | None]
    limits: dict[str, int]
    known_cost_subtotal_micro_cny: int
    cost_ceiling_enforced: bool
    cost_usage_exact: bool
    activity_us: int
    activity_remaining_us: int
    clock_uncertain: bool
    reservation_overrun: bool
    unknown_reservations: tuple[str, ...]
    active_readers: int
    active_jobs: int
    active_gpus: int


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha(value: object) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode()).hexdigest()


def _now(value: int | None) -> int:
    result = time.time_ns() // 1000 if value is None else value
    if type(result) is not int or not 0 <= result < 2**63:
        raise ValueError("observation time must be nonnegative integer Unix microseconds")
    return result


def _cny_micro(value: float) -> int:
    amount = Decimal(str(value)) * _MICRO
    if amount != amount.to_integral_value():
        raise ValueError("CNY budget must be exactly representable in integer micro-yuan")
    return int(amount)


def _limits(budget: ResearchBudget) -> dict[str, int]:
    result = {key: int(getattr(budget, key)) for key in BudgetAmounts.model_fields
              if key not in {"model_cost_micro_cny", "training_process_us", "gpu_us"}}
    result.update(model_cost_micro_cny=_cny_micro(budget.model_cost_cny),
                  training_process_us=budget.training_process_seconds * _MICRO,
                  gpu_us=budget.gpu_seconds * _MICRO)
    return result


def interval_union_us(intervals: list[tuple[int, int]]) -> int:
    """Pure union, not the sum of overlapping parallel actions."""
    total = 0
    edge: int | None = None
    for start, end in sorted(intervals):
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise ValueError("invalid activity interval")
        total += max(0, end - max(start, edge if edge is not None else start))
        edge = max(end, edge if edge is not None else end)
    return total


class ResearchBudgetLedger:
    def __init__(self, journal: StateJournal, *, task_sha256: str, budget: ResearchBudget,
                 price_reference_sha256: str | None = None) -> None:
        identity = _PolicyIdentity(task_sha256=task_sha256, price_reference_sha256=price_reference_sha256)
        self.journal = journal
        self.task_sha256 = identity.task_sha256
        self.budget = ResearchBudget.model_validate(budget.model_dump(mode="json"))
        self.price_reference_sha256 = price_reference_sha256
        self.policy = {"budget": self.budget.model_dump(mode="json"), "price_reference_sha256": price_reference_sha256}
        self.policy_sha256 = _sha(self.policy)
        self.limits = _limits(self.budget)

    def _binding(self, connection: sqlite3.Connection) -> None:
        payload = StateJournal._read(connection)
        request = payload.get("request")
        extra = request.get("extra") if isinstance(request, dict) else None
        if (payload.get("run_id") != self.journal.run_id or not isinstance(extra, dict)
                or extra.get("research_task_sha256") != self.task_sha256):
            raise BudgetConflictError("budget contract does not match the authoritative run request")
        root = self.journal.path.parent
        path = root / "input/research_task.v1.json"
        try:
            if (path.parent.is_symlink() or path.is_symlink() or not path.is_file()
                    or path.stat().st_size > 2 * 1024 * 1024):
                raise ValueError("invalid frozen task input path")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"task", "task_sha256"}:
                raise ValueError("invalid frozen task envelope")
            task = ResearchTaskContract.model_validate(raw["task"])
            if (raw["task_sha256"] != self.task_sha256
                    or _sha(task.model_dump(mode="json")).removeprefix("sha256:") != self.task_sha256
                    or task.project.project_id != payload.get("project")):
                raise ValueError("frozen task identity mismatch")
        except (OSError, ValueError, TypeError) as exc:
            raise RunStateIntegrityError("frozen research task is unavailable or invalid") from exc
        if task.budget != self.budget:
            raise BudgetConflictError("budget policy differs from the frozen research task")

    def initialize(self, *, now_us: int | None = None) -> None:
        """Explicit extension v2 installation; no legacy ledger is silently imported."""
        observed = _now(now_us)
        with self.journal.transaction() as connection:
            self._binding(connection)
            present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if present & _TABLES:
                if not _TABLES <= present:
                    raise RunStateIntegrityError("incomplete research budget extension")
                BudgetTransaction(self, connection).snapshot(now_us=observed)
                return
            payload = StateJournal._read(connection)
            nodes = payload.get("graph", {}).get("nodes", [])
            root = self.journal.path.parent
            prior_ledgers = (root / "resources/model_budget.v1.json", root / "discovery/budget")
            if (payload.get("status") != "created" or not nodes
                    or any(node.get("state") != "pending" for node in nodes)
                    or any(path.exists() or path.is_symlink() for path in prior_ledgers)):
                raise BudgetConflictError("existing execution or legacy usage requires explicit budget migration")
            statements = (
                "CREATE TABLE research_budget (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, run_id TEXT NOT NULL, journal_id TEXT NOT NULL, task_sha256 TEXT NOT NULL, policy TEXT NOT NULL, policy_sha256 TEXT NOT NULL, high_watermark_us INTEGER NOT NULL, clock_uncertain INTEGER NOT NULL CHECK(clock_uncertain IN (0,1)))",
                "CREATE TABLE research_operations (operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE)",
                "CREATE TABLE research_reservations (reservation_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, attempt_index INTEGER NOT NULL, specification TEXT NOT NULL, specification_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('reserved','settled','unknown','retained')), settlement TEXT, settlement_sha256 TEXT, unknown_reason TEXT, reconciliation TEXT, observed_lower_bound TEXT, observed_lower_bound_sha256 TEXT, UNIQUE(operation_id,attempt_index))",
                "CREATE TABLE research_activity (reservation_id TEXT PRIMARY KEY, started_us INTEGER NOT NULL, ended_us INTEGER)",
                "CREATE TABLE research_leases (reservation_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('reader','job')), gpus INTEGER NOT NULL, released INTEGER NOT NULL CHECK(released IN (0,1)))",
            )
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO research_budget VALUES (1,?,?,?,?,?,?,?,0)",
                (_BUDGET_EXTENSION_VERSION, self.journal.run_id, self.journal.journal_id, self.task_sha256, _json(self.policy), self.policy_sha256, observed))

    def _metadata(self, connection: sqlite3.Connection) -> tuple[int, bool]:
        self.journal.validate_transaction(connection)
        self._binding(connection)
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not present & _TABLES:
            raise BudgetNotInitialized("research budget extension requires explicit initialization")
        if not _TABLES <= present:
            raise RunStateIntegrityError("incomplete research budget extension")
        row = connection.execute("SELECT version,run_id,journal_id,task_sha256,policy,policy_sha256,high_watermark_us,clock_uncertain FROM research_budget WHERE id=1").fetchone()
        if row is None or row[:4] != (_BUDGET_EXTENSION_VERSION, self.journal.run_id, self.journal.journal_id, self.task_sha256):
            raise RunStateIntegrityError("research budget identity or extension version mismatch")
        try:
            policy = json.loads(row[4])
            if _sha(policy) != row[5]:
                raise ValueError("policy hash mismatch")
        except (ValueError, TypeError) as exc:
            raise RunStateIntegrityError("invalid research budget policy") from exc
        if policy != self.policy or row[5] != self.policy_sha256:
            raise BudgetConflictError("research budget policy is frozen")
        if type(row[6]) is not int or row[6] < 0 or row[7] not in (0, 1):
            raise RunStateIntegrityError("invalid research budget clock state")
        return row[6], bool(row[7])

    @contextmanager
    def transaction(self) -> Iterator[BudgetTransaction]:
        with self.journal.transaction() as connection:
            yield self.in_transaction(connection)

    def in_transaction(self, connection: sqlite3.Connection) -> BudgetTransaction:
        """Compose reservation/settlement with state transitions atomically."""
        self.journal.validate_transaction(connection)
        self._metadata(connection)
        return BudgetTransaction(self, connection)

    def snapshot(self, *, now_us: int | None = None) -> BudgetSnapshot:
        with self.journal.connection() as connection:
            connection.execute("BEGIN")
            return BudgetTransaction(self, connection).snapshot(now_us=now_us)

    def reserve(self, specification: BudgetReservation, *, now_us: int | None = None) -> BudgetAdmission:
        with self.transaction() as transaction:
            return transaction.reserve(specification, now_us=now_us)

    def settle(self, reservation_id: str, settlement: BudgetSettlement, *, now_us: int | None = None,
               activity_ended_us: int | None = None) -> None:
        with self.transaction() as transaction:
            transaction.settle(reservation_id, settlement, now_us=now_us, activity_ended_us=activity_ended_us)

    def mark_unknown(self, reservation_id: str, *, reason: str,
                     observed_lower_bound: BudgetAmounts | None = None, now_us: int | None = None) -> None:
        with self.transaction() as transaction:
            transaction.mark_unknown(reservation_id, reason=reason,
                observed_lower_bound=observed_lower_bound, now_us=now_us)

    def reconcile_stopped(self, reservation_id: str, *, actor: str, evidence_refs: tuple[str, ...],
                          now_us: int | None = None, activity_ended_us: int | None = None) -> None:
        with self.transaction() as transaction:
            transaction.reconcile_stopped(reservation_id, actor=actor, evidence_refs=evidence_refs,
                now_us=now_us, activity_ended_us=activity_ended_us)


class BudgetTransaction:
    """A view bound to an already-open transaction, never an independent commit."""
    def __init__(self, ledger: ResearchBudgetLedger, connection: sqlite3.Connection) -> None:
        self.ledger = ledger
        self.connection = connection

    def _rows(self) -> list[tuple[BudgetReservation, str, BudgetSettlement | None, BudgetAmounts | None]]:
        self.ledger._metadata(self.connection)
        rows = self.connection.execute("SELECT reservation_id,operation_id,attempt_index,specification,specification_sha256,state,settlement,settlement_sha256,unknown_reason,reconciliation,observed_lower_bound,observed_lower_bound_sha256 FROM research_reservations ORDER BY operation_id,attempt_index").fetchall()
        operations = dict(self.connection.execute("SELECT operation_id,fingerprint FROM research_operations"))
        expected: dict[str, int] = {}
        result: list[tuple[BudgetReservation, str, BudgetSettlement | None, BudgetAmounts | None]] = []
        try:
            for row in rows:
                spec = BudgetReservation.model_validate_json(row[3])
                if (row[:3] != (spec.reservation_id, spec.operation_id, spec.attempt_index)
                        or row[4] != _sha(spec.model_dump(mode="json"))
                        or operations.get(spec.operation_id) != spec.operation_fingerprint
                        or spec.attempt_index != expected.get(spec.operation_id, 0)):
                    raise ValueError("reservation identity mismatch")
                expected[spec.operation_id] = spec.attempt_index + 1
                settled = BudgetSettlement.model_validate_json(row[6]) if row[6] is not None else None
                if settled is not None:
                    self._validate_usage(spec, settled.actual, require_attempt=True)
                lower = BudgetAmounts.model_validate_json(row[10]) if row[10] is not None else None
                if ((lower is None) != (row[11] is None)
                        or lower is not None and (row[5] not in {"unknown", "retained"}
                            or row[11] != _sha(lower.model_dump(mode="json")))):
                    raise ValueError("invalid observed usage lower bound")
                if lower is not None:
                    self._validate_usage(spec, lower, require_attempt=False)
                if (row[5] not in {"reserved", "settled", "unknown", "retained"}
                        or (row[5] == "settled") != (settled is not None)
                        or (settled is None and row[7] is not None)
                        or (settled is not None and row[7] != _sha(settled.model_dump(mode="json")))
                        or (row[5] in {"unknown", "retained"} and not row[8])
                        or (row[5] == "retained") != (row[9] is not None)):
                    raise ValueError("reservation state mismatch")
                if row[9] is not None:
                    reconciliation = json.loads(row[9])
                    if (not isinstance(reconciliation, dict) or not isinstance(reconciliation.get("actor"), str)
                            or not reconciliation["actor"].strip() or not isinstance(reconciliation.get("evidence_refs"), list)
                            or not reconciliation["evidence_refs"]
                            or any(not isinstance(ref, str) or not ref.strip() for ref in reconciliation["evidence_refs"])):
                        raise ValueError("invalid stopped reconciliation evidence")
                result.append((spec, row[5], settled, lower))
            if set(operations) != set(expected):
                raise ValueError("operation has no durable reservation")
        except (ValueError, TypeError, ValidationError) as exc:
            raise RunStateIntegrityError("invalid research reservation record") from exc
        return result

    def _clock(self, now_us: int | None) -> tuple[int, bool]:
        observed = _now(now_us)
        previous, uncertain = self.ledger._metadata(self.connection)
        uncertain = uncertain or observed < previous
        effective = max(previous, observed)
        self.connection.execute("UPDATE research_budget SET high_watermark_us=?,clock_uncertain=? WHERE id=1", (effective, int(uncertain)))
        return effective, uncertain

    def observe_clock(self, *, now_us: int | None = None) -> None:
        """Preserve monotone clock evidence after a quota-only savepoint rollback."""
        self._clock(now_us)

    def snapshot(self, *, now_us: int | None = None) -> BudgetSnapshot:
        previous, uncertain = self.ledger._metadata(self.connection)
        # Read the real clock after opening the SQLite snapshot so a concurrent
        # writer cannot make an ordinary read appear to observe clock rollback.
        observed = _now(now_us)
        uncertain = uncertain or observed < previous
        effective = max(observed, previous)
        rows = self._rows()
        used = {key: 0 for key in self.ledger.limits}
        unknown: list[str] = []
        cost_known = True
        cost_exact = True
        overrun = False
        by_id = {spec.reservation_id: (spec, state) for spec, state, _, _ in rows}
        for spec, state, settled, lower in rows:
            amounts = settled.actual if settled is not None else spec.amounts
            if lower is not None:
                amounts = _maximum_amounts(amounts, lower)
            charges = amounts.units()
            if spec.kind == "model":
                if amounts.model_cost_micro_cny is None:
                    cost_exact = False
                    if spec.amounts.model_cost_micro_cny is not None:
                        charges["model_cost_micro_cny"] = spec.amounts.model_cost_micro_cny
                    else:
                        cost_known = False
                if settled is None:
                    cost_exact = False
            for key, value in charges.items():
                used[key] += value
                if (settled is not None or lower is not None) and value > spec.amounts.units().get(key, 0):
                    overrun = True
            if state in {"unknown", "retained"}:
                unknown.append(spec.reservation_id)
        intervals: list[tuple[int, int]] = []
        activities = self.connection.execute("SELECT reservation_id,started_us,ended_us FROM research_activity").fetchall()
        if {row[0] for row in activities} != set(by_id):
            raise RunStateIntegrityError("activity/reservation identity mismatch")
        for identifier, start, end in activities:
            if (type(start) is not int or start < 0 or start > previous
                    or (end is not None and (type(end) is not int or not start <= end <= previous))
                    or (end is None) != (by_id[identifier][1] in {"reserved", "unknown"})):
                raise RunStateIntegrityError("invalid persisted activity interval")
            intervals.append((start, effective if end is None else end))
        activity = interval_union_us(intervals)
        limit = self.ledger.budget.research_activity_seconds * _MICRO
        if uncertain:
            activity = max(activity, limit)
        readers = jobs = gpus = 0
        leases = self.connection.execute("SELECT reservation_id,kind,gpus,released FROM research_leases").fetchall()
        expected_leases = {spec.reservation_id for spec, _, _, _ in rows if spec.kind in {"reader", "job"}}
        if {row[0] for row in leases} != expected_leases:
            raise RunStateIntegrityError("lease/reservation identity mismatch")
        for identifier, kind, count, released in leases:
            spec, state = by_id[identifier]
            if (kind != spec.kind or count != spec.gpus or released not in (0, 1)
                    or bool(released) != (state in {"settled", "retained"})):
                raise RunStateIntegrityError("invalid persisted resource lease")
            if not released:
                readers += int(kind == "reader")
                jobs += int(kind == "job")
                gpus += count
        reported: dict[str, int | None] = dict(used)
        if not cost_known:
            reported["model_cost_micro_cny"] = None
        return BudgetSnapshot(task_sha256=self.ledger.task_sha256, used=reported, limits=self.ledger.limits,
            known_cost_subtotal_micro_cny=used["model_cost_micro_cny"],
            cost_ceiling_enforced=self.ledger.price_reference_sha256 is not None and cost_known,
            cost_usage_exact=cost_known and cost_exact, activity_us=activity,
            activity_remaining_us=max(0, limit - activity), clock_uncertain=uncertain,
            reservation_overrun=overrun, unknown_reservations=tuple(unknown),
            active_readers=readers, active_jobs=jobs, active_gpus=gpus)

    def reserve(self, specification: BudgetReservation, *, now_us: int | None = None) -> BudgetAdmission:
        specification = BudgetReservation.model_validate(specification.model_dump(mode="json"))
        observed, uncertain = self._clock(now_us)
        rows = self._rows()
        identifier = specification.reservation_id
        for existing, _, _, _ in rows:
            if existing.reservation_id == identifier:
                if existing != specification:
                    raise BudgetConflictError("reservation identity already has different inputs")
                return BudgetAdmission(admitted=True, reservation_id=identifier, replay=True)
        snapshot = self.snapshot(now_us=observed)
        reason = self._admission_reason(specification, rows, snapshot, uncertain)
        if reason:
            return BudgetAdmission(admitted=False, reservation_id=identifier, reason=reason)
        operation = self.connection.execute("SELECT operation_id,fingerprint FROM research_operations WHERE operation_id=? OR fingerprint=?",
            (specification.operation_id, specification.operation_fingerprint)).fetchall()
        if operation and operation != [(specification.operation_id, specification.operation_fingerprint)]:
            raise BudgetConflictError("operation identity or fingerprint is already bound")
        if not operation:
            self.connection.execute("INSERT INTO research_operations VALUES (?,?)", (specification.operation_id, specification.operation_fingerprint))
        payload = specification.model_dump(mode="json")
        self.connection.execute("INSERT INTO research_reservations VALUES (?,?,?,?,?,'reserved',NULL,NULL,NULL,NULL,NULL,NULL)",
            (identifier, specification.operation_id, specification.attempt_index, _json(payload), _sha(payload)))
        self.connection.execute("INSERT INTO research_activity VALUES (?,?,NULL)", (identifier, observed))
        if specification.kind in {"reader", "job"}:
            self.connection.execute("INSERT INTO research_leases VALUES (?,?,?,0)", (identifier, specification.kind, specification.gpus))
        return BudgetAdmission(admitted=True, reservation_id=identifier)

    def _admission_reason(self, spec: BudgetReservation,
                          rows: list[tuple[BudgetReservation, str, BudgetSettlement | None, BudgetAmounts | None]],
                          snapshot: BudgetSnapshot, uncertain: bool) -> str | None:
        budget = self.ledger.budget
        if StateJournal._read(self.connection).get("status") not in {"created", "running"}:
            return "run_not_executable"
        if uncertain:
            return "clock_reconciliation_required"
        if snapshot.reservation_overrun:
            return "reservation_overrun"
        if snapshot.activity_remaining_us <= 0:
            return "research_activity_exhausted"
        previous = [(item, state, settled) for item, state, settled, _ in rows if item.operation_id == spec.operation_id]
        if spec.attempt_index != len(previous):
            raise BudgetConflictError("operation attempt indices must be consecutive")
        if previous:
            last, state, settled = previous[-1]
            if last.operation_fingerprint != spec.operation_fingerprint:
                raise BudgetConflictError("operation fingerprint is immutable")
            if state != "settled" or settled is None or settled.outcome != "failure":
                return "operation_not_retryable"
            repeated = 0
            for _, _, old in reversed(previous):
                if old is None or old.outcome != "failure" or (old.error_fingerprint, old.evidence_fingerprint) != (settled.error_fingerprint, settled.evidence_fingerprint):
                    break
                repeated += 1
            if repeated >= budget.repeated_error_limit:
                return "repeated_error_limit"
        attempts_used = sum(max(1, settled.actual.model_requests if settled is not None and item.kind == "model" else 1)
                            for item, _, settled in previous)
        attempts_reserved = max(1, spec.amounts.model_requests) if spec.kind == "model" else 1
        if spec.attempt_index > budget.operation_retries or attempts_used + attempts_reserved > budget.operation_retries + 1:
            return "operation_retry_limit"
        if spec.kind == "model":
            if spec.request_input_tokens > budget.request_input_tokens:
                return "request_input_limit"
            output = budget.coding_output_tokens if spec.output_class == "coding" else budget.request_output_tokens
            if spec.request_output_tokens > output:
                return "request_output_limit"
            if (self.ledger.price_reference_sha256 is None) != (spec.amounts.model_cost_micro_cny is None):
                return "model_price_policy_mismatch"
        if spec.kind == "reader" and snapshot.active_readers >= budget.concurrent_readers:
            return "reader_concurrency_limit"
        if spec.kind == "job":
            if spec.job_duration_us > budget.training_job_seconds * _MICRO:
                return "training_job_time_limit"
            if snapshot.active_jobs >= budget.concurrent_training_jobs:
                return "training_concurrency_limit"
            if snapshot.active_gpus + spec.gpus > budget.max_gpus:
                return "gpu_allocation_limit"
        for key, amount in spec.amounts.units().items():
            used = snapshot.known_cost_subtotal_micro_cny if key == "model_cost_micro_cny" else int(snapshot.used[key] or 0)
            if used + amount > self.ledger.limits[key]:
                return "exhausted:" + key
        return None

    def _get(self, reservation_id: str) -> tuple[BudgetReservation, str, BudgetSettlement | None, BudgetAmounts | None]:
        row = next((row for row in self._rows() if row[0].reservation_id == reservation_id), None)
        if row is None:
            raise BudgetConflictError("unknown budget reservation")
        return row

    def _activity_end(self, reservation_id: str, observed: int, activity_ended_us: int | None) -> int:
        ended = observed if activity_ended_us is None else _now(activity_ended_us)
        row = self.connection.execute("SELECT started_us,ended_us FROM research_activity WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None or not row[0] <= ended <= observed:
            raise BudgetConflictError("activity end must be between reservation start and observed time")
        if activity_ended_us is not None and row[1] is not None and row[1] != ended:
            raise BudgetConflictError("closed activity end is immutable")
        return ended

    def _close(self, reservation_id: str, ended: int) -> None:
        self.connection.execute("UPDATE research_activity SET ended_us=? WHERE reservation_id=?", (ended, reservation_id))
        self.connection.execute("UPDATE research_leases SET released=1 WHERE reservation_id=?", (reservation_id,))

    def _validate_usage(self, spec: BudgetReservation, amounts: BudgetAmounts, *, require_attempt: bool) -> None:
        _validate_amount_family(spec.kind, amounts, require_attempt=require_attempt)
        if (spec.kind == "model" and self.ledger.price_reference_sha256 is None
                and amounts.model_cost_micro_cny is not None):
            raise BudgetConflictError("unpriced model usage cannot claim a verified cost")

    def settle(self, reservation_id: str, settlement: BudgetSettlement, *, now_us: int | None = None,
               activity_ended_us: int | None = None) -> None:
        settlement = BudgetSettlement.model_validate(settlement.model_dump(mode="json"))
        observed = _now(now_us)
        self._clock(observed)
        spec, state, previous, _ = self._get(reservation_id)
        self._validate_usage(spec, settlement.actual, require_attempt=True)
        ended = self._activity_end(reservation_id, observed, activity_ended_us)
        if previous is not None:
            if previous != settlement:
                raise BudgetConflictError("settlement is immutable")
            return
        if state != "reserved":
            raise BudgetConflictError("unknown usage requires explicit conservative reconciliation")
        payload = settlement.model_dump(mode="json")
        self.connection.execute("UPDATE research_reservations SET state='settled',settlement=?,settlement_sha256=? WHERE reservation_id=?",
            (_json(payload), _sha(payload), reservation_id))
        self._close(reservation_id, ended)

    def mark_unknown(self, reservation_id: str, *, reason: str,
                     observed_lower_bound: BudgetAmounts | None = None, now_us: int | None = None) -> None:
        self._clock(now_us)
        if not reason.strip():
            raise ValueError("unknown outcome requires a reason")
        spec, state, _, lower = self._get(reservation_id)
        if observed_lower_bound is not None:
            observed_lower_bound = BudgetAmounts.model_validate(observed_lower_bound.model_dump(mode="json"))
            self._validate_usage(spec, observed_lower_bound, require_attempt=False)
            lower = observed_lower_bound if lower is None else _maximum_amounts(lower, observed_lower_bound)
        previous = self.connection.execute("SELECT unknown_reason FROM research_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if state != "reserved" and not (state == "unknown" and previous is not None and previous[0] == reason):
            raise BudgetConflictError("only a reserved operation can become unknown")
        payload = lower.model_dump(mode="json") if lower is not None else None
        self.connection.execute("UPDATE research_reservations SET state='unknown',unknown_reason=?,observed_lower_bound=?,observed_lower_bound_sha256=? WHERE reservation_id=?",
            (reason, _json(payload) if payload is not None else None, _sha(payload) if payload is not None else None, reservation_id))

    def reconcile_stopped(self, reservation_id: str, *, actor: str, evidence_refs: tuple[str, ...],
                          now_us: int | None = None, activity_ended_us: int | None = None) -> None:
        observed = _now(now_us)
        self._clock(observed)
        if not actor.strip() or not evidence_refs or any(not value.strip() for value in evidence_refs):
            raise ValueError("stopped reconciliation requires actor and evidence")
        _, state, _, _ = self._get(reservation_id)
        ended = self._activity_end(reservation_id, observed, activity_ended_us)
        receipt = _json({"actor": actor, "evidence_refs": evidence_refs})
        old = self.connection.execute("SELECT reconciliation FROM research_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if state == "retained" and old is not None and old[0] == receipt:
            return
        if state != "unknown":
            raise BudgetConflictError("only an unknown operation can be explicitly reconciled")
        self.connection.execute("UPDATE research_reservations SET state='retained',reconciliation=? WHERE reservation_id=?", (receipt, reservation_id))
        self._close(reservation_id, ended)
