from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.harness.discovery.models import BudgetLimits, BudgetTransaction
from app.storage.discovery_budget_ledger import BudgetExceededError, BudgetLedger
from app.storage.discovery_common import DiscoveryConflictError


def _transaction(
    index: int,
    *,
    proposals: int = 1,
    idempotency_key: str | None = None,
) -> BudgetTransaction:
    return BudgetTransaction(
        transaction_id=f"transaction-{index}",
        run_id="run-1",
        candidate_id=f"candidate-{index}",
        idempotency_key=idempotency_key or f"charge:{index}",
        proposals=proposals,
        llm_tokens=10,
    )


def test_legacy_budget_read_is_zero_and_lazy(tmp_path: Path) -> None:
    run_root = tmp_path / "legacy"
    run_root.mkdir()
    ledger = BudgetLedger(
        run_root,
        run_id="run-1",
        limits=BudgetLimits(proposals=3, llm_tokens=100),
    )

    snapshot = ledger.snapshot()

    assert snapshot.used.proposals == 0
    assert snapshot.remaining.proposals == 3
    assert not (run_root / "discovery").exists()


def test_budget_charge_is_idempotent_and_limits_are_frozen(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    limits = BudgetLimits(proposals=2, llm_tokens=30)
    ledger = BudgetLedger(run_root, run_id="run-1", limits=limits)
    first = _transaction(1)

    assert ledger.charge(first) == first
    assert ledger.charge(first) == first
    assert ledger.charge(_transaction(1)) == first
    assert ledger.snapshot().used.proposals == 1
    with pytest.raises(DiscoveryConflictError):
        ledger.charge(_transaction(2, idempotency_key=first.idempotency_key))
    with pytest.raises(BudgetExceededError):
        ledger.charge(_transaction(3, proposals=2))
    assert len(ledger.transactions()) == 1

    changed = BudgetLedger(
        run_root,
        run_id="run-1",
        limits=BudgetLimits(proposals=10, llm_tokens=100),
    )
    with pytest.raises(DiscoveryConflictError):
        changed.charge(_transaction(4))


def test_budget_recovers_state_cache_from_transactions(tmp_path: Path) -> None:
    ledger = BudgetLedger(
        tmp_path / "run",
        run_id="run-1",
        limits=BudgetLimits(proposals=3, llm_tokens=100),
    )
    ledger.charge(_transaction(1))
    ledger.state_path.unlink()

    recovered = ledger.recover()

    assert recovered.used.proposals == 1
    assert recovered.remaining.proposals == 2
    assert ledger.state_path.exists()


def test_parallel_slot_leases_are_bounded_and_recoverable(tmp_path: Path) -> None:
    ledger = BudgetLedger(
        tmp_path / "run",
        run_id="run-1",
        limits=BudgetLimits(max_parallel=2),
    )

    first = ledger.acquire_slot(lease_id="lease-1", candidate_id="candidate-1")
    assert ledger.acquire_slot(lease_id="lease-1", candidate_id="candidate-1") == first
    ledger.acquire_slot(lease_id="lease-2", candidate_id="candidate-2")
    with pytest.raises(BudgetExceededError):
        ledger.acquire_slot(lease_id="lease-3", candidate_id="candidate-3")

    assert ledger.release_slot("lease-1") is True
    ledger.acquire_slot(lease_id="lease-3", candidate_id="candidate-3")
    assert ledger.recover_slots(active_lease_ids={"lease-3"}) == ("lease-2",)
    assert [lease.lease_id for lease in ledger.snapshot().active_slots] == ["lease-3"]


def test_concurrent_budget_charges_are_atomic(tmp_path: Path) -> None:
    ledger = BudgetLedger(
        tmp_path / "run",
        run_id="run-1",
        limits=BudgetLimits(proposals=5, llm_tokens=1_000),
    )

    def charge(index: int) -> bool:
        try:
            ledger.charge(_transaction(index))
        except BudgetExceededError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(charge, range(20)))

    assert sum(results) == 5
    assert ledger.snapshot().used.proposals == 5
    assert len(ledger.transactions()) == 5
