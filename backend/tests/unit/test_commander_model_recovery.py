"""Real lock/process/ledger recovery; no generated model or tool success."""
from __future__ import annotations

import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import socket
import time
from typing import Any

import pytest
import yaml

from app.bridge.commander_recovery import recover_interrupted_model_requests
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.harness.llm.accounting import RunModelBudget, guarded_complete
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message


def _policy(root: Path, *, seconds: float = 60) -> dict[str, Any]:
    policy = {"schema": "runtime.resources.v1", "limits": {
        "max_model_requests": 32, "max_total_tokens": 100_000, "max_parallel_model_calls": 3,
        "max_elapsed_seconds": seconds, "max_cost": None,
    }, "prices": {"openai/test-model": {"input_per_million": 1, "output_per_million": 2}}, "currency": "USD"}
    directory = root / "resources"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model_budget.policy.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    return policy


def _config() -> LLMConfig:
    return LLMConfig(provider="openai", model="test-model", max_tokens=16,
                     max_retries=0, request_timeout_seconds=0.1)


def _session() -> CommanderSession:
    session = CommanderSession(conv_id="conv_recovery", project="test7")
    session.add(ChatMessage(role="user", content="继续已有任务", id="previous-turn"))
    return session


def _correlation(session: CommanderSession, **changes: Any) -> dict[str, Any]:
    return {"trace_id": session.conv_id, "node_id": "commander_native",
            "turn_id": "previous-turn", **changes}


def _crash(root: str, correlation: dict[str, Any], deadline: float | None, channel: Connection) -> None:
    budget = RunModelBudget(Path(root), active_model_time=True)
    reservation = budget.reserve([Message("user", "Real reservation; no model response is supplied.")],
                                 _config(), correlation, local_deadline_seconds=deadline)
    channel.send(reservation.request_id)
    channel.close()
    os._exit(0)


def _orphan(root: Path, correlation: dict[str, Any], *, deadline: float | None = 1.0) -> str:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_crash, args=(str(root), correlation, deadline, sender))
    child.start()
    sender.close()
    try:
        assert receiver.poll(15), "real reservation process did not report its ledger ID"
        identifier = str(receiver.recv())
    finally:
        receiver.close()
        child.join(timeout=15)
    assert child.exitcode == 0
    return identifier


