"""Actual ledger/concurrency/crash/refusal checks; no provider success substitutes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import socket
from typing import Any

import pytest

from app.harness.llm.accounting import (
    Reservation,
    ResourceBudgetError,
    ResourceReconciliationRequired,
    RunModelBudget,
    guarded_complete,
)
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message


def _policy(max_attempts: int) -> dict[str, Any]:
    return {"schema": "runtime.resources.v1", "currency": "CNY", "prices": {}, "limits": {
        "max_model_requests": max_attempts, "max_total_tokens": 100_000,
        "max_parallel_model_calls": 10, "max_elapsed_seconds": 60, "max_cost": None,
    }}


def _config(retries: int = 2) -> LLMConfig:
    return LLMConfig(provider="custom", model="connection-refusal-test", max_tokens=8,
                     max_retries=retries, request_timeout_seconds=0.1, retry_base_delay_seconds=0)


def _messages() -> list[Message]:
    return [Message(role="user", content="Accounting test; no model success requested.")]


def _rows(budget: RunModelBudget) -> dict[str, dict[str, Any]]:
    value: dict[str, dict[str, Any]] = json.loads(budget.path.read_text())["requests"]
    return value


def _crash_after_reservation(root: str, connection: Connection) -> None:
    owner = RunModelBudget(Path(root), configuration=_policy(3))
    reserved = owner.reserve(_messages(), _config(), {})
    connection.send(reserved.request_id)
    connection.close()
    os._exit(0)


def test_all_retry_attempts_are_reserved_before_sending(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy(2))
    with pytest.raises(ResourceBudgetError, match="reserve all SDK attempts"):
        budget.reserve(_messages(), _config(), {})
    assert not budget.path.exists()
    reserved = budget.reserve(_messages(), _config(retries=1), {})
    assert reserved.attempts == 2
    assert _rows(budget)[reserved.request_id]["charged_attempts"] == 2
    budget.settle(reserved, usage=None, complete=False, outcome="cancelled")
    with pytest.raises(ResourceBudgetError, match="model-request budget"):
        budget.reserve(_messages(), _config(retries=0), {})


def test_settlement_refunds_only_fully_observed_attempt_counts(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy(4))
    first = budget.reserve(_messages(), _config(), {})
    # Caller-authored ledger arithmetic only. No provider result or run success.
    budget.settle(first, usage=None, complete=False, outcome="failed", sdk_attempts=1,
                  attempts_complete=True)
    first_row = _rows(budget)[first.request_id]
    assert first_row["charged_attempts"] == first_row["observed_attempts"] == 1
    assert first_row["attempts_complete"] is True
    assert first_row["charged_tokens"] == first_row["reserved_tokens"]
    second = budget.reserve(_messages(), _config(), {})
    budget.settle(second, usage=None, complete=False, outcome="cancelled", sdk_attempts=1,
                  attempts_complete=False)
    second_row = _rows(budget)[second.request_id]
    assert second_row["charged_attempts"] == second_row["max_sdk_attempts"] == 3
    assert second_row["observed_attempts"] == 1
    assert second_row["attempts_complete"] is False
    with pytest.raises(ResourceBudgetError, match="model-request budget"):
        budget.reserve(_messages(), _config(retries=0), {})


def test_no_observation_never_refunds_a_retry_reservation(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy(3))
    reserved = budget.reserve(_messages(), _config(), {})
    budget.settle(reserved, usage=None, complete=False, outcome="failed", sdk_attempts=0,
                  attempts_complete=True)
    row = _rows(budget)[reserved.request_id]
    assert row["charged_attempts"] == 3 and row["attempts_complete"] is False
    with pytest.raises(ResourceBudgetError, match="model-request budget"):
        budget.reserve(_messages(), _config(retries=0), {})


def test_concurrent_reservations_cannot_oversubscribe_attempt_limit(tmp_path: Path) -> None:
    policy = _policy(5)
    owners = [RunModelBudget(tmp_path, configuration=policy) for _ in range(12)]

    def reserve(index: int) -> tuple[int, Reservation | None]:
        try:
            return index, owners[index].reserve(_messages(), _config(retries=1), {})
        except ResourceBudgetError:
            return index, None

    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(reserve, range(12)))
    admitted = [(index, item) for index, item in results if item is not None]
    assert len(admitted) == 2
    assert sum(row["charged_attempts"] for row in _rows(owners[0]).values()) == 4
    for index, reservation in admitted:
        owners[index].settle(reservation, usage=None, complete=False, outcome="failed")
    last = owners[0].reserve(_messages(), _config(retries=0), {})
    owners[0].settle(last, usage=None, complete=False, outcome="cancelled")
    assert sum(row["charged_attempts"] for row in _rows(owners[0]).values()) == 5


@pytest.mark.parametrize("ceiling_missing", [False, True])
def test_legacy_rows_keep_their_retry_ceiling(tmp_path: Path, ceiling_missing: bool) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy(4))
    first = budget.reserve(_messages(), _config(), {})
    budget.settle(first, usage=None, complete=False, outcome="failed", sdk_attempts=1,
                  attempts_complete=True)
    state = json.loads(budget.path.read_text())
    row = state["requests"][first.request_id]
    for name in ("charged_attempts", "observed_attempts", "attempts_complete"):
        del row[name]
    if ceiling_missing:
        del row["max_sdk_attempts"]
    budget.path.write_text(json.dumps(state))
    with pytest.raises(ResourceBudgetError, match="model-request budget"):
        budget.reserve(_messages(), _config(retries=1), {})
    if ceiling_missing:
        with pytest.raises(ResourceBudgetError, match="model-request budget"):
            budget.reserve(_messages(), _config(retries=0), {})
    else:
        final = budget.reserve(_messages(), _config(retries=0), {})
        budget.settle(final, usage=None, complete=False, outcome="failed")


@pytest.mark.parametrize("charged,known", [(0, True), (True, True), (4, True), (1, False)])
def test_corrupt_attempt_records_fail_closed(tmp_path: Path, charged: Any, known: bool) -> None:
    budget = RunModelBudget(tmp_path, configuration=_policy(8))
    first = budget.reserve(_messages(), _config(), {})
    budget.settle(first, usage=None, complete=False, outcome="cancelled")
    state = json.loads(budget.path.read_text())
    state["requests"][first.request_id].update(charged_attempts=charged, attempts_complete=known)
    budget.path.write_text(json.dumps(state))
    with pytest.raises(ResourceBudgetError, match="model-attempt reservation"):
        budget.reserve(_messages(), _config(retries=0), {})


def test_crash_and_reconciliation_preserve_full_attempt_reservation(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_crash_after_reservation, args=(str(tmp_path), child))
    process.start()
    child.close()
    try:
        assert parent.poll(15)
        request_id = parent.recv()
        process.join(timeout=15)
        assert process.exitcode == 0
    finally:
        parent.close()
        if process.is_alive():
            process.kill()
            process.join(timeout=15)
    budget = RunModelBudget(tmp_path, configuration=_policy(3))
    with pytest.raises(ResourceReconciliationRequired):
        budget.reserve(_messages(), _config(retries=0), {})
    budget.reconcile_abandoned(request_id, actor="test-operator", reason="Observed real process death",
                               evidence_refs=("resources/model_budget.v1.json",))
    row = _rows(budget)[request_id]
    assert row["charged_attempts"] == 3 and row["attempts_complete"] is False
    with pytest.raises(ResourceBudgetError, match="model-request budget"):
        budget.reserve(_messages(), _config(retries=0), {})


@pytest.mark.asyncio
async def test_real_connection_refusal_records_every_actual_retry_attempt(tmp_path: Path) -> None:
    from openai import APIConnectionError

    events: list[str] = []
    config = _config()
    config.attempt_observer = lambda kind, _data: events.append(kind)
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        provider = CustomEndpointProvider(api_key="local-failure-only", base_url=f"http://127.0.0.1:{unavailable.getsockname()[1]}/v1")
        try:
            with pytest.raises(APIConnectionError):
                await guarded_complete(provider, _messages(), config, run_root=tmp_path)
        finally:
            await provider.close()
    budget = RunModelBudget(tmp_path)
    row = next(iter(_rows(budget).values()))
    assert events.count("sdk_attempt_started") == events.count("sdk_attempt_failed") == 3
    assert row["charged_attempts"] == row["observed_attempts"] == row["max_sdk_attempts"] == 3
    assert row["attempts_complete"] is True
    assert row["usage_complete"] is False and row["charged_tokens"] == row["reserved_tokens"]


@pytest.mark.asyncio
async def test_cancelled_real_transport_keeps_max_attempts_despite_terminal_cancel_event(tmp_path: Path) -> None:
    from app.harness.llm.anthropic_provider import AnthropicProvider

    started = asyncio.Event()
    events: list[str] = []
    config = _config()
    config.request_timeout_seconds = 10

    def observe(kind: str, _data: dict[str, Any]) -> None:
        events.append(kind)
        if kind == "sdk_attempt_started":
            started.set()

    config.attempt_observer = observe
    # Real TCP listener with no HTTP response: only cancellation is exercised.
    # This never provides a substitute model/tool/service success response.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        unavailable.listen()
        provider = AnthropicProvider(api_key="local-failure-only", base_url=f"http://127.0.0.1:{unavailable.getsockname()[1]}")
        pending = asyncio.create_task(guarded_complete(provider, _messages(), config, run_root=tmp_path))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
            await provider.close()
    row = next(iter(_rows(RunModelBudget(tmp_path)).values()))
    assert events.count("sdk_attempt_started") == 1
    assert events.count("sdk_attempt_failed") == 1
    assert row["status"] == "cancelled" and row["observed_attempts"] == 1
    assert row["charged_attempts"] == 3 and row["attempts_complete"] is False
