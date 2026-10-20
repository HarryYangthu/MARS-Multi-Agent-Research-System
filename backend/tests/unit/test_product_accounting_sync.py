"""Real temporary ledgers for Product's request-limit policy; no model calls."""
from __future__ import annotations

import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.llm.accounting import (
    ResourceBudgetError,
    RunModelBudget,
    model_request_capacity,
)
from app.harness.llm.provider_base import LLMConfig, MAX_LLM_RETRIES, Message


def _policy(**limits: Any) -> dict[str, Any]:
    return {
        "schema": "runtime.resources.v1",
        "currency": "USD",
        "prices": {
            "openai/ledger-only": {"input_per_million": 1, "output_per_million": 2},
        },
        "limits": {
            "max_model_requests": 10,
            "max_total_tokens": 100_000,
            "max_input_tokens": 90_000,
            "max_billed_output_tokens": 10_000,
            "max_parallel_model_calls": 3,
            "max_elapsed_seconds": 60,
            "max_cost": None,
            **limits,
        },
    }


def _config(retries: int = 2) -> LLMConfig:
    return LLMConfig(provider="openai", model="ledger-only", max_tokens=16,
                     max_retries=retries)


def _messages() -> list[Message]:
    return [Message(role="user", content="Temporary ledger arithmetic; no provider request.")]


def _state(budget: RunModelBudget) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(budget.path.read_text())
    return state


def _settled_budget(root: Path, policy: dict[str, Any]) -> RunModelBudget:
    budget = RunModelBudget(root, configuration=policy, token_mode="limited")
    reservation = budget.reserve(_messages(), _config(0), {"task_id": "ledger-only"})
    budget.settle(reservation, usage=None, complete=False, outcome="cancelled")
    return budget


@pytest.mark.parametrize("limit,used,retries,required,available", [
    (5, 0, 2, 3, True),
    (5, 3, 2, 2, True),
    (5, 4, 2, 1, True),
    (5, 5, 2, 1, False),
    (5, 6, 2, 1, False),
    (None, 100, 2, 3, True),
    (None, 100, MAX_LLM_RETRIES + 10, MAX_LLM_RETRIES + 1, True),
    (5, 0, -1, 1, True),
])
def test_capacity_uses_remaining_attempts_without_raising_the_limit(
    limit: int | None, used: int, retries: int, required: int, available: bool,
) -> None:
    state = {
        "configuration": {"limits": {"max_model_requests": limit}},
        "requests": {
            str(index): {"max_sdk_attempts": 1, "charged_attempts": 1}
            for index in range(used)
        },
    }
    capacity = model_request_capacity(state, retries)
    assert (capacity.used, capacity.limit, capacity.required) == (used, limit, required)
    assert capacity.available is available
    if available:
        capacity.require()
    else:
        with pytest.raises(ResourceBudgetError, match="model-request budget"):
            capacity.require()


def test_remaining_slot_narrows_real_token_and_cost_reservations(tmp_path: Path) -> None:
    # Measure one reservation using the real ledger, then put exactly two such
    # reservations under the token, component, monetary and attempt ceilings.
    reference = _settled_budget(tmp_path / "reference", _policy())
    single = next(iter(_state(reference)["requests"].values()))
    budget = RunModelBudget(tmp_path / "bounded", configuration=_policy(
        max_model_requests=2,
        max_total_tokens=2 * single["reserved_tokens"],
        max_input_tokens=2 * single["reserved_input_tokens"],
        max_billed_output_tokens=2 * single["reserved_output_tokens"],
        max_cost=2 * single["reserved_cost"],
    ), token_mode="limited")
    first = budget.reserve(_messages(), _config(0), {})
    budget.settle(first, usage=None, complete=False, outcome="cancelled")
    last = budget.reserve(_messages(), _config(2), {})
    try:
        assert last.attempts == 1
        assert last.tokens == first.tokens == single["reserved_tokens"]
        assert last.cost == first.cost == single["reserved_cost"]
        state = _state(budget)
        row = state["requests"][last.request_id]
        assert row["max_sdk_attempts"] == row["charged_attempts"] == 1
        assert row["reserved_input_tokens"] == single["reserved_input_tokens"]
        assert row["reserved_output_tokens"] == single["reserved_output_tokens"]
        for charge, ceiling in (
            ("charged_attempts", "max_model_requests"),
            ("charged_tokens", "max_total_tokens"),
            ("charged_input_tokens", "max_input_tokens"),
            ("charged_output_tokens", "max_billed_output_tokens"),
            ("charged_cost", "max_cost"),
        ):
            assert sum(item[charge] for item in state["requests"].values()) == state["configuration"]["limits"][ceiling]
    finally:
        budget.settle(last, usage=None, complete=False, outcome="cancelled")


def test_exhausted_real_ledger_refuses_without_adding_or_refunding_rows(tmp_path: Path) -> None:
    budget = _settled_budget(tmp_path, _policy(max_model_requests=1))
    before = budget.path.read_bytes()
    with pytest.raises(ResourceBudgetError, match="model-request budget"):
        budget.reserve(_messages(), _config(2), {})
    assert budget.path.read_bytes() == before
    assert len(_state(budget)["requests"]) == 1


