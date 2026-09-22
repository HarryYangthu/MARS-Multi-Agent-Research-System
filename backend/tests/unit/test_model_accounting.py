"""Real process/ledger contracts and genuine refused connections, without providers' success substitutes."""
from __future__ import annotations

import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import socket
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any

import pytest

from app.harness.llm.accounting import (ModelConcurrencyBusy, ResourceBudgetError,
    ResourceReconciliationRequired, RunModelBudget, guarded_complete, run_resource_scope)
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message


def _policy(**limits: Any) -> dict[str, Any]:
    return {"schema": "runtime.resources.v1", "limits": {
        "max_model_requests": 32, "max_total_tokens": 100000, "max_parallel_model_calls": 3,
        "max_elapsed_seconds": 60, "max_cost": None, **limits,
    }, "prices": {"openai/test-model": {"input_per_million": 1, "output_per_million": 2}}, "currency": "USD"}


def _config() -> LLMConfig:
    return LLMConfig(provider="openai", model="test-model", max_tokens=16, max_retries=0,
                     request_timeout_seconds=0.2)


def _messages() -> list[Message]:
    return [Message(role="user", content="Budget contract input; no model success is requested.")]


def _crash_after_reservation(root: str, policy: dict[str, Any], connection: Connection) -> None:
    budget = RunModelBudget(Path(root), configuration=policy)
    reservation = budget.reserve(_messages(), _config(), {"task_id": "interrupted-ledger-operation"})
    connection.send(reservation.request_id)
    connection.close()
    os._exit(0)  # Real abrupt process death without settlement or lock finalizers.


def _competing_process(root: str, policy: dict[str, Any]) -> str:
    budget = RunModelBudget(Path(root), configuration=policy)
    try:
        budget.reserve(_messages(), _config(), {"task_id": "competing-process"})
    except ModelConcurrencyBusy:
        return "busy"
    return "unexpectedly_reserved"


@pytest.mark.parametrize("max_requests", [32, None])
def test_concurrent_reservations_enforce_shared_slot_limit_and_unknown_cost(tmp_path: Path, max_requests: int | None) -> None:
    policy = _policy(max_model_requests=max_requests)
    owners = [RunModelBudget(tmp_path, configuration=policy) for _ in range(12)]

    def reserve(index: int) -> tuple[int, Any]:
        try:
            return index, owners[index].reserve(_messages(), _config(), {"task_id": str(index)})
        except ModelConcurrencyBusy:
            return index, None

    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(reserve, range(12)))
    reservations = [(index, reservation) for index, reservation in results if reservation is not None]
    assert len(reservations) == 3
    for index, reservation in reservations:
        owners[index].settle(reservation, usage=None, complete=False, outcome="failed")
    state = json.loads(owners[0].path.read_text())
    assert len(state["requests"]) == 3
    assert all(row["status"] == "failed" and row["usage_complete"] is False
               and row["charged_tokens"] == row["reserved_tokens"]
               and row["charged_cost"] == row["reserved_cost"] for row in state["requests"].values())


def test_other_process_respects_the_live_model_lease(tmp_path: Path) -> None:
    policy = _policy(max_parallel_model_calls=1)
    owner = RunModelBudget(tmp_path, configuration=policy)
    reservation = owner.reserve(_messages(), _config(), {})
    before = owner.path.read_bytes()
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as worker:
        assert worker.submit(_competing_process, str(tmp_path), policy).result(timeout=15) == "busy"
    assert owner.path.read_bytes() == before
    owner.settle(reservation, usage=None, complete=False, outcome="cancelled")


@pytest.mark.parametrize("missing_price", [False, True])
def test_monetary_limit_blocks_unpriced_or_over_budget_calls_before_reservation(tmp_path: Path, missing_price: bool) -> None:
    policy = _policy(max_cost=0.000001)
    if missing_price:
        policy["prices"] = {}
    budget = RunModelBudget(tmp_path, configuration=policy)
    with pytest.raises(ResourceBudgetError, match="explicit prices" if missing_price else "monetary reservation"):
        budget.reserve(_messages(), _config(), {})
    assert not budget.path.exists()


def test_policy_drift_and_persisted_policy_tampering_fail_closed(tmp_path: Path) -> None:
    owner = RunModelBudget(tmp_path, configuration=_policy())
    reservation = owner.reserve(_messages(), _config(), {})
    owner.settle(reservation, usage=None, complete=False, outcome="cancelled")
    changed = RunModelBudget(tmp_path, configuration=_policy(max_total_tokens=200000))
    with pytest.raises(ResourceBudgetError, match="policy changed"):
        changed.reserve(_messages(), _config(), {})
    original = json.loads(owner.path.read_text())
    original["configuration"]["limits"]["max_total_tokens"] = 99999999
    owner.path.write_text(json.dumps(original))
    with pytest.raises(ResourceBudgetError, match="configuration is corrupt"):
        owner.reserve(_messages(), _config(), {})


