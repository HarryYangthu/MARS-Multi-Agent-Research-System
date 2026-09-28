"""Read-only real SQLite/file checks; authored envelopes are parser inputs only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import sqlite3
from typing import Any

import pytest

from app.bridge.research_results_usage import read_research_resources
from app.bridge.results_service import ResultReader, load_results_policy
from app.harness.llm.accounting import guarded_complete
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.runtime.research_budget_ledger import BudgetAmounts, BudgetReservation, BudgetSettlement, ResearchBudgetLedger
from app.harness.runtime.research_execution_scope import bind_research_execution
from app.storage.run_store import RunHandle, RunStore
from tests.unit.test_research_tool_accounting import setup


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def fixture(tmp_path: Path) -> tuple[RunHandle, ResearchBudgetLedger, ResultReader]:
    scope, execution, _ = setup(tmp_path)
    run = RunStore(scope.run_root.parent).get(scope.run_id)
    assert run is not None
    return run, execution.ledger, ResultReader(run, load_results_policy())


def authored_envelope(run: RunHandle, ledger: ResearchBudgetLedger, *, complete: bool = True,
                      unknown: bool = False, label: str = "one", identity_error: bool = False) -> Path:
    """Pure accounting evidence input; no provider/Completion/handler is invoked."""
    identity = {"task_sha256": ledger.task_sha256, "provider": "custom", "model": "authored-parser-envelope", "stage": "coding", "label": label}
    fingerprint = digest(identity)
    identifier = "model-" + fingerprint.removeprefix("sha256:")
    spec = BudgetReservation(reservation_id=identifier, operation_id=identifier, operation_fingerprint=fingerprint,
        kind="model", amounts=BudgetAmounts(model_requests=2, input_tokens=200, billed_output_tokens=20),
        request_input_tokens=100, request_output_tokens=10)
    assert ledger.reserve(spec).admitted
    receipt = {"schema": "runtime.contract_model_receipt.v1", "run_id": run.run_id,
        "task_sha256": ledger.task_sha256, "reservation_id": identifier, "identity": identity,
        "outcome": "completed", "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 8},
        "usage_complete": complete, "observed_sdk_attempts": 1, "attempts_complete": True, "max_sdk_attempts": 2,
        "cost_cny": None, "response_identity": {"provider": "custom", "model": "other" if identity_error else "authored-parser-envelope",
            "response_model_status": "consistent", "model_matches_requested": True, "provider_matches_requested": True}}
    path = run.root / "resources/contract_models" / (identifier + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt))
    if unknown:
        ledger.mark_unknown(identifier, reason="authored accounting uncertainty")
    else:
        amounts = BudgetAmounts(model_requests=1, input_tokens=3 if complete else 200, billed_output_tokens=5 if complete else 20)
        ledger.settle(identifier, BudgetSettlement(actual=amounts, outcome="success",
            evidence_refs=(path.relative_to(run.root).as_posix(),), evidence_fingerprint=digest(receipt)))
    return path


def read(reader: ResultReader) -> dict[str, Any]:
    result = read_research_resources(reader.run, read=reader.read)
    assert result is not None
    return result.resources


def test_authored_complete_envelope_numbers_require_sql_and_receipt_consistency(tmp_path: Path) -> None:
    run, ledger, reader = fixture(tmp_path)
    authored_envelope(run, ledger)
    value = read(reader)
    assert value["status"] == "recorded" and value["authority"] == "sqlite"
    assert value["model_requests"] == value["logical_records"] == 1
    assert value["request_count_scope"] == "logical_records"
    assert value["observed_sdk_attempts"] == value["charged_sdk_attempts"] == 1
    assert value["reserved_sdk_attempts"] == 2
    assert value["input_tokens"] == 3 and value["billed_output_tokens"] == 5
    assert value["usage_complete"] and value["cost"] is None


def test_incomplete_settlement_reservation_never_becomes_actual_tokens(tmp_path: Path) -> None:
    run, ledger, reader = fixture(tmp_path)
    authored_envelope(run, ledger, complete=False)
    value = read(reader)
    assert value["input_tokens"] is None and value["billed_output_tokens"] is None
    assert value["charged_input_tokens"] == 200 and value["charged_billed_output_tokens"] == 20
    assert value["observed_sdk_attempts"] == 1 and value["observed_attempts_complete"]
    assert value["verified_usage_records"] == 0 and not value["usage_complete"]


def test_unknown_receipt_without_sql_hash_cannot_establish_actual_usage(tmp_path: Path) -> None:
    run, ledger, reader = fixture(tmp_path)
    authored_envelope(run, ledger, unknown=True)
    value = read(reader)
    assert value["input_tokens"] is None and value["observed_sdk_attempts"] is None
    assert value["charged_sdk_attempts"] == 2 and value["unknown_reservations"] == 1
    assert value["open_activity_reservations"] == 1 and value["research_activity_seconds"] >= 0
    assert value["research_activity_remaining_seconds"] < ledger.budget.research_activity_seconds


@pytest.mark.parametrize("corruption", ["missing", "edited", "symbolic", "hardlink", "identity"])
def test_corrupt_receipt_only_removes_actual_claims_not_validated_charges(tmp_path: Path, corruption: str) -> None:
    run, ledger, reader = fixture(tmp_path)
    path = authored_envelope(run, ledger, identity_error=corruption == "identity")
    if corruption == "missing":
        path.unlink()
    elif corruption == "edited":
        raw = json.loads(path.read_text())
        raw["usage"]["prompt_tokens"] = 2
        path.write_text(json.dumps(raw))
    elif corruption in {"symbolic", "hardlink"}:
        outside = tmp_path / "receipt.json"
        path.rename(outside)
        if corruption == "symbolic":
            path.symlink_to(outside)
        else:
            path.hardlink_to(outside)
    value = read(reader)
    assert value["status"] == "recorded" and value["charged_sdk_attempts"] == 1
    assert value["input_tokens"] is None and value["observed_sdk_attempts"] is None
    assert value["invalid_model_receipts"] == 1


def test_mixed_rows_have_verified_subtotal_and_unknown_full_total(tmp_path: Path) -> None:
    run, ledger, reader = fixture(tmp_path)
    authored_envelope(run, ledger, label="one")
    authored_envelope(run, ledger, unknown=True, label="two")
    value = read(reader)
    assert value["logical_records"] == 2 and value["charged_sdk_attempts"] == 3
    assert value["input_tokens"] is None and value["observed_sdk_attempts"] is None
    assert value["verified_input_tokens_subtotal"] == 3 and value["verified_sdk_attempts_subtotal"] == 1
    assert value["verified_usage_records"] == value["unverified_usage_records"] == 1


@pytest.mark.parametrize("damage", ["contract", "options", "authority", "database"])
def test_sql_contract_damage_never_falls_back_to_legacy(tmp_path: Path, damage: str) -> None:
    run, ledger, reader = fixture(tmp_path)
    authored_envelope(run, ledger)
    legacy = run.root / "resources/model_budget.v1.json"
    legacy.write_text('{"must_not_be_read":true}')
    target = {"contract": "input/research_task.v1.json", "options": "input/run_request_options.v1.json",
              "authority": "run_state.authority.json", "database": "run_state.sqlite3"}[damage]
    (run.root / target).unlink()
    value = reader.resources()
    assert value["status"] == "invalid" and value["authority"] == "sqlite"
    assert value["input_tokens"] is None and value["charged_sdk_attempts"] is None
    assert not reader.sources


@pytest.mark.parametrize("marker", ["metadata", "options"])
def test_isolated_contract_marker_prevents_legacy_path(tmp_path: Path, marker: str) -> None:
    run = RunStore(tmp_path / "runs").create(task="marker", project="generic")
    if marker == "metadata":
        data = dict(run.meta, research_task_sha256="a" * 64)
        (run.root / "run_meta.json").write_text(json.dumps(data))
    else:
        (run.root / "input/run_request_options.v1.json").write_text(json.dumps({"extra": {"research_task_sha256": "a" * 64}}))
    value = read(ResultReader(run, load_results_policy()))
    assert value["status"] == "invalid"


def test_unmarked_legacy_run_is_the_only_fallback(tmp_path: Path) -> None:
    run = RunStore(tmp_path / "runs").create(task="legacy", project="generic")
    reader = ResultReader(run, load_results_policy())
    assert read_research_resources(run, read=reader.read) is None
    assert reader.resources()["status"] == "missing"


def test_reads_preserve_sqlite_bytes_mtime_and_all_run_files(tmp_path: Path) -> None:
    run, ledger, reader = fixture(tmp_path)
    authored_envelope(run, ledger, unknown=True)
    before = {path.relative_to(run.root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
              for path in run.root.rglob("*") if path.is_file()}
    first = read(reader)
    second = read(ResultReader(run, load_results_policy()))
    assert second["research_activity_seconds"] >= first["research_activity_seconds"]
    after = {path.relative_to(run.root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
             for path in run.root.rglob("*") if path.is_file()}
    assert before == after


@pytest.mark.asyncio
async def test_actual_sdk_connection_refusal_is_charged_and_usage_stays_unknown(tmp_path: Path) -> None:
    scope, execution, _ = setup(tmp_path, operation_retries=0)
    run = RunStore(scope.run_root.parent).get(scope.run_id)
    assert run is not None
    endpoint = socket.socket()
    endpoint.bind(("127.0.0.1", 0))  # Bound but not listening: real kernel refusal.
    provider = CustomEndpointProvider(api_key="unused-local-negative-test", base_url=f"http://127.0.0.1:{endpoint.getsockname()[1]}/v1")
    try:
        from openai import APIConnectionError
        with bind_research_execution(execution), pytest.raises(APIConnectionError):
            await guarded_complete(provider, [Message("user", "actual negative request")],
                LLMConfig(provider="custom", model="unreachable", max_tokens=32, max_retries=0,
                    request_timeout_seconds=0.5, retry_base_delay_seconds=0), run_root=run.root)
    finally:
        endpoint.close()
        await provider.close()
    value = ResultReader(run, load_results_policy()).resources()
    assert value["status"] == "recorded" and value["charged_sdk_attempts"] == 1
    assert value["input_tokens"] is None and value["billed_output_tokens"] is None
    assert value["observed_sdk_attempts"] is None and value["cost"] is None


def test_sql_budget_ignores_broken_regenerable_json_projection(tmp_path: Path) -> None:
    run, ledger, reader = fixture(tmp_path)
    authored_envelope(run, ledger)
    (run.root / "run_state.json").write_text("broken stale projection")
    value = read(reader)
    assert value["status"] == "recorded" and value["input_tokens"] == 3


def test_sql_request_marker_prevents_fallback_when_external_markers_are_lost(tmp_path: Path) -> None:
    run, _ledger, reader = fixture(tmp_path)
    run.meta.pop("research_task_sha256")
    (run.root / "run_meta.json").write_text(json.dumps(run.meta))
    (run.root / "input/run_request_options.v1.json").write_text('{"schema_id":"run_request_options.v1","extra":{}}')
    (run.root / "input/research_task.v1.json").unlink()
    value = read(reader)
    assert value["status"] == "invalid" and value["authority"] == "sqlite"


def test_real_prepared_contract_without_budget_extension_has_no_zero_usage_or_500(tmp_path: Path) -> None:
    from app.bridge.orchestrator import Orchestrator
    from app.bridge.research_run_service import create_research_run, load_run_research_contract
    prepared, _ledger, _reader = fixture(tmp_path / "source_contract")
    frozen = load_run_research_contract(prepared)
    assert frozen is not None
    owner = Orchestrator(run_store=RunStore(tmp_path / "not_initialized/runs"))
    run = create_research_run(owner, name="prepared-only", contract=frozen).run
    reader = ResultReader(run, load_results_policy())
    value = reader.resources()
    assert value["status"] == "invalid" and value["authority"] == "sqlite"
    assert value["logical_records"] is None and value["charged_sdk_attempts"] is None
    assert value["input_tokens"] is None


@pytest.mark.parametrize("damage", ["not_database", "missing_table", "missing_column"])
def test_damaged_sql_schema_is_a_stable_unavailable_summary(tmp_path: Path, damage: str) -> None:
    _run, ledger, reader = fixture(tmp_path)
    if damage == "not_database":
        ledger.journal.path.write_bytes(b"actual corrupted SQLite file")
    else:
        with sqlite3.connect(ledger.journal.path) as connection:
            if damage == "missing_table":
                connection.execute("DROP TABLE research_activity")
            else:
                connection.execute("ALTER TABLE research_budget RENAME COLUMN policy TO corrupted_policy_column")
    value = reader.resources()
    assert value["status"] == "invalid" and value["authority"] == "sqlite"
    assert value["input_tokens"] is None and value["charged_input_tokens"] is None