@pytest.mark.parametrize("new_limit", [1, 7, None])
def test_request_limit_change_preserves_time_usage_and_revision_history(
    tmp_path: Path, new_limit: int | None,
) -> None:
    owner = _settled_budget(tmp_path, _policy(max_model_requests=3))
    owner.begin_revision(invocation_id="ledger-revision", reason="Explicit ledger-only revision")
    before = owner.recovery_snapshot()
    updated = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=new_limit),
                             token_mode="limited")
    updated.change_request_limit(actor="test-operator", reason="Explicit request-limit change")
    after = updated.recovery_snapshot()
    assert after["started_at"] == before["started_at"]
    assert after["revision_started_at"] == before["revision_started_at"]
    assert after["requests"] == before["requests"]
    assert after["revisions"] == before["revisions"]
    assert after["configuration"] == updated.configuration
    receipt = after["policy_changes"][0]
    assert receipt["kind"] == "model_request_limit"
    assert receipt["actor"] == "test-operator"
    assert receipt["reason"] == "Explicit request-limit change"
    assert receipt["timestamp"] >= before["revision_started_at"]
    assert receipt["previous_configuration"] == before["configuration"]
    assert receipt["previous_configuration_sha256"] == before["configuration_sha256"]
    assert receipt["configuration_sha256"] == updated.configuration_hash
    assert RunModelBudget(tmp_path, configuration=updated.configuration).recovery_snapshot() == after
    with pytest.raises(ResourceBudgetError, match="policy changed"):
        owner.recovery_snapshot()


@pytest.mark.parametrize("actor,reason", [
    ("", "explicit change"), (" \t", "explicit change"),
    ("operator", ""), ("operator", " \n"),
])
def test_request_limit_change_requires_actor_and_reason(
    tmp_path: Path, actor: str, reason: str,
) -> None:
    owner = _settled_budget(tmp_path, _policy(max_model_requests=3))
    updated = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=7))
    before = owner.path.read_bytes()
    with pytest.raises(ValueError, match="actor and reason"):
        updated.change_request_limit(actor=actor, reason=reason)
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("other_field", [
    "max_total_tokens", "max_elapsed_seconds", "max_parallel_model_calls",
    "max_cost", "currency", "prices",
])
def test_request_limit_change_cannot_modify_any_other_policy_field(
    tmp_path: Path, other_field: str,
) -> None:
    owner = _settled_budget(tmp_path, _policy(max_model_requests=3))
    policy = _policy(max_model_requests=7)
    if other_field == "currency":
        policy["currency"] = "CNY"
    elif other_field == "prices":
        policy["prices"]["openai/ledger-only"]["input_per_million"] = 2
    elif other_field == "max_cost":
        policy["limits"][other_field] = 1
    else:
        policy["limits"][other_field] *= 2
    updated = RunModelBudget(tmp_path, configuration=policy)
    before = owner.path.read_bytes()
    with pytest.raises(ResourceBudgetError, match="cannot modify other resource limits"):
        updated.change_request_limit(actor="operator", reason="Request count only")
    assert owner.path.read_bytes() == before


def test_request_limit_change_refuses_a_real_active_lease(tmp_path: Path) -> None:
    owner = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=3))
    reservation = owner.reserve(_messages(), _config(0), {})
    try:
        assert owner.recover_abandoned() == ()
        updated = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=7))
        before = owner.path.read_bytes()
        with pytest.raises(ResourceBudgetError, match="active or unresolved"):
            updated.change_request_limit(actor="operator", reason="Request count only")
        assert owner.path.read_bytes() == before
    finally:
        owner.settle(reservation, usage=None, complete=False, outcome="cancelled")


def _reserve_and_exit(root: str, connection: Connection) -> None:
    owner = RunModelBudget(Path(root), configuration=_policy(max_model_requests=3))
    reservation = owner.reserve(_messages(), _config(0), {})
    connection.send(reservation.request_id)
    connection.close()
    os._exit(0)  # Actual owner death; no invented provider result or settlement.


def test_request_limit_change_refuses_a_real_unresolved_owner_loss(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_reserve_and_exit, args=(str(tmp_path), child))
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
    owner = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=3))
    assert owner.recover_abandoned() == (request_id,)
    before = owner.path.read_bytes()
    updated = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=7))
    with pytest.raises(ResourceBudgetError, match="active or unresolved"):
        updated.change_request_limit(actor="operator", reason="Request count only")
    assert owner.path.read_bytes() == before
    assert _state(owner)["requests"][request_id]["status"] == "reconciliation_required"


def test_identical_request_limit_policy_is_byte_for_byte_idempotent(tmp_path: Path) -> None:
    owner = _settled_budget(tmp_path, _policy(max_model_requests=3))
    before = owner.path.read_bytes()
    owner.change_request_limit(actor="operator", reason="No configuration change")
    assert owner.path.read_bytes() == before
    updated = RunModelBudget(tmp_path, configuration=_policy(max_model_requests=7))
    updated.change_request_limit(actor="operator", reason="Explicit change")
    changed = updated.path.read_bytes()
    updated.change_request_limit(actor="another-operator", reason="Repeat same policy")
    assert updated.path.read_bytes() == changed
    assert len(updated.recovery_snapshot()["policy_changes"]) == 1