def test_explicit_revision_adopts_policy_and_renews_time_without_refunding_usage(tmp_path: Path) -> None:
    owner = RunModelBudget(tmp_path, configuration=_policy())
    reservation = owner.reserve(_messages(), _config(), {"invocation_id": "old"})
    owner.settle(reservation, usage=None, complete=False, outcome="cancelled")
    original = json.loads(owner.path.read_text())
    original["started_at"] = time.time() - 3600
    owner.path.write_text(json.dumps(original))
    with pytest.raises(ResourceBudgetError, match="elapsed-time"):
        owner.reserve(_messages(), _config(), {})

    updated = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=None))
    updated.begin_revision(invocation_id="new", reason="Explicit user retry")
    after = json.loads(owner.path.read_text())
    assert after["requests"] == original["requests"]
    assert after["started_at"] == original["started_at"]
    assert after["revision_started_at"] > after["started_at"]
    assert after["revisions"][0]["previous_configuration"] == original["configuration"]
    assert after["configuration"] == updated.configuration
    updated.begin_revision(invocation_id="new", reason="Explicit user retry")
    assert json.loads(owner.path.read_text()) == after
    next_request = updated.reserve(_messages(), _config(), {"invocation_id": "new"})
    updated.settle(next_request, usage=None, complete=False, outcome="cancelled")
    with pytest.raises(ResourceBudgetError, match="immutable"):
        updated.begin_revision(invocation_id="new", reason="Changed reason")
    with pytest.raises(ResourceBudgetError, match="fresh invocation"):
        updated.begin_revision(invocation_id="old", reason="Cannot reuse old call")


@pytest.mark.parametrize("limit,error", [("max_total_tokens", "total-token"), ("max_model_requests", "model-request")])
def test_revision_keeps_aggregate_hard_limits(tmp_path: Path, limit: str, error: str) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy())
    reservation = budget.reserve(_messages(), _config(), {})
    budget.settle(reservation, usage=None, complete=False, outcome="cancelled")
    changed = RunModelBudget(tmp_path, configuration=_policy(**{limit: reservation.tokens if limit == "max_total_tokens" else 1}))
    changed.begin_revision(invocation_id="fresh", reason="Explicit retry")
    with pytest.raises(ResourceBudgetError, match=error):
        changed.reserve(_messages(), _config(), {})


def test_revision_cannot_replace_policy_while_a_model_request_is_active(tmp_path: Path) -> None:
    owner = RunModelBudget(tmp_path, configuration=_policy())
    reservation = owner.reserve(_messages(), _config(), {})
    updated = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=None))
    before = owner.path.read_bytes()
    with pytest.raises(ResourceBudgetError, match="active"):
        updated.begin_revision(invocation_id="new", reason="Explicit retry")
    assert owner.path.read_bytes() == before
    owner.settle(reservation, usage=None, complete=False, outcome="cancelled")


def test_configuration_is_an_immutable_snapshot(tmp_path: Path) -> None:
    supplied = _policy(max_model_requests=1)
    budget = RunModelBudget(tmp_path, configuration=supplied)
    supplied["limits"]["max_model_requests"] = 999
    reservation = budget.reserve(_messages(), _config(), {})
    budget.settle(reservation, usage=None, complete=False, outcome="cancelled")
    with pytest.raises(ResourceBudgetError, match="model-request budget"):
        budget.reserve(_messages(), _config(), {})


def test_unlimited_requests_cross_old_ceiling_without_losing_ledger_rows(tmp_path: Path) -> None:
    # Real reservation/settlement operations only; no model calls or responses.
    policy = _policy(max_model_requests=None)
    budget = RunModelBudget(tmp_path, configuration=policy)
    for index in range(257):
        reservation = budget.reserve(_messages(), _config(), {"task_id": str(index)})
        budget.settle(reservation, usage=None, complete=False, outcome="cancelled")
    state = json.loads(budget.path.read_text())
    assert state["configuration"]["limits"]["max_model_requests"] is None
    assert len(state["requests"]) == 257
    assert all(row["status"] == "cancelled" and row["usage"] is None and not row["usage_complete"]
               and row["charged_tokens"] == row["reserved_tokens"] for row in state["requests"].values())
    fresh = RunModelBudget(tmp_path, configuration=policy)
    assert fresh.recover_abandoned() == ()
    assert json.loads(fresh.path.read_text()) == state
    finite = RunModelBudget(tmp_path, configuration=_policy())
    with pytest.raises(ResourceBudgetError, match="policy changed"):
        finite.reserve(_messages(), _config(), {})


def test_unlimited_count_does_not_remove_token_reservations(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=None, max_total_tokens=1))
    with pytest.raises(ResourceBudgetError, match="total-token"):
        budget.reserve(_messages(), _config(), {})
    assert not budget.path.exists()


@pytest.mark.parametrize("key,value", [("max_model_requests", True), ("max_model_requests", "null"),
    ("max_model_requests", 0), ("max_total_tokens", None), ("max_parallel_model_calls", None)])