def _state(root: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((root / "resources/model_budget.v1.json").read_text())
    return value


def test_real_process_death_recovers_only_this_conversation_without_refunding_usage(tmp_path: Path) -> None:
    _policy(tmp_path)
    session = _session()
    identifier = _orphan(tmp_path, _correlation(session))
    before = _state(tmp_path)["requests"][identifier]
    recovered = recover_interrupted_model_requests(tmp_path, session)
    assert recovered["status"] == "recovered" and recovered["recovered_request_ids"] == [identifier]
    row = _state(tmp_path)["requests"][identifier]
    assert row["status"] == "abandoned" and row["usage"] is None and not row["usage_complete"]
    for name in ("charged_tokens", "charged_input_tokens", "charged_output_tokens", "charged_cost", "charged_attempts"):
        assert row[name] == before[name]
    assert row["reconciliation"]["actor"] == "mars-conversation-recovery"
    assert row["reconciliation"]["evidence_refs"]
    unchanged = (tmp_path / "resources/model_budget.v1.json").read_bytes()
    assert recover_interrupted_model_requests(tmp_path, session)["status"] == "idle"
    assert (tmp_path / "resources/model_budget.v1.json").read_bytes() == unchanged
    fresh = RunModelBudget(tmp_path, active_model_time=True)
    next_reservation = fresh.reserve([Message("user", "Independent next model request")], _config(), _correlation(session))
    assert next_reservation.request_id != identifier
    fresh.settle(next_reservation, usage=None, complete=False, outcome="cancelled")


def test_live_file_lease_is_never_acknowledged_by_recovery(tmp_path: Path) -> None:
    _policy(tmp_path)
    session = _session()
    owner = RunModelBudget(tmp_path, active_model_time=True)
    reservation = owner.reserve([Message("user", "Active owner")], _config(), _correlation(session))
    before = owner.path.read_bytes()
    try:
        receipt = recover_interrupted_model_requests(tmp_path, session)
        assert receipt["status"] == "blocked" and receipt["error_code"] == "model_request_still_running"
        assert owner.path.read_bytes() == before
    finally:
        owner.settle(reservation, usage=None, complete=False, outcome="cancelled")


@pytest.mark.parametrize("changes", [
    {"trace_id": "another-conversation"}, {"node_id": "coding"}, {"turn_id": "another-user-turn"},
])
def test_foreign_or_unverifiable_model_request_is_not_reconciled(tmp_path: Path, changes: dict[str, Any]) -> None:
    _policy(tmp_path)
    session = _session()
    _orphan(tmp_path, _correlation(session, **changes))
    ledger = tmp_path / "resources/model_budget.v1.json"
    before = ledger.read_bytes()
    result = recover_interrupted_model_requests(tmp_path, session)
    assert result["status"] == "blocked" and result["error_code"] == "model_recovery_scope_mismatch"
    assert ledger.read_bytes() == before


def test_unknown_mutation_does_not_stop_model_recovery_or_settle_any_action(tmp_path: Path) -> None:
    _policy(tmp_path)
    session = _session()
    identifier = _orphan(tmp_path, _correlation(session))
    journal = tmp_path / "action_journal.json"
    journal.write_text(json.dumps({"schema": "commander.action_journal.v1", "entries": [
        {"id": "unconfirmed-write", "tool": "run.start", "args": {"run_id": "old-run"},
         "turn_id": "previous-turn", "mutation": True, "state": "running"}]}), encoding="utf-8")
    before = journal.read_bytes()
    result = recover_interrupted_model_requests(tmp_path, session)
    assert result["status"] == "recovered" and result["recovered_request_ids"] == [identifier]
    assert result["pending_action_ids"] == ["unconfirmed-write"]
    assert journal.read_bytes() == before


def test_absent_model_ledger_does_not_block_observation_of_an_unknown_operation(tmp_path: Path) -> None:
    journal = tmp_path / "action_journal.json"
    journal.write_text(json.dumps({"entries": [{"id": "write", "mutation": True, "state": "running"}]}))
    before = journal.read_bytes()
    assert recover_interrupted_model_requests(tmp_path, _session())["status"] == "idle"
    assert journal.read_bytes() == before and not (tmp_path / "resources").exists()


def test_corrupt_journal_is_an_advisory_and_is_not_replaced_by_model_recovery(tmp_path: Path) -> None:
    _policy(tmp_path)
    session = _session()
    _orphan(tmp_path, _correlation(session))
    journal = tmp_path / "action_journal.json"
    journal.write_text("{incomplete", encoding="utf-8")
    result = recover_interrupted_model_requests(tmp_path, session)
    assert result["status"] == "recovered" and result["action_journal_warning"]
    assert journal.read_text() == "{incomplete"


def test_enforced_deadline_excludes_post_death_idle_but_keeps_all_token_and_cost_charges(tmp_path: Path) -> None:
    _policy(tmp_path, seconds=0.2)
    session = _session()
    identifier = _orphan(tmp_path, _correlation(session), deadline=0.02)
    time.sleep(0.25)
    before = _state(tmp_path)["requests"][identifier]
    receipt = recover_interrupted_model_requests(tmp_path, session)
    assert receipt["status"] == "recovered"
    assert 0 < receipt["model_activity_seconds_remaining"] <= 0.2
    after = _state(tmp_path)["requests"][identifier]
    assert after["owner_lost_at"] - after["started_at"] > 0.2
    assert after["local_call_deadline_at"] - after["started_at"] == pytest.approx(0.02, abs=1e-6)
    assert after["charged_tokens"] == before["charged_tokens"] and after["charged_cost"] == before["charged_cost"]


def test_legacy_record_without_deadline_keeps_its_original_conservative_activity_charge(tmp_path: Path) -> None:
    _policy(tmp_path, seconds=0.1)
    session = _session()
    identifier = _orphan(tmp_path, _correlation(session), deadline=None)
    time.sleep(0.15)
    before = _state(tmp_path)["requests"][identifier]
    receipt = recover_interrupted_model_requests(tmp_path, session)
    assert receipt["status"] == "blocked" and receipt["error_code"] == "model_activity_budget_exhausted"
    after = _state(tmp_path)["requests"][identifier]
    assert "local_call_deadline_at" not in after
    assert after["started_at"] == before["started_at"]
    assert after["charged_tokens"] == before["charged_tokens"] and not after["usage_complete"]


@pytest.mark.asyncio
async def test_real_refused_model_connection_records_the_deadline_actually_enforced_by_harness(tmp_path: Path) -> None:
    from openai import APIConnectionError

    _policy(tmp_path)
    with socket.socket() as unserved:
        unserved.bind(("127.0.0.1", 0))
        provider = CustomEndpointProvider(api_key="real-refused-connection-only",
            base_url=f"http://127.0.0.1:{unserved.getsockname()[1]}/v1")
        try:
            with pytest.raises(APIConnectionError):
                await guarded_complete(provider, [Message("user", "Failure transport verification")], _config(),
                    run_root=tmp_path, active_model_time=True, correlation=_correlation(_session()))
        finally:
            await provider.close()
    row = next(iter(_state(tmp_path)["requests"].values()))
    assert row["local_call_deadline_at"] > row["started_at"]
    assert row["local_call_deadline_seconds"] > 0
    assert row["finished_at"] <= row["local_call_deadline_at"]
    assert row["status"] == "failed" and row["usage"] is None and not row["usage_complete"]


def test_targeted_orphan_probe_does_not_modify_another_owners_reservation(tmp_path: Path) -> None:
    _policy(tmp_path)
    session = _session()
    owner = RunModelBudget(tmp_path)
    foreign = owner.reserve([Message("user", "Another actual live reservation")],
                            _config(), _correlation(session, node_id="coding"))
    try:
        first = _orphan(tmp_path, _correlation(session))
        budget = RunModelBudget(tmp_path)
        before = _state(tmp_path)["requests"][foreign.request_id]
        assert budget.recover_abandoned(request_ids=(first,)) == (first,)
        assert _state(tmp_path)["requests"][foreign.request_id] == before
    finally:
        owner.settle(foreign, usage=None, complete=False, outcome="cancelled")
