"""Atomic budget accounting and parallel-slot leases for discovery runs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.harness.discovery.models import BudgetLimits, BudgetTransaction
from app.storage.discovery_common import (
    DiscoveryConflictError,
    DiscoveryCorruptionError,
    DiscoveryPaths,
    DiscoveryStoreError,
    atomic_write_json,
    discovery_lock,
    equivalent_model_payload,
    iter_json_files,
    model_payload,
    read_json,
    stable_key,
)


class BudgetExceededError(DiscoveryStoreError):
    def __init__(self, resource: str, attempted: float, limit: float) -> None:
        super().__init__(
            f"discovery budget exceeded for {resource}: attempted {attempted}, limit {limit}"
        )
        self.resource = resource
        self.attempted = attempted
        self.limit = limit


class BudgetUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposals: int = Field(default=0, ge=0)
    llm_tokens: int = Field(default=0, ge=0)
    gpu_seconds: float = Field(default=0.0, ge=0.0)
    wall_seconds: float = Field(default=0.0, ge=0.0)
    api_cost: float = Field(default=0.0, ge=0.0)

    def plus(self, transaction: BudgetTransaction) -> BudgetUsage:
        return BudgetUsage(
            proposals=self.proposals + transaction.proposals,
            llm_tokens=self.llm_tokens + transaction.llm_tokens,
            gpu_seconds=self.gpu_seconds + transaction.gpu_seconds,
            wall_seconds=self.wall_seconds + transaction.wall_seconds,
            api_cost=self.api_cost + transaction.api_cost,
        )


class SlotLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["discovery_slot_lease.v1"] = "discovery_slot_lease.v1"
    run_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["budget_snapshot.v1"] = "budget_snapshot.v1"
    run_id: str
    limits: BudgetLimits
    used: BudgetUsage
    remaining: BudgetUsage
    active_slots: tuple[SlotLease, ...] = ()


class BudgetLedger:
    """Use immutable transactions as truth and an atomic state file as cache."""

    def __init__(self, run_root: Path, *, run_id: str, limits: BudgetLimits) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.limits = limits
        self.budget_root = self.paths.root / "budget"
        self.transactions_dir = self.budget_root / "transactions"
        self.leases_dir = self.budget_root / "leases"
        self.limits_path = self.budget_root / "limits.json"
        self.state_path = self.budget_root / "state.json"

    def charge(self, transaction: BudgetTransaction) -> BudgetTransaction:
        if transaction.run_id != self.paths.run_id:
            raise ValueError("budget transaction run_id does not match ledger run")
        with discovery_lock(self.paths):
            self._ensure_limits_unlocked()
            existing = self._get_transaction_unlocked(transaction.transaction_id)
            if existing is not None:
                if existing == transaction or equivalent_model_payload(
                    existing,
                    transaction,
                    ignored_fields=frozenset({"created_at"}),
                ):
                    return existing
                raise DiscoveryConflictError(
                    f"transaction_id already contains different data: {transaction.transaction_id}"
                )
            for other in self._transactions_unlocked():
                if other.idempotency_key != transaction.idempotency_key:
                    continue
                if other == transaction or equivalent_model_payload(
                    other,
                    transaction,
                    ignored_fields=frozenset({"transaction_id", "created_at"}),
                ):
                    return other
                raise DiscoveryConflictError(
                    f"budget idempotency key already used: {transaction.idempotency_key}"
                )

            used = self._usage_unlocked()
            attempted = used.plus(transaction)
            self._assert_within_limits(attempted)
            # The immutable transaction is authoritative.  A crash before the
            # cache update is repaired by snapshot()/recover() from this file.
            atomic_write_json(self._transaction_path(transaction.transaction_id), model_payload(transaction))
            self._write_state_unlocked(attempted)
            return transaction

    def can_charge(self, transaction: BudgetTransaction) -> bool:
        if transaction.run_id != self.paths.run_id:
            return False
        if not self.paths.root.exists():
            attempted = BudgetUsage().plus(transaction)
            return self._is_within_limits(attempted)
        with discovery_lock(self.paths):
            self._validate_limits_unlocked()
            existing = self._get_transaction_unlocked(transaction.transaction_id)
            if existing is not None and equivalent_model_payload(
                existing,
                transaction,
                ignored_fields=frozenset({"created_at"}),
            ):
                return True
            for other in self._transactions_unlocked():
                if other.idempotency_key == transaction.idempotency_key and equivalent_model_payload(
                    other,
                    transaction,
                    ignored_fields=frozenset({"transaction_id", "created_at"}),
                ):
                    return True
            return self._is_within_limits(self._usage_unlocked().plus(transaction))

    def transactions(self) -> list[BudgetTransaction]:
        transactions = [
            BudgetTransaction.model_validate(read_json(path))
            for path in iter_json_files(self.transactions_dir)
        ]
        return sorted(transactions, key=lambda item: (item.created_at, item.transaction_id))

    def snapshot(self) -> BudgetSnapshot:
        if not self.paths.root.exists():
            return self._snapshot_for(BudgetUsage(), ())
        with discovery_lock(self.paths):
            self._validate_limits_unlocked()
            used = self._usage_unlocked()
            leases = tuple(self._leases_unlocked())
            return self._snapshot_for(used, leases)

    def acquire_slot(self, *, lease_id: str, candidate_id: str) -> SlotLease:
        requested = SlotLease(
            run_id=self.paths.run_id,
            lease_id=lease_id,
            candidate_id=candidate_id,
        )
        with discovery_lock(self.paths):
            self._ensure_limits_unlocked()
            existing = self._get_lease_unlocked(lease_id)
            if existing is not None:
                if existing.candidate_id == candidate_id:
                    return existing
                raise DiscoveryConflictError(f"lease_id already used: {lease_id}")
            active = self._leases_unlocked()
            if len(active) >= self.limits.max_parallel:
                raise BudgetExceededError(
                    "max_parallel",
                    float(len(active) + 1),
                    float(self.limits.max_parallel),
                )
            atomic_write_json(self._lease_path(lease_id), model_payload(requested))
            self._write_state_unlocked(self._usage_unlocked())
            return requested

    def release_slot(self, lease_id: str) -> bool:
        if not self.paths.root.exists():
            return False
        with discovery_lock(self.paths):
            path = self._lease_path(lease_id)
            if not path.exists():
                return False
            path.unlink()
            self._write_state_unlocked(self._usage_unlocked())
            return True

    def recover_slots(self, *, active_lease_ids: set[str]) -> tuple[str, ...]:
        """Release leases not owned by the recovered scheduler state."""

        if not self.paths.root.exists():
            return ()
        released: list[str] = []
        with discovery_lock(self.paths):
            for lease in self._leases_unlocked():
                if lease.lease_id in active_lease_ids:
                    continue
                self._lease_path(lease.lease_id).unlink(missing_ok=True)
                released.append(lease.lease_id)
            self._write_state_unlocked(self._usage_unlocked())
        return tuple(sorted(released))

    def recover(self) -> BudgetSnapshot:
        """Rebuild the mutable state cache from immutable transactions/leases."""

        if not self.paths.root.exists():
            return self._snapshot_for(BudgetUsage(), ())
        with discovery_lock(self.paths):
            self._ensure_limits_unlocked()
            used = self._usage_unlocked()
            self._assert_within_limits(used, corruption=True)
            leases = tuple(self._leases_unlocked())
            if len(leases) > self.limits.max_parallel:
                raise DiscoveryCorruptionError("persisted slot leases exceed max_parallel")
            self._write_state_unlocked(used)
            return self._snapshot_for(used, leases)

    def _snapshot_for(
        self,
        used: BudgetUsage,
        leases: tuple[SlotLease, ...],
    ) -> BudgetSnapshot:
        remaining = BudgetUsage(
            proposals=max(0, self.limits.proposals - used.proposals),
            llm_tokens=max(0, self.limits.llm_tokens - used.llm_tokens),
            gpu_seconds=max(0.0, self.limits.gpu_seconds - used.gpu_seconds),
            wall_seconds=max(0.0, self.limits.wall_seconds - used.wall_seconds),
            api_cost=max(0.0, self.limits.api_cost - used.api_cost),
        )
        return BudgetSnapshot(
            run_id=self.paths.run_id,
            limits=self.limits,
            used=used,
            remaining=remaining,
            active_slots=leases,
        )

    def _ensure_limits_unlocked(self) -> None:
        if self.limits_path.exists():
            self._validate_limits_unlocked()
            return
        atomic_write_json(self.limits_path, model_payload(self.limits))

    def _validate_limits_unlocked(self) -> None:
        if not self.limits_path.exists():
            return
        persisted = BudgetLimits.model_validate(read_json(self.limits_path))
        if persisted != self.limits:
            raise DiscoveryConflictError(
                "budget limits are frozen after the first charge or lease"
            )

    def _transaction_path(self, transaction_id: str) -> Path:
        return self.transactions_dir / f"{stable_key(transaction_id)}.json"

    def _lease_path(self, lease_id: str) -> Path:
        return self.leases_dir / f"{stable_key(lease_id)}.json"

    def _get_transaction_unlocked(self, transaction_id: str) -> BudgetTransaction | None:
        path = self._transaction_path(transaction_id)
        if not path.exists():
            return None
        return BudgetTransaction.model_validate(read_json(path))

    def _transactions_unlocked(self) -> list[BudgetTransaction]:
        return [
            BudgetTransaction.model_validate(read_json(path))
            for path in iter_json_files(self.transactions_dir)
        ]

    def _usage_unlocked(self) -> BudgetUsage:
        used = BudgetUsage()
        for transaction in self._transactions_unlocked():
            used = used.plus(transaction)
        return used

    def _get_lease_unlocked(self, lease_id: str) -> SlotLease | None:
        path = self._lease_path(lease_id)
        if not path.exists():
            return None
        return SlotLease.model_validate(read_json(path))

    def _leases_unlocked(self) -> list[SlotLease]:
        leases = [SlotLease.model_validate(read_json(path)) for path in iter_json_files(self.leases_dir)]
        return sorted(leases, key=lambda item: (item.acquired_at, item.lease_id))

    def _is_within_limits(self, usage: BudgetUsage) -> bool:
        try:
            self._assert_within_limits(usage)
        except BudgetExceededError:
            return False
        return True

    def _assert_within_limits(
        self,
        usage: BudgetUsage,
        *,
        corruption: bool = False,
    ) -> None:
        checks = (
            ("proposals", float(usage.proposals), float(self.limits.proposals)),
            ("llm_tokens", float(usage.llm_tokens), float(self.limits.llm_tokens)),
            ("gpu_seconds", usage.gpu_seconds, self.limits.gpu_seconds),
            ("wall_seconds", usage.wall_seconds, self.limits.wall_seconds),
            ("api_cost", usage.api_cost, self.limits.api_cost),
        )
        for resource, attempted, limit in checks:
            if attempted <= limit + 1e-9:
                continue
            if corruption:
                raise DiscoveryCorruptionError(
                    f"persisted {resource} usage {attempted} exceeds frozen limit {limit}"
                )
            raise BudgetExceededError(resource, attempted, limit)

    def _write_state_unlocked(self, used: BudgetUsage) -> None:
        payload = {
            "schema_id": "budget_ledger_state.v1",
            "run_id": self.paths.run_id,
            "used": model_payload(used),
            "active_slots": [model_payload(lease) for lease in self._leases_unlocked()],
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        atomic_write_json(self.state_path, payload)