def test_only_explicit_request_null_is_unlimited(tmp_path: Path, key: str, value: Any) -> None:
    with pytest.raises(ValueError, match=key):
        RunModelBudget(tmp_path, configuration=_policy(**{key: value}))
    missing = _policy()
    del missing["limits"]["max_model_requests"]
    with pytest.raises(ValueError, match="max_model_requests"):
        RunModelBudget(tmp_path, configuration=missing)


def test_actual_resource_configuration_has_no_request_count_ceiling(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path)
    assert budget.configuration["limits"]["max_model_requests"] is None


def test_crashed_request_requires_explicit_reconciliation_without_refunding_unknown_usage(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    policy = _policy(max_parallel_model_calls=1)
    process = context.Process(target=_crash_after_reservation, args=(str(tmp_path), policy, child))
    process.start()
    child.close()
    assert parent.poll(15)
    request_id = parent.recv()
    parent.close()
    process.join(timeout=15)
    assert process.exitcode == 0
    fresh = RunModelBudget(tmp_path, configuration=policy)
    before = json.loads(fresh.path.read_text())["requests"][request_id]
    with pytest.raises(ResourceReconciliationRequired, match=request_id):
        fresh.reserve(_messages(), _config(), {})
    assert fresh.recover_abandoned() == (request_id,)
    with pytest.raises(ResourceReconciliationRequired, match="reconciliation"):
        fresh.begin_revision(invocation_id="new", reason="A retry cannot erase unknown outcomes")
    fresh.reconcile_abandoned(request_id, actor="test-operator", reason="Owner exited; response remains unknown",
                              evidence_refs=("resources/model_budget.v1.json",))
    # Same explicit reconciliation is idempotent; it does not refund or resend.
    fresh.reconcile_abandoned(request_id, actor="test-operator", reason="Owner exited; response remains unknown",
                              evidence_refs=("resources/model_budget.v1.json",))
    row = json.loads(fresh.path.read_text())["requests"][request_id]
    assert row["status"] == "abandoned" and row["usage"] is None and not row["usage_complete"]
    assert row["charged_tokens"] == before["reserved_tokens"]
    assert row["charged_cost"] == before["reserved_cost"]
    next_request = fresh.reserve(_messages(), _config(), {"task_id": "independent-next-task"})
    assert next_request.request_id != request_id
    fresh.settle(next_request, usage=None, complete=False, outcome="cancelled")


def test_active_request_cannot_be_reconciled_or_settled_by_another_owner(tmp_path: Path) -> None:
    owner = RunModelBudget(tmp_path, configuration=_policy())
    other = RunModelBudget(tmp_path, configuration=_policy())
    reservation = owner.reserve(_messages(), _config(), {})
    assert other.recover_abandoned() == ()
    with pytest.raises(ResourceBudgetError, match="only an abandoned"):
        other.reconcile_abandoned(reservation.request_id, actor="operator", reason="still active", evidence_refs=("trace",))
    with pytest.raises(ResourceBudgetError, match="another owner"):
        other.settle(reservation, usage=None, complete=False, outcome="failed")
    owner.settle(reservation, usage=None, complete=False, outcome="failed")


def test_numeric_settlement_is_pure_ledger_arithmetic_and_never_invents_usage(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy())
    reservation = budget.reserve(_messages(), _config(), {})
    # Caller-authored accounting input, not a provider response or simulated run.
    budget.settle(reservation, usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                  complete=True, outcome="completed")
    row = json.loads(budget.path.read_text())["requests"][reservation.request_id]
    assert row["charged_tokens"] == 15 and row["charged_cost"] == pytest.approx(20 / 1_000_000)


@pytest.mark.asyncio
async def test_real_refused_connection_keeps_conservative_reservation_in_ancestor_scope(tmp_path: Path) -> None:
    from openai import APIConnectionError

    attempts: list[str] = []
    config = _config()
    config.attempt_observer = lambda kind, _data: attempts.append(kind)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        provider = CustomEndpointProvider(api_key="local-failure-only", base_url=f"http://127.0.0.1:{reserved.getsockname()[1]}/v1")
        try:
            with run_resource_scope(tmp_path / "parent"):
                with run_resource_scope(tmp_path / "child"):
                    with pytest.raises(APIConnectionError):
                        await guarded_complete(provider, _messages(), config, run_root=tmp_path / "child")
        finally:
            await provider.close()
    budget_path = tmp_path / "parent/resources/model_budget.v1.json"
    state = json.loads(budget_path.read_text())
    assert len(state["requests"]) == 1
    row = next(iter(state["requests"].values()))
    assert row["status"] == "failed" and not row["usage_complete"]
    assert row["charged_tokens"] == row["reserved_tokens"] and row["usage"] is None
    assert not (tmp_path / "child/resources/model_budget.v1.json").exists()
    assert attempts == ["sdk_attempt_started", "sdk_attempt_failed"]
