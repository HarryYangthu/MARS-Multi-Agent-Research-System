"""Pure admission checks and actual refused TCP requests; no provider doubles."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import socket

import pytest

from app.harness.llm.accounting import ResourceBudgetError, guarded_complete
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message, public_endpoint_url
from app.harness.llm.research_accounting import ContractModelBudget, bounded_contract_config
from app.harness.runtime.research_execution_scope import (
    ResearchExecutionScope, bind_research_execution, current_research_execution,
)
from tests.unit.test_research_budget_ledger import _budget, _setup


def test_frozen_contract_cannot_fall_back_to_global_budget(tmp_path: Path) -> None:
    ledger, run = _setup(tmp_path)
    with pytest.raises(ValueError, match="host-bound"):
        current_research_execution(run.root)
    (run.root / "input/research_task.v1.json").unlink()
    with pytest.raises(ValueError, match="host-bound"):
        current_research_execution(run.root)
    assert not (run.root / "resources/model_budget.v1.json").exists()
    assert ledger.journal.path.is_file()


def test_scope_requires_initialized_ledger_and_preserves_identity(tmp_path: Path) -> None:
    ledger, run = _setup(tmp_path / "one", initialize=False)
    scope = ResearchExecutionScope(ledger, "idea", "call-one")
    with pytest.raises(ValueError, match="initialization"):
        with bind_research_execution(scope):
            pass
    ledger.initialize()
    other, other_run = _setup(tmp_path / "two")
    with bind_research_execution(scope):
        assert current_research_execution(run.root) is scope
        with pytest.raises(ValueError, match="another run"):
            current_research_execution(other_run.root)
        with pytest.raises(ValueError, match="replace"):
            with bind_research_execution(ResearchExecutionScope(other, "coding", "call-two")):
                pass


def test_sdk_attempts_use_frozen_limit_and_correlation_cannot_grant_coding(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, budget=_budget(operation_retries=0, request_output_tokens=128,
                                              coding_output_tokens=256))
    scope = ResearchExecutionScope(ledger, "idea", "first")
    config = LLMConfig(provider="openai", model="test-admission", max_tokens=129, max_retries=3)
    bounded = bounded_contract_config(scope, config)
    assert config.max_retries == 3 and bounded.max_retries == 0
    budget = ContractModelBudget(scope, endpoint="http://127.0.0.1:1/v1")
    with pytest.raises(ResourceBudgetError, match="request_output_limit"):
        budget.reserve([Message("user", "hello")], bounded, {"agent": "coding", "stage": "coding"})
    assert ledger.snapshot().used["model_requests"] == 0
    coding = ContractModelBudget(ResearchExecutionScope(ledger, "coding", "second"), endpoint="http://127.0.0.1:1/v1")
    reservation = coding.reserve([Message("user", "hello")], bounded, {})
    assert reservation.attempts == 1
    assert ledger.snapshot().used["billed_output_tokens"] == 129


def test_identical_prompt_cannot_replay_through_another_invocation(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    config = LLMConfig(provider="openai", model="admission", max_tokens=32, max_retries=0)
    first = ContractModelBudget(ResearchExecutionScope(ledger, "idea", "first"), endpoint=None)
    first.reserve([Message("user", "same input")], config, {})
    second = ContractModelBudget(ResearchExecutionScope(ledger, "idea", "recovered"), endpoint=None)
    with pytest.raises(ResourceBudgetError, match="reconcile"):
        second.reserve([Message("user", "same input")], config, {})
    assert ledger.snapshot().used["model_requests"] == 1


def test_full_tool_schema_counts_toward_single_request_input(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, budget=_budget(request_input_tokens=128))
    budget = ContractModelBudget(ResearchExecutionScope(ledger, "idea", "first"), endpoint=None)
    config = LLMConfig(provider="openai", model="admission", max_tokens=32, max_retries=0,
                       tools=({"type": "function", "function": {"description": "x" * 256}},))
    with pytest.raises(ResourceBudgetError, match="request_input_limit"):
        budget.reserve([Message("user", "hi")], config, {})
    assert ledger.snapshot().used["model_requests"] == 0


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_partial_usage_is_a_lower_bound_even_when_total_is_unknown(tmp_path: Path, outcome: str) -> None:
    # Pure accounting-input regression, not a stand-in model or SDK execution.
    ledger, _ = _setup(tmp_path)
    budget = ContractModelBudget(ResearchExecutionScope(ledger, "idea", "lower-bound"), endpoint=None)
    config = LLMConfig(provider="custom", model="arithmetic", max_tokens=8, max_retries=0)
    reservation = budget.reserve([Message("user", "arithmetic input")], config, {})
    budget.settle(reservation, usage={"prompt_tokens": 1, "completion_tokens": 80, "total_tokens": 81},
                  complete=False, outcome=outcome, sdk_attempts=1, attempts_complete=True)
    snapshot = ledger.snapshot()
    assert snapshot.used["billed_output_tokens"] == 80 and snapshot.reservation_overrun
    assert snapshot.used["input_tokens"] is not None and snapshot.used["input_tokens"] > 1
    with pytest.raises(ResourceBudgetError, match="reservation_overrun"):
        budget.reserve([Message("user", "different arithmetic input")], config, {})


@pytest.mark.asyncio
async def test_real_sdk_connection_refusal_retains_request_and_tokens(tmp_path: Path) -> None:
    ledger, run = _setup(tmp_path, budget=_budget(operation_retries=0))
    scope = ResearchExecutionScope(ledger, "idea", "real-refused-connection")
    # Reserve an actual local TCP endpoint without listening: the kernel refuses
    # the SDK connection. No HTTP server or fabricated completion is involved.
    endpoint = socket.socket()
    endpoint.bind(("127.0.0.1", 0))
    endpoint_url = f"http://127.0.0.1:{endpoint.getsockname()[1]}/private-path-marker/v1?auth=private-query-marker&sig=private-signature-marker"
    assert "private-query-marker" not in public_endpoint_url(endpoint_url)
    assert "private-signature-marker" not in public_endpoint_url(endpoint_url)
    provider = CustomEndpointProvider(api_key="unused-local-negative-test", base_url=endpoint_url)
    config = LLMConfig(provider="custom", model="unreachable-test", max_tokens=32,
        max_retries=3, request_timeout_seconds=0.5, retry_base_delay_seconds=0)
    try:
        with bind_research_execution(scope):
            from openai import APIConnectionError
            with pytest.raises(APIConnectionError):
                await guarded_complete(provider, [Message("user", "actual negative request")],
                    config, run_root=run.root)
            with pytest.raises(ResourceBudgetError, match="reconcile"):
                await guarded_complete(provider, [Message("user", "actual negative request")],
                    config, run_root=run.root)
    finally:
        endpoint.close()
        await provider.close()
    snapshot = ledger.snapshot()
    assert snapshot.used["model_requests"] == 1
    assert snapshot.used["billed_output_tokens"] == 32
    assert snapshot.used["model_cost_micro_cny"] is None
    assert len(snapshot.unknown_reservations) == 1 and snapshot.activity_us > 0
    receipts = list((run.root / "resources/contract_models").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["max_sdk_attempts"] == receipt["observed_sdk_attempts"] == 1
    assert receipt["usage"] is None and not receipt["usage_complete"]
    assert not (run.root / "resources/model_budget.v1.json").exists()
    assert "actual negative request" not in receipts[0].read_text()
    assert "private-path-marker" not in receipts[0].read_text()
    assert "private-query-marker" not in receipts[0].read_text()
    assert "private-signature-marker" not in receipts[0].read_text()


@pytest.mark.asyncio
async def test_real_repeated_connection_error_stops_before_third_attempt(tmp_path: Path) -> None:
    ledger, run = _setup(tmp_path, budget=_budget(operation_retries=2, repeated_error_limit=2))
    endpoint = socket.socket()
    endpoint.bind(("127.0.0.1", 0))
    provider = CustomEndpointProvider(api_key="unused-local-negative-test",
        base_url=f"http://127.0.0.1:{endpoint.getsockname()[1]}/v1")
    try:
        with bind_research_execution(ResearchExecutionScope(ledger, "idea", "real-repeated-errors")):
            with pytest.raises(ResourceBudgetError, match="repeated_error_limit"):
                await guarded_complete(provider, [Message("user", "two real refusals")],
                    LLMConfig(provider="custom", model="unreachable", max_tokens=32, max_retries=2,
                        request_timeout_seconds=0.5, retry_base_delay_seconds=0), run_root=run.root)
    finally:
        endpoint.close()
        await provider.close()
    receipt = json.loads(next((run.root / "resources/contract_models").glob("*.json")).read_text())
    assert receipt["observed_sdk_attempts"] == 2
    assert receipt["max_sdk_attempts"] == ledger.snapshot().used["model_requests"] == 3
    assert len(ledger.snapshot().unknown_reservations) == 1


@pytest.mark.asyncio
async def test_scope_is_inherited_by_children_and_removed_on_exit(tmp_path: Path) -> None:
    ledger, run = _setup(tmp_path)
    scope = ResearchExecutionScope(ledger, "idea", "owner")

    async def child() -> ResearchExecutionScope | None:
        await asyncio.sleep(0)
        return current_research_execution(run.root)

    with bind_research_execution(scope):
        assert await asyncio.create_task(child()) is scope
    with pytest.raises(ValueError, match="host-bound"):
        await child()


def test_config_validation_does_not_mutate_caller(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    scope = ResearchExecutionScope(ledger, "idea", "owner")
    config = LLMConfig(provider="openai", model="admission")
    for changed in (replace(config, max_retries=-1), replace(config, max_tokens=0),
                    replace(config, request_timeout_seconds=float("nan")),
                    replace(config, retry_base_delay_seconds=float("inf"))):
        with pytest.raises(ResourceBudgetError):
            bounded_contract_config(scope, changed)
