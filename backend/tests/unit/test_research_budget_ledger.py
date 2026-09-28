"""Actual SQLite/process accounting tests, not model/tool execution substitutes."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
from multiprocessing.connection import Connection
from pathlib import Path
import sqlite3
from typing import Any

import pytest
import yaml

from app.harness.runtime.research_budget_ledger import (
    BudgetAmounts, BudgetConflictError, BudgetNotInitialized, BudgetReservation, BudgetSettlement,
    ResearchBudgetLedger, interval_union_us,
)
from app.harness.runtime.research_contract import ResearchBudget
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_journal import RunStateConflictError, RunStateIntegrityError, StateJournal
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle, RunStore

PRICE_HASH = "sha256:" + "b" * 64


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _budget(**changes: Any) -> ResearchBudget:
    path = Path(__file__).resolve().parents[3] / "configs/research_defaults.yaml"
    raw = yaml.safe_load(path.read_text())["budget"]
    return ResearchBudget.model_validate({**raw, **changes})


def _setup(tmp_path: Path, *, budget: ResearchBudget | None = None, initialize: bool = True,
           priced: bool = False) -> tuple[ResearchBudgetLedger, RunHandle]:
    import sys

    from app.bridge.research_contract_service import freeze_research_task
    from app.harness.runtime.research_contract import ProjectContract

    code = tmp_path / "source"
    code.mkdir(parents=True)
    (code / "baseline.py").write_text("# Authored source; no execution occurs.\n")
    project = ProjectContract.model_validate({"project_id": "generic", "display_name": "Ledger fixture",
        "paths": {"code": str(code), "data": [], "knowledge": [], "output": str(tmp_path / "output")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
            "arguments": ["baseline.py"], "entrypoint_files": ["baseline.py"]} for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "mse", "unit": "unitless", "direction": "minimize", "target": 0, "tolerance": 0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}})
    frozen = freeze_research_task(project, goal="Prepare a ledger", mode="manual", budget=budget or _budget())
    run = RunStore(tmp_path / "runs").create(task="sqlite-budget", project="generic", entrypoint="idea")
    (run.root / "input/research_task.v1.json").write_text(frozen.model_dump_json())
    graph = RunGraph()
    graph.add_node("idea")
    store = RunStateStore(run)
    store.write(graph=graph, request={"extra": {"research_task_sha256": frozen.task_sha256}}, status="created", expected_revision=0)
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    assert journal is not None
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget,
        price_reference_sha256=PRICE_HASH if priced else None)
    if initialize:
        ledger.initialize(now_us=0)
    return ledger, run


def _reserve(name: str, *, kind: str = "tool", amounts: BudgetAmounts | None = None,
             **extra: Any) -> BudgetReservation:
    if kind == "model":
        assert amounts is not None
        attempts = amounts.model_requests
        input_bound = int(extra.get("request_input_tokens", 1))
        output_bound = int(extra.get("request_output_tokens", 1))
        extra = {"request_input_tokens": input_bound, "request_output_tokens": output_bound, **extra}
        amounts = amounts.model_copy(update={"input_tokens": max(amounts.input_tokens, input_bound * attempts),
            "billed_output_tokens": max(amounts.billed_output_tokens, output_bound * attempts)})
    raw = {"reservation_id": name, "operation_id": name, "operation_fingerprint": _digest(name),
           "kind": kind, "amounts": (amounts or BudgetAmounts(tool_executions=1)).model_dump(), **extra}
    return BudgetReservation.model_validate(raw)


def _settlement(amounts: BudgetAmounts, *, failure: bool = False, evidence: str = "input-1") -> BudgetSettlement:
    return BudgetSettlement(actual=amounts, outcome="failure" if failure else "success",
        evidence_refs=("events/actual-observation.json",), evidence_fingerprint=_digest(evidence),
        error_fingerprint=_digest("same-error") if failure else None)


def test_read_requires_explicit_extension_and_initialization_is_idempotent(tmp_path: Path) -> None:
    ledger, run = _setup(tmp_path, initialize=False)
    before = ledger.journal.path.read_bytes()
    with pytest.raises(BudgetNotInitialized):
        ledger.snapshot(now_us=0)
    assert ledger.journal.path.read_bytes() == before
    ledger.initialize(now_us=0)
    initialized = ledger.journal.path.read_bytes()
    ledger.initialize(now_us=100)
    assert ledger.journal.path.read_bytes() == initialized
    assert ledger.snapshot(now_us=0).used["tool_executions"] == 0
    assert not any(path.name.startswith("research_budget") for path in run.root.iterdir())
    changed = ResearchBudgetLedger(ledger.journal, task_sha256=ledger.task_sha256, budget=_budget(tool_executions=121))
    with pytest.raises(BudgetConflictError, match="frozen"):
        changed.initialize(now_us=0)
    assert ledger.journal.path.read_bytes() == initialized


def test_atomic_reserve_settle_and_immutable_receipts(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, budget=_budget(tool_executions=2))
    spec = _reserve("op1", amounts=BudgetAmounts(tool_executions=2))
    assert ledger.reserve(spec, now_us=0).admitted
    replay = ledger.reserve(spec, now_us=1)
    assert replay.admitted and replay.replay
    assert ledger.snapshot(now_us=1).used["tool_executions"] == 2
    denied = ledger.reserve(_reserve("op2"), now_us=2)
    assert not denied.admitted and denied.reason == "exhausted:tool_executions"
    settlement = _settlement(BudgetAmounts(tool_executions=1))
    ledger.settle("op1", settlement, now_us=10)
    ledger.settle("op1", settlement, now_us=10)
    assert ledger.snapshot(now_us=10).used["tool_executions"] == 1
    with pytest.raises(BudgetConflictError, match="immutable"):
        ledger.settle("op1", _settlement(BudgetAmounts(tool_executions=2)), now_us=10)
    assert ledger.reserve(_reserve("op2"), now_us=10).admitted
    with pytest.raises(BudgetConflictError):
        ledger.reserve(spec.model_copy(update={"amounts": BudgetAmounts(tool_executions=1)}), now_us=10)


def test_state_outbox_and_budget_share_commit_and_rollback(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    previous = ledger.journal.read()
    candidate = json.loads(json.dumps(previous))
    candidate["graph"]["nodes"][0]["state"] = "running"
    with pytest.raises(RuntimeError, match="abort"):
        with ledger.journal.transaction() as connection:
            assert ledger.in_transaction(connection).reserve(_reserve("rollback"), now_us=0).admitted
            ledger.journal.commit_in_transaction(connection, candidate, expected_revision=1)
            raise RuntimeError("abort whole actual transaction")
    assert ledger.snapshot(now_us=0).used["tool_executions"] == 0
    assert ledger.journal.read() == previous and ledger.journal.pending_events() == []
    with ledger.journal.transaction() as connection:
        assert ledger.in_transaction(connection).reserve(_reserve("commit"), now_us=0).admitted
        committed = ledger.journal.commit_in_transaction(connection, candidate, expected_revision=1)
    assert committed["revision"] == 2 and len(ledger.journal.pending_events()) == 1
    assert ledger.snapshot(now_us=0).used["tool_executions"] == 1
    with pytest.raises(RunStateConflictError):
        with ledger.journal.transaction() as connection:
            assert ledger.in_transaction(connection).reserve(_reserve("cas-loser"), now_us=0).admitted
            ledger.journal.commit_in_transaction(connection, candidate, expected_revision=1)
    assert ledger.snapshot(now_us=0).used["tool_executions"] == 1


def test_activity_union_counts_parallel_work_once_and_does_not_count_idle(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("a"), now_us=10)
    ledger.reserve(_reserve("b"), now_us=15)
    ledger.settle("a", _settlement(BudgetAmounts(tool_executions=1)), now_us=20)
    ledger.settle("b", _settlement(BudgetAmounts(tool_executions=1)), now_us=25)
    assert ledger.snapshot(now_us=100).activity_us == 15
    ledger.reserve(_reserve("c"), now_us=100)
    ledger.settle("c", _settlement(BudgetAmounts(tool_executions=1)), now_us=110)
    assert ledger.snapshot(now_us=1000).activity_us == 25
    assert interval_union_us([(1, 5), (3, 7), (8, 10)]) == 8


def test_backward_clock_persists_uncertainty_without_refunding_activity(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("a"), now_us=100)
    assert ledger.snapshot(now_us=90).clock_uncertain
    refused = ledger.reserve(_reserve("b"), now_us=90)
    assert not refused.admitted and refused.reason == "clock_reconciliation_required"
    snapshot = ledger.snapshot(now_us=200)
    assert snapshot.clock_uncertain and snapshot.activity_remaining_us == 0
    assert snapshot.activity_us >= ledger.budget.research_activity_seconds * 1_000_000
    ledger.settle("a", _settlement(BudgetAmounts(tool_executions=1)), now_us=200)
    assert not ledger.reserve(_reserve("c"), now_us=300).admitted


def test_unknown_keeps_job_slot_time_and_quota_until_explicit_stopped_evidence(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    spec = _reserve("job", kind="job", amounts=BudgetAmounts(training_process_us=10_000_000), job_duration_us=10_000_000)
    assert ledger.reserve(spec, now_us=0).admitted
    ledger.mark_unknown("job", reason="actual runner owner disappeared", now_us=5)
    snapshot = ledger.snapshot(now_us=20)
    assert snapshot.active_jobs == 1 and snapshot.activity_us == 20
    assert snapshot.used["training_process_us"] == 10_000_000 and snapshot.unknown_reservations == ("job",)
    another = _reserve("job2", kind="job", amounts=BudgetAmounts(training_process_us=10), job_duration_us=10)
    assert ledger.reserve(another, now_us=20).reason == "training_concurrency_limit"
    with pytest.raises(BudgetConflictError, match="conservative reconciliation"):
        ledger.settle("job", _settlement(BudgetAmounts(training_process_us=0)), now_us=20)
    ledger.reconcile_stopped("job", actor="host", evidence_refs=("execution/actual-stop-receipt.json",), now_us=30)
    ledger.reconcile_stopped("job", actor="host", evidence_refs=("execution/actual-stop-receipt.json",), now_us=30)
    snapshot = ledger.snapshot(now_us=100)
    assert snapshot.active_jobs == 0 and snapshot.activity_us == 30
    assert snapshot.used["training_process_us"] == 10_000_000
    assert ledger.reserve(another, now_us=100).admitted
    retry = spec.model_copy(update={"reservation_id": "job-retry", "attempt_index": 1})
    assert ledger.reserve(retry, now_us=100).reason == "operation_not_retryable"


def test_reader_and_gpu_slots_are_atomic_and_persist_across_instances(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    for index in range(2):
        assert ledger.reserve(_reserve(f"reader{index}", kind="reader", amounts=BudgetAmounts(deep_read_papers=1)), now_us=0).admitted
    reopened = ResearchBudgetLedger(ledger.journal, task_sha256=ledger.task_sha256, budget=ledger.budget)
    assert reopened.reserve(_reserve("reader2", kind="reader", amounts=BudgetAmounts(deep_read_papers=1)), now_us=0).reason == "reader_concurrency_limit"
    ledger.settle("reader0", _settlement(BudgetAmounts(deep_read_papers=1)), now_us=1)
    assert reopened.reserve(_reserve("reader2", kind="reader", amounts=BudgetAmounts(deep_read_papers=1)), now_us=1).admitted
    job = _reserve("gpu", kind="job", amounts=BudgetAmounts(training_process_us=100, gpu_us=200), job_duration_us=100, gpus=2)
    assert ledger.reserve(job, now_us=1).reason == "gpu_allocation_limit"


def test_retries_and_repeated_error_fingerprints_survive_reopen(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    first = _reserve("operation")
    assert ledger.reserve(first, now_us=0).admitted
    error = _settlement(BudgetAmounts(tool_executions=1), failure=True)
    ledger.settle(first.reservation_id, error, now_us=1)
    retry = first.model_copy(update={"reservation_id": "retry1", "attempt_index": 1})
    assert ledger.reserve(retry, now_us=2).admitted
    ledger.settle("retry1", error, now_us=3)
    reopened = ResearchBudgetLedger(ledger.journal, task_sha256=ledger.task_sha256, budget=ledger.budget)
    third = first.model_copy(update={"reservation_id": "retry2", "attempt_index": 2})
    assert reopened.reserve(third, now_us=4).reason == "repeated_error_limit"
    duplicate_operation = first.model_copy(update={"reservation_id": "new", "operation_id": "renamed"})
    with pytest.raises(BudgetConflictError, match="already bound"):
        reopened.reserve(duplicate_operation, now_us=4)


def test_zero_retry_and_automatic_iteration_limits_are_real_zero(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, budget=_budget(operation_retries=0, automatic_iterations=0))
    first = _reserve("operation")
    ledger.reserve(first, now_us=0)
    ledger.settle("operation", _settlement(BudgetAmounts(tool_executions=1), failure=True), now_us=0)
    retry = first.model_copy(update={"reservation_id": "retry", "attempt_index": 1})
    assert ledger.reserve(retry, now_us=0).reason == "operation_retry_limit"
    iteration = _reserve("iteration", kind="iteration", amounts=BudgetAmounts(automatic_iterations=1))
    assert ledger.reserve(iteration, now_us=0).reason == "exhausted:automatic_iterations"


def test_exact_micro_currency_and_unknown_price_are_distinct(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path / "priced", budget=_budget(model_cost_cny=0.000003), priced=True)
    first = _reserve("m1", kind="model", amounts=BudgetAmounts(model_requests=1, model_cost_micro_cny=2))
    assert ledger.reserve(first, now_us=0).admitted
    second = first.model_copy(update={"reservation_id": "m2", "operation_id": "m2", "operation_fingerprint": _digest("m2")})
    assert ledger.reserve(second, now_us=0).reason == "exhausted:model_cost_micro_cny"
    ledger.settle("m1", _settlement(BudgetAmounts(model_requests=1, model_cost_micro_cny=1)), now_us=1)
    assert ledger.reserve(second, now_us=1).admitted
    assert ledger.snapshot(now_us=1).used["model_cost_micro_cny"] == 3
    assert ledger.snapshot(now_us=1).cost_ceiling_enforced
    unknown, _ = _setup(tmp_path / "unknown")
    assert unknown.reserve(_reserve("u", kind="model", amounts=BudgetAmounts(model_requests=1)), now_us=0).admitted
    snapshot = unknown.snapshot(now_us=0)
    assert snapshot.used["model_cost_micro_cny"] is None and not snapshot.cost_ceiling_enforced and not snapshot.cost_usage_exact
    assert snapshot.known_cost_subtotal_micro_cny == 0
    assert unknown.reserve(first, now_us=0).reason == "model_price_policy_mismatch"


def test_actual_usage_over_reservation_is_recorded_then_blocks_new_work(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("a"), now_us=0)
    ledger.settle("a", _settlement(BudgetAmounts(tool_executions=3)), now_us=1)
    snapshot = ledger.snapshot(now_us=1)
    assert snapshot.used["tool_executions"] == 3 and snapshot.reservation_overrun
    assert ledger.reserve(_reserve("b"), now_us=1).reason == "reservation_overrun"


def _race(path: str, run_id: str, journal_id: str, budget: dict[str, Any], name: str,
          barrier: Any, queue: Any) -> None:
    journal = StateJournal(Path(path), run_id=run_id, journal_id=journal_id)
    ledger = ResearchBudgetLedger(journal,
        task_sha256=journal.read()["request"]["extra"]["research_task_sha256"], budget=ResearchBudget.model_validate(budget))
    barrier.wait(timeout=15)
    result = ledger.reserve(_reserve(name), now_us=0)
    queue.put(result.admitted)


def test_two_real_processes_cannot_reserve_the_same_last_unit(tmp_path: Path) -> None:
    ledger, run = _setup(tmp_path, budget=_budget(tool_executions=1))
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [context.Process(target=_race, args=(str(ledger.journal.path), run.run_id, ledger.journal.journal_id,
        ledger.budget.model_dump(), f"worker{index}", barrier, queue)) for index in range(2)]
    try:
        for process in processes:
            process.start()
        assert sorted(queue.get(timeout=20) for _ in processes) == [False, True]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
    assert ledger.snapshot(now_us=0).used["tool_executions"] == 1


def _crash(path: str, run_id: str, journal_id: str, budget: dict[str, Any], committed: bool, pipe: Connection) -> None:
    journal = StateJournal(Path(path), run_id=run_id, journal_id=journal_id)
    ledger = ResearchBudgetLedger(journal,
        task_sha256=journal.read()["request"]["extra"]["research_task_sha256"], budget=ResearchBudget.model_validate(budget))
    if committed:
        ledger.reserve(_reserve("orphan"), now_us=0)
        pipe.send("committed")
        pipe.recv()
    else:
        with ledger.transaction() as transaction:
            transaction.reserve(_reserve("orphan"), now_us=0)
            pipe.send("staged")
            pipe.recv()


@pytest.mark.parametrize("committed", [False, True])
def test_real_process_death_preserves_only_committed_reservation(tmp_path: Path, committed: bool) -> None:
    ledger, run = _setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_crash, args=(str(ledger.journal.path), run.run_id, ledger.journal.journal_id,
        ledger.budget.model_dump(), committed, child))
    process.start()
    try:
        assert parent.poll(15)
        assert parent.recv() == ("committed" if committed else "staged")
        process.kill()
        process.join(timeout=5)
        assert process.exitcode != 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        parent.close()
        child.close()
    assert ledger.snapshot(now_us=10).used["tool_executions"] == int(committed)
    if committed:
        ledger.mark_unknown("orphan", reason="confirmed local owner process killed", now_us=10)
        assert ledger.snapshot(now_us=20).unknown_reservations == ("orphan",)
        assert ledger.snapshot(now_us=20).used["tool_executions"] == 1


@pytest.mark.parametrize("fault", ["identity", "version", "policy", "reservation", "activity", "lease"])
def test_corrupt_identity_or_extension_fails_closed(tmp_path: Path, fault: str) -> None:
    ledger, _ = _setup(tmp_path)
    reader = _reserve("reader", kind="reader", amounts=BudgetAmounts(deep_read_papers=1))
    ledger.reserve(reader, now_us=1)
    sql = {
        "identity": "UPDATE identity SET journal_id='wrong'",
        "version": "UPDATE research_budget SET version=99",
        "policy": "UPDATE research_budget SET policy='{}'",
        "reservation": "UPDATE research_reservations SET specification='{}'",
        "activity": "DELETE FROM research_activity",
        "lease": "DELETE FROM research_leases",
    }[fault]
    with sqlite3.connect(ledger.journal.path) as connection:
        connection.execute(sql)
    with pytest.raises(RunStateIntegrityError):
        ledger.snapshot(now_us=2)
    with pytest.raises(RunStateIntegrityError):
        ledger.reserve(_reserve("new"), now_us=2)


def test_cross_journal_transaction_and_wrong_contract_are_rejected(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path / "one")
    other, _ = _setup(tmp_path / "two")
    with other.journal.transaction() as connection:
        with pytest.raises(RunStateIntegrityError, match="does not belong"):
            ledger.in_transaction(connection)
    wrong = ResearchBudgetLedger(ledger.journal, task_sha256=_digest("wrong").removeprefix("sha256:"), budget=ledger.budget)
    with pytest.raises(BudgetConflictError, match="authoritative"):
        wrong.initialize(now_us=0)


@pytest.mark.parametrize("field,kind,maximum", [
    ("search_candidates", "search", 20), ("deep_read_papers", "reader", 5),
    ("proposal_candidates", "proposal", 2), ("implemented_candidates", "implementation", 1),
    ("debate_rounds", "debate", 1), ("automatic_iterations", "iteration", 2),
    ("tool_executions", "tool", 120), ("input_tokens", "model", 1_000_000),
    ("billed_output_tokens", "model", 128_000), ("training_process_us", "job", 3_600_000_000),
    ("gpu_us", "job", 3_600_000_000),
])
def test_each_cumulative_resource_rejects_before_inserting_reservation(
    tmp_path: Path, field: str, kind: str, maximum: int,
) -> None:
    ledger, _ = _setup(tmp_path)
    amounts: dict[str, int] = {field: maximum + 1}
    extra: dict[str, Any] = {}
    if kind == "model":
        amounts["model_requests"] = 1
    if kind == "job":
        amounts["training_process_us"] = max(1, amounts.get("training_process_us", 0))
        extra["job_duration_us"] = 1
    spec = _reserve("limit", kind=kind, amounts=BudgetAmounts.model_validate(amounts), **extra)
    admission = ledger.reserve(spec, now_us=0)
    assert not admission.admitted and admission.reason == "exhausted:" + field
    assert ledger.snapshot(now_us=0).used[field] == 0
    with ledger.journal.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_reservations").fetchone() == (0,)


def test_all_model_requests_across_distinct_operations_share_one_limit(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    for index in range(60):
        spec = _reserve(f"model{index}", kind="model", amounts=BudgetAmounts(model_requests=1))
        assert ledger.reserve(spec, now_us=0).admitted
        ledger.settle(spec.reservation_id, _settlement(BudgetAmounts(model_requests=1)), now_us=0)
    spec = _reserve("model61", kind="model", amounts=BudgetAmounts(model_requests=1))
    assert ledger.reserve(spec, now_us=0).reason == "exhausted:model_requests"
    assert ledger.snapshot(now_us=0).used["model_requests"] == 60


@pytest.mark.parametrize("input_bound,output_bound,output_class,expected", [
    (48_001, 1, "ordinary", "request_input_limit"),
    (1, 8_193, "ordinary", "request_output_limit"),
    (1, 8_193, "coding", None),
    (1, 16_385, "coding", "request_output_limit"),
])
def test_per_request_bounds_and_explicit_coding_class(
    tmp_path: Path, input_bound: int, output_bound: int, output_class: str, expected: str | None,
) -> None:
    ledger, _ = _setup(tmp_path)
    spec = _reserve("model", kind="model", amounts=BudgetAmounts(model_requests=1),
        request_input_tokens=input_bound, request_output_tokens=output_bound, output_class=output_class)
    admission = ledger.reserve(spec, now_us=0)
    assert admission.reason == expected and admission.admitted == (expected is None)


def test_job_and_activity_deadlines_and_cpu_gpu_zero(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, budget=_budget(max_gpus=0, research_activity_seconds=1))
    too_long = _reserve("long", kind="job", amounts=BudgetAmounts(training_process_us=601_000_000), job_duration_us=601_000_000)
    assert ledger.reserve(too_long, now_us=0).reason == "training_job_time_limit"
    gpu = _reserve("gpu", kind="job", amounts=BudgetAmounts(training_process_us=100, gpu_us=100), job_duration_us=100, gpus=1)
    assert ledger.reserve(gpu, now_us=0).reason == "gpu_allocation_limit"
    assert ledger.reserve(_reserve("active"), now_us=0).admitted
    assert ledger.reserve(_reserve("past-deadline"), now_us=1_000_000).reason == "research_activity_exhausted"
    assert ledger.snapshot(now_us=1_000_001).activity_us == 1_000_001


def test_sdk_attempt_reservations_share_operation_retry_limit(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    oversized = _reserve("sdk4", kind="model", amounts=BudgetAmounts(model_requests=4))
    assert ledger.reserve(oversized, now_us=0).reason == "operation_retry_limit"
    spec = _reserve("model", kind="model", amounts=BudgetAmounts(model_requests=3))
    assert ledger.reserve(spec, now_us=0).admitted
    ledger.settle("model", _settlement(BudgetAmounts(model_requests=1), failure=True), now_us=0)
    too_many = spec.model_copy(update={"reservation_id": "retry3", "attempt_index": 1})
    assert ledger.reserve(too_many, now_us=0).reason == "operation_retry_limit"
    allowed = spec.model_copy(update={"reservation_id": "retry2", "attempt_index": 1,
        "amounts": BudgetAmounts(model_requests=2, input_tokens=2, billed_output_tokens=2)})
    assert ledger.reserve(allowed, now_us=0).admitted


@pytest.mark.parametrize("prior", ["model", "discovery", "running"])
def test_initialization_does_not_erase_prior_execution_accounting(tmp_path: Path, prior: str) -> None:
    ledger, run = _setup(tmp_path, initialize=False)
    if prior == "running":
        payload = ledger.journal.read()
        payload["status"] = "running"
        payload["graph"]["nodes"][0]["state"] = "running"
        ledger.journal.commit(payload, expected_revision=1)
        original: bytes | None = None
        source = run.root / "unused"
    else:
        source = run.root / ("resources/model_budget.v1.json" if prior == "model" else "discovery/budget/limits.json")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('{"existing_usage": 17}')
        original = source.read_bytes()
    with pytest.raises(BudgetConflictError, match="explicit budget migration"):
        ledger.initialize(now_us=0)
    if original is not None:
        assert source.read_bytes() == original
    with pytest.raises(BudgetNotInitialized):
        ledger.snapshot(now_us=0)


def test_price_reference_is_frozen_and_absent_authority_is_not_recreated(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, priced=True)
    changed = ResearchBudgetLedger(ledger.journal, task_sha256=ledger.task_sha256, budget=ledger.budget,
        price_reference_sha256=_digest("different-prices"))
    with pytest.raises(BudgetConflictError, match="frozen"):
        changed.initialize(now_us=0)
    ledger.journal.path.unlink()
    with pytest.raises(RunStateIntegrityError):
        ledger.snapshot(now_us=0)
    assert not ledger.journal.path.exists()


def test_unknown_actual_cost_retains_priced_reservation_upper_bound(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, budget=_budget(model_cost_cny=0.000003), priced=True)
    spec = _reserve("model", kind="model", amounts=BudgetAmounts(model_requests=1, model_cost_micro_cny=2))
    assert ledger.reserve(spec, now_us=0).admitted
    ledger.settle("model", _settlement(BudgetAmounts(model_requests=1)), now_us=1)
    snapshot = ledger.snapshot(now_us=1)
    assert snapshot.used["model_cost_micro_cny"] == 2
    assert snapshot.cost_ceiling_enforced and not snapshot.cost_usage_exact
    second = spec.model_copy(update={"reservation_id": "second", "operation_id": "second", "operation_fingerprint": _digest("second")})
    assert ledger.reserve(second, now_us=1).reason == "exhausted:model_cost_micro_cny"


def test_closed_reconciliation_requires_well_formed_persisted_evidence(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("unknown"), now_us=0)
    ledger.mark_unknown("unknown", reason="owner lost", now_us=0)
    ledger.reconcile_stopped("unknown", actor="host", evidence_refs=("actual-stop.json",), now_us=1)
    with sqlite3.connect(ledger.journal.path) as connection:
        connection.execute("UPDATE research_reservations SET reconciliation='{}'")
    with pytest.raises(RunStateIntegrityError):
        ledger.snapshot(now_us=1)


def test_real_frozen_contract_run_initializes_with_exact_production_task_hash(tmp_path: Path) -> None:
    import sys

    from app.bridge.orchestrator import Orchestrator
    from app.bridge.research_contract_service import freeze_research_task
    from app.bridge.research_run_service import create_research_run, load_run_research_contract, research_execution_admission
    from app.harness.runtime.research_contract import ProjectContract

    code = tmp_path / "real_project"
    code.mkdir()
    (code / "baseline.py").write_text("# Human-authored protected source.\n")
    (code / "command.py").write_text("raise RuntimeError('budget initialization must not execute a command')\n")
    project = ProjectContract.model_validate({"project_id": "budget_integration", "display_name": "Budget integration",
        "paths": {"code": str(code), "knowledge": [], "data": [], "output": str(tmp_path / "output")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
                      "arguments": ["command.py"], "entrypoint_files": ["command.py"]}
                     for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "MSE", "unit": "unitless", "direction": "minimize", "target": 0.0, "tolerance": 0.0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}})
    frozen = freeze_research_task(project, goal="Prepare a real contract-bound ledger", mode="manual", budget=_budget())
    owner = Orchestrator(run_store=RunStore(tmp_path / "runs"))
    session = create_research_run(owner, name="Frozen ledger integration", contract=frozen)
    journal = StateJournal.from_authority(session.run.root, run_id=session.run.run_id)
    assert journal is not None
    extra = journal.read()["request"]["extra"]
    assert len(frozen.task_sha256) == 64 and extra["research_task_sha256"] == frozen.task_sha256
    assert load_run_research_contract(session.run, extra) == frozen
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget)
    ledger.initialize(now_us=0)
    assert ledger.snapshot(now_us=0).task_sha256 == frozen.task_sha256
    assert ledger.reserve(_reserve("pure-ledger-record"), now_us=0).admitted
    restored = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget)
    assert restored.snapshot(now_us=0).used["tool_executions"] == 1
    admission = research_execution_admission(session.run, extra)
    assert admission is not None and admission.ready is False
    assert owner.owned_tasks.active(session.run.run_id) is None
    assert not (session.run.root / "agent_traces").exists() and not (session.run.root / "resources").exists()


def test_first_initialization_cannot_bind_different_budget_to_same_task_hash(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, initialize=False)
    mismatched = ResearchBudgetLedger(ledger.journal, task_sha256=ledger.task_sha256, budget=_budget(model_requests=61))
    with pytest.raises(BudgetConflictError, match="differs from the frozen"):
        mismatched.initialize(now_us=0)
    with pytest.raises(BudgetNotInitialized):
        ledger.snapshot(now_us=0)


@pytest.mark.parametrize("fault", ["missing", "modified", "symlink"])
def test_frozen_declaration_is_checked_without_following_external_input(tmp_path: Path, fault: str) -> None:
    ledger, run = _setup(tmp_path)
    path = run.root / "input/research_task.v1.json"
    original = path.read_bytes()
    external = tmp_path / "external-contract.json"
    external.write_bytes(original)
    if fault == "missing":
        path.unlink()
    elif fault == "symlink":
        path.unlink()
        path.symlink_to(external)
    else:
        raw = json.loads(original)
        raw["task"]["budget"]["model_requests"] += 1
        path.write_text(json.dumps(raw))
    before = ledger.journal.path.read_bytes()
    with pytest.raises(RunStateIntegrityError, match="frozen research task"):
        ledger.snapshot(now_us=0)
    assert ledger.journal.path.read_bytes() == before and external.read_bytes() == original


@pytest.mark.parametrize("kind,amounts", [
    ("tool", BudgetAmounts()),
    ("tool", BudgetAmounts(tool_executions=1, model_requests=1)),
    ("tool", BudgetAmounts(tool_executions=1, input_tokens=1)),
    ("tool", BudgetAmounts(tool_executions=1, billed_output_tokens=1)),
    ("tool", BudgetAmounts(tool_executions=1, model_cost_micro_cny=1)),
    ("tool", BudgetAmounts(tool_executions=1, training_process_us=1)),
    ("model", BudgetAmounts(model_requests=1, training_process_us=1)),
    ("job", BudgetAmounts(training_process_us=1, model_requests=1)),
])
def test_reservation_rejects_missing_attempt_and_wrong_resource_family(kind: str, amounts: BudgetAmounts) -> None:
    with pytest.raises(ValueError):
        _reserve("invalid", kind=kind, amounts=amounts, **({"job_duration_us": 1} if kind == "job" else {}))


@pytest.mark.parametrize("kind,actual", [
    ("tool", BudgetAmounts()),
    ("model", BudgetAmounts()),
    ("tool", BudgetAmounts(tool_executions=1, model_requests=1, input_tokens=1)),
    ("tool", BudgetAmounts(tool_executions=1, gpu_us=1)),
    ("model", BudgetAmounts(model_requests=1, training_process_us=1)),
    ("job", BudgetAmounts(training_process_us=1, model_requests=1)),
])
def test_settlement_family_failure_rolls_back_without_refunding_reservation(
    tmp_path: Path, kind: str, actual: BudgetAmounts,
) -> None:
    ledger, _ = _setup(tmp_path)
    amounts = {"tool": BudgetAmounts(tool_executions=1), "model": BudgetAmounts(model_requests=1),
               "job": BudgetAmounts(training_process_us=1)}[kind]
    spec = _reserve("operation", kind=kind, amounts=amounts, **({"job_duration_us": 1} if kind == "job" else {}))
    ledger.reserve(spec, now_us=0)
    before = ledger.journal.path.read_bytes()
    with pytest.raises(ValueError):
        ledger.settle("operation", _settlement(actual), now_us=1)
    assert ledger.journal.path.read_bytes() == before
    assert ledger.snapshot(now_us=1).used == ledger.snapshot(now_us=0).used


def test_unknown_observed_lower_bound_never_refunds_and_survives_reopen(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    spec = _reserve("model", kind="model", amounts=BudgetAmounts(model_requests=3, input_tokens=30, billed_output_tokens=24))
    ledger.reserve(spec, now_us=0)
    with ledger.journal.connection() as connection:
        original = connection.execute("SELECT specification,specification_sha256 FROM research_reservations").fetchone()
    ledger.mark_unknown("model", reason="incomplete actual receipt",
        observed_lower_bound=BudgetAmounts(model_requests=1, input_tokens=10, billed_output_tokens=80), now_us=1)
    ledger.mark_unknown("model", reason="incomplete actual receipt",
        observed_lower_bound=BudgetAmounts(input_tokens=40, billed_output_tokens=8), now_us=2)
    ledger.mark_unknown("model", reason="incomplete actual receipt", now_us=2)
    reopened = ResearchBudgetLedger(ledger.journal, task_sha256=ledger.task_sha256, budget=ledger.budget)
    snapshot = reopened.snapshot(now_us=10)
    assert snapshot.used["model_requests"] == 3
    assert snapshot.used["input_tokens"] == 40 and snapshot.used["billed_output_tokens"] == 80
    assert snapshot.used["model_cost_micro_cny"] is None and not snapshot.cost_usage_exact
    assert snapshot.activity_us == 10 and snapshot.unknown_reservations == ("model",)
    assert snapshot.reservation_overrun and ledger.reserve(_reserve("new"), now_us=10).reason == "reservation_overrun"
    with ledger.journal.connection() as connection:
        assert connection.execute("SELECT specification,specification_sha256 FROM research_reservations").fetchone() == original
    ledger.reconcile_stopped("model", actor="host", evidence_refs=("provider/observed-stop.json",), now_us=11)
    retained = reopened.snapshot(now_us=100)
    assert retained.used == snapshot.used and retained.reservation_overrun
    assert retained.activity_us == 11 and retained.unknown_reservations == ("model",)


def test_unknown_job_observed_overrun_keeps_slot_until_explicit_stop(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    spec = _reserve("job", kind="job", amounts=BudgetAmounts(training_process_us=10, gpu_us=10), job_duration_us=10, gpus=1)
    ledger.reserve(spec, now_us=0)
    ledger.mark_unknown("job", reason="runner lost", observed_lower_bound=BudgetAmounts(training_process_us=50, gpu_us=50), now_us=1)
    snapshot = ledger.snapshot(now_us=5)
    assert snapshot.active_jobs == snapshot.active_gpus == 1
    assert snapshot.used["training_process_us"] == snapshot.used["gpu_us"] == 50
    assert snapshot.activity_us == 5 and snapshot.reservation_overrun


def test_unknown_lower_bound_validates_family_and_price_without_partial_write(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    spec = _reserve("model", kind="model", amounts=BudgetAmounts(model_requests=1))
    ledger.reserve(spec, now_us=0)
    before = ledger.journal.path.read_bytes()
    for actual in (BudgetAmounts(training_process_us=1), BudgetAmounts(model_cost_micro_cny=1)):
        with pytest.raises(ValueError):
            ledger.mark_unknown("model", reason="known error", observed_lower_bound=actual, now_us=1)
        assert ledger.journal.path.read_bytes() == before
    ledger.mark_unknown("model", reason="known error", observed_lower_bound=BudgetAmounts(), now_us=1)
    assert ledger.snapshot(now_us=1).used["model_requests"] == 1


def _record_hash(raw: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


@pytest.mark.parametrize("fault", ["hash", "missing_hash", "malformed", "wrong_family", "wrong_state"])
def test_persisted_unknown_lower_bound_corruption_fails_closed(tmp_path: Path, fault: str) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("operation"), now_us=0)
    ledger.mark_unknown("operation", reason="owner lost", observed_lower_bound=BudgetAmounts(tool_executions=2), now_us=1)
    with sqlite3.connect(ledger.journal.path) as connection:
        if fault == "hash":
            connection.execute("UPDATE research_reservations SET observed_lower_bound_sha256=?", (_digest("wrong"),))
        elif fault == "missing_hash":
            connection.execute("UPDATE research_reservations SET observed_lower_bound_sha256=NULL")
        elif fault == "malformed":
            connection.execute("UPDATE research_reservations SET observed_lower_bound='not json'")
        elif fault == "wrong_family":
            raw = BudgetAmounts(model_requests=1).model_dump(mode="json")
            connection.execute("UPDATE research_reservations SET observed_lower_bound=?,observed_lower_bound_sha256=?",
                               (json.dumps(raw), _record_hash(raw)))
        else:
            connection.execute("UPDATE research_reservations SET state='reserved'")
    before = ledger.journal.path.read_bytes()
    with pytest.raises(RunStateIntegrityError):
        ledger.snapshot(now_us=2)
    with pytest.raises(RunStateIntegrityError):
        ledger.reserve(_reserve("next"), now_us=2)
    assert ledger.journal.path.read_bytes() == before


def test_read_rejects_wrong_family_even_with_valid_settlement_hash(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("operation"), now_us=0)
    ledger.settle("operation", _settlement(BudgetAmounts(tool_executions=1)), now_us=1)
    raw = _settlement(BudgetAmounts(model_requests=1)).model_dump(mode="json")
    with sqlite3.connect(ledger.journal.path) as connection:
        connection.execute("UPDATE research_reservations SET settlement=?,settlement_sha256=?", (json.dumps(raw), _record_hash(raw)))
    with pytest.raises(RunStateIntegrityError):
        ledger.snapshot(now_us=1)


def test_extension_v1_is_preserved_and_requires_explicit_future_migration(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    with sqlite3.connect(ledger.journal.path) as connection:
        connection.execute("UPDATE research_budget SET version=1")
        connection.execute("ALTER TABLE research_reservations DROP COLUMN observed_lower_bound")
        connection.execute("ALTER TABLE research_reservations DROP COLUMN observed_lower_bound_sha256")
    before = ledger.journal.path.read_bytes()
    for action in (ledger.snapshot, ledger.initialize):
        with pytest.raises(RunStateIntegrityError, match="extension version"):
            action(now_us=0)
        assert ledger.journal.path.read_bytes() == before


@pytest.mark.parametrize("status", ["queued", "waiting_review", "waiting_feedback", "pausing", "paused", "cancelling", "cancelled", "failed", "completed", "blocked"])
def test_inactive_run_cannot_add_reservations_but_can_finish_existing_work(tmp_path: Path, status: str) -> None:
    ledger, _ = _setup(tmp_path)
    spec = _reserve("already-owned")
    ledger.reserve(spec, now_us=0)
    payload = ledger.journal.read()
    payload["status"] = status
    ledger.journal.commit(payload, expected_revision=payload["revision"])
    assert ledger.reserve(_reserve("new"), now_us=1).reason == "run_not_executable"
    # Replay is existing accounting identity, never permission to execute again.
    assert ledger.reserve(spec, now_us=1).replay
    ledger.settle(spec.reservation_id, _settlement(BudgetAmounts(tool_executions=1)), now_us=2)
    assert ledger.snapshot(now_us=2).used["tool_executions"] == 1


def test_verified_late_activity_end_does_not_rewind_clock_or_other_intervals(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("finished"), now_us=10)
    ledger.reserve(_reserve("ongoing"), now_us=50)
    ledger.settle("finished", _settlement(BudgetAmounts(tool_executions=1)), now_us=100, activity_ended_us=20)
    snapshot = ledger.snapshot(now_us=110)
    assert snapshot.activity_us == 70 and not snapshot.clock_uncertain
    with ledger.journal.connection() as connection:
        assert connection.execute("SELECT high_watermark_us FROM research_budget").fetchone() == (100,)
    with pytest.raises(BudgetConflictError, match="immutable"):
        ledger.settle("finished", _settlement(BudgetAmounts(tool_executions=1)), now_us=110, activity_ended_us=21)


@pytest.mark.parametrize("ended", [9, 101])
def test_activity_end_outside_verified_observation_window_rolls_back(tmp_path: Path, ended: int) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("operation"), now_us=10)
    before = ledger.journal.path.read_bytes()
    with pytest.raises(BudgetConflictError, match="between"):
        ledger.settle("operation", _settlement(BudgetAmounts(tool_executions=1)), now_us=100, activity_ended_us=ended)
    assert ledger.journal.path.read_bytes() == before


def test_late_unknown_stop_retains_usage_with_verified_activity_end(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path)
    ledger.reserve(_reserve("operation", amounts=BudgetAmounts(tool_executions=2)), now_us=10)
    ledger.mark_unknown("operation", reason="receipt unavailable", now_us=100)
    ledger.reconcile_stopped("operation", actor="verified-host", evidence_refs=("receipt.json",), now_us=200, activity_ended_us=20)
    snapshot = ledger.snapshot(now_us=300)
    assert snapshot.activity_us == 10 and snapshot.used["tool_executions"] == 2
    assert not snapshot.clock_uncertain and snapshot.unknown_reservations == ("operation",)


def test_clock_observation_after_group_rollback_retains_uncertainty(tmp_path: Path) -> None:
    ledger, _ = _setup(tmp_path, budget=_budget(tool_executions=1))
    with ledger.transaction() as transaction:
        transaction.observe_clock(now_us=100)
        transaction.connection.execute("SAVEPOINT quota_group")
        assert transaction.reserve(_reserve("first"), now_us=100).admitted
        assert not transaction.reserve(_reserve("second"), now_us=100).admitted
        transaction.connection.execute("ROLLBACK TO quota_group")
        transaction.connection.execute("RELEASE quota_group")
        transaction.observe_clock(now_us=90)
    snapshot = ledger.snapshot(now_us=200)
    assert snapshot.clock_uncertain and snapshot.used["tool_executions"] == 0
    assert ledger.reserve(_reserve("third"), now_us=200).reason == "clock_reconciliation_required"
