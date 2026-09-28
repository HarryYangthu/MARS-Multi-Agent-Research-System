"""Real files, actual git subprocesses and SQLite; no tool/provider doubles."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import time

import yaml
from filelock import FileLock
from typing import Any

import pytest

from app.bridge.research_contract_service import freeze_research_task
from app.bridge.research_run_service import persist_run_research_contract
from app.bridge.research_project_scope import prepare_project_scope
from app.harness.discovery.snapshots import SnapshotPolicy
from app.harness.runtime.project_scope import ProjectScope, bind_project_scope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.research_contract import ProjectContract, ResearchBudget
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_journal import StateJournal
from app.storage.run_store import RunStore
from app.storage.run_state_store import RunStateStore
from app.harness.runtime.research_execution_scope import ResearchExecutionScope, bind_research_execution
from app.harness.tools.registry import ToolContext, ToolResult, get_registry
def _budget(**changes: Any) -> ResearchBudget:
    path = Path(__file__).resolve().parents[3] / "configs/research_defaults.yaml"
    return ResearchBudget.model_validate({**yaml.safe_load(path.read_text())["budget"], **changes})



def setup(tmp_path: Path, **budget: Any) -> tuple[ProjectScope, ResearchExecutionScope, ToolContext]:
    root = tmp_path.resolve()
    code = root / "source"
    code.mkdir(parents=True)
    (code / "baseline.py").write_text("# Authored baseline; not executed.\n")
    (code / "candidate.py").write_text("VALUE = 1\n")
    project = ProjectContract.model_validate({"project_id": "generic", "display_name": "Tool fixture",
        "paths": {"code": str(code), "data": [], "knowledge": [], "output": str(root / "output")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
            "arguments": ["baseline.py"], "entrypoint_files": ["baseline.py"]} for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "mse", "unit": "unitless", "direction": "minimize", "target": 0, "tolerance": 0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}})
    frozen = freeze_research_task(project, goal="Actual file tool checks", mode="manual", budget=_budget(**budget))
    run = RunStore(root / "runs").create(task="file-tools", project="generic", entrypoint="pipeline")
    persist_run_research_contract(run, frozen)
    (run.root / "input/run_request_options.v1.json").write_text(json.dumps({
        "schema_id": "run_request_options.v1", "extra": {"research_task_sha256": frozen.task_sha256}}))
    graph = RunGraph()
    graph.add_node("coding")
    RunStateStore(run).write(graph=graph, request={"extra": {"research_task_sha256": frozen.task_sha256}},
                            status="created", expected_revision=0)
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    assert journal is not None
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget)
    ledger.initialize()
    scope = prepare_project_scope(run, candidate_id="one", snapshot_policy=SnapshotPolicy(allowed_paths=("*",)))
    execution = ResearchExecutionScope(ledger, stage="coding", invocation_id="actual-tools")
    ctx = ToolContext(run_id=run.run_id, project=run.project, agent="coding", extra={"run_root": str(run.root)})
    return scope, execution, ctx


def call(scope: ProjectScope, execution: ResearchExecutionScope, ctx: ToolContext,
         name: str, args: dict[str, Any]) -> ToolResult:
    with bind_research_execution(execution), bind_project_scope(scope):
        return asyncio.run(get_registry().dispatch(name, args, ctx))


def receipts(scope: ProjectScope) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted((scope.run_root / "resources/tool_receipts").glob("*.json"))]


def patch(before: str = "VALUE = 1", after: str = "VALUE = 2") -> str:
    return f"--- a/candidate.py\n+++ b/candidate.py\n@@ -1 +1 @@\n-{before}\n+{after}\n"


def test_actual_read_write_reread_and_stable_repeat_identity(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    first = call(scope, execution, ctx, "code.repo_reader", {"path": "candidate.py"})
    assert first.ok and first.output["content"] == "VALUE = 1\n"
    repeated = call(scope, replace(execution, invocation_id="different-host-call"), ctx,
                    "code.repo_reader", {"path": "candidate.py", "char_offset": 0})
    assert not repeated.ok and execution.ledger.snapshot().used["tool_executions"] == 1
    written = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "VALUE = 2\n"})
    assert written.ok and written.metadata["changed_paths"] == ["candidate.py"]
    second = call(scope, execution, ctx, "code.repo_reader", {"path": "candidate.py"})
    assert second.ok and second.output["content"] == "VALUE = 2\n"
    assert execution.ledger.snapshot().used["tool_executions"] == 3
    assert not execution.ledger.snapshot().unknown_reservations
    assert len(receipts(scope)) == 3
    assert (tmp_path / "source/candidate.py").read_text() == "VALUE = 1\n"


@pytest.mark.parametrize("name,args", [
    ("code.write_file", {"path": "candidate.py"}),
    ("code.repo_reader", {"path": "absent.py"}),
    ("code.repo_reader", {"path": "candidate.py", "char_offset": 900}),
    ("code.repo_reader", {"path": "candidate.py", "nonce": "fresh"}),
    ("code.apply_patch", {"diff": patch(), "files": [{"path": "baseline.py"}]}),
    ("code.apply_patch", {"patch_path": "/not/admitted.diff"}),
    ("code.test_runner", {}),
    ("code.patch_generator", {"path": "candidate.py", "content": "changed"}),
])
def test_prehandler_rejections_do_not_consume_execution(tmp_path: Path, name: str, args: dict[str, Any]) -> None:
    scope, execution, ctx = setup(tmp_path)
    result = call(scope, execution, ctx, name, args)
    assert not result.ok
    assert execution.ledger.snapshot().used["tool_executions"] == 0
    assert not receipts(scope)
    assert (scope.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"


def test_actual_patch_and_rollback_with_host_version_no_raw_requested_label(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    secret = "auth-token-not-a-patch-version"
    result = call(scope, execution, ctx, "code.apply_patch", {"diff": patch(), "version": secret})
    assert result.ok and (scope.candidate_root / "candidate.py").read_text() == "VALUE = 2\n"
    assert result.metadata["host_version"].startswith("host_")
    assert result.rollback_ref
    restored = call(scope, execution, ctx, "code.rollback_patch", {"rollback_ref": result.rollback_ref})
    assert restored.ok and (scope.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"
    assert execution.ledger.snapshot().used["tool_executions"] == 2
    for path in scope.run_root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl"}:
            assert secret not in path.read_text()


def test_actual_git_check_failure_retains_unknown_and_versions_cannot_retry(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path, operation_retries=0, repeated_error_limit=1)
    for version in ("first", "second"):
        result = call(scope, execution, ctx, "code.apply_patch", {"diff": patch(before="VALUE = 999"), "version": version})
        assert not result.ok
    snapshot = execution.ledger.snapshot()
    assert snapshot.used["tool_executions"] == 1 and len(snapshot.unknown_reservations) == 2
    record = receipts(scope)[0]
    assert record["outcome"] == "unknown" and record["handler_started"]
    assert record["before"] == record["after"]
    assert (scope.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"


def test_sqlite_refuses_reservation_before_actual_write(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    with sqlite3.connect(execution.ledger.journal.path) as connection:
        connection.execute("CREATE TRIGGER reject_tool BEFORE INSERT ON research_reservations BEGIN SELECT RAISE(ABORT, 'actual rejection'); END")
    result = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "changed"})
    assert not result.ok and (scope.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"
    assert not receipts(scope)
    assert execution.ledger.snapshot().used["tool_executions"] == 0


def test_settlement_failure_never_claims_committed_success(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    with sqlite3.connect(execution.ledger.journal.path) as connection:
        connection.execute("CREATE TRIGGER reject_settlement BEFORE UPDATE ON research_reservations WHEN NEW.state='settled' BEGIN SELECT RAISE(ABORT, 'actual rejection'); END")
    result = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "changed"})
    assert not result.ok and result.metadata["budget_outcome"] == "unknown"
    assert (scope.candidate_root / "candidate.py").read_text() == "changed"
    assert execution.ledger.snapshot().used["tool_executions"] == 1
    assert len(execution.ledger.snapshot().unknown_reservations) == 2


def test_real_async_quota_competition(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path, tool_executions=1)
    async def compete() -> list[ToolResult]:
        with bind_research_execution(execution), bind_project_scope(scope):
            return list(await asyncio.gather(
                get_registry().dispatch("code.repo_reader", {"path": "candidate.py"}, ctx),
                get_registry().dispatch("code.repo_reader", {"path": "baseline.py"}, ctx)))
    results = asyncio.run(compete())
    assert sum(item.ok for item in results) == 1
    assert execution.ledger.snapshot().used["tool_executions"] == 1
    assert len(receipts(scope)) == 1


def test_gate5_and_actual_human_approval_precede_reservation(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    blocked = call(scope, execution, ctx, "code.write_file", {"path": "baseline.py", "content": "protected"})
    assert not blocked.ok and blocked.blocked_by_gate == "baseline_compatibility"
    pending = call(scope, execution, ctx, "code.delete_file", {"path": "candidate.py"})
    assert pending.requires_approval and execution.ledger.snapshot().used["tool_executions"] == 0
    approval_id = pending.metadata["approval_id"]
    path = scope.run_file(f"events/tool_approvals/{approval_id}.json", must_exist=True)
    approved = json.loads(path.read_text())
    approved["status"] = "approved"  # Authored human decision on this temporary real approval artifact.
    path.write_text(json.dumps(approved))
    deleted = call(scope, execution, replace(ctx, agent="bridge", approval_mode="approved"),
                   "code.delete_file", {"path": "candidate.py", "_approval_id": approval_id})
    assert deleted.ok and not (scope.candidate_root / "candidate.py").exists()
    assert execution.ledger.snapshot().used["tool_executions"] == 1


@pytest.mark.parametrize("binding", ["none", "project", "execution", "wrong_root", "wrong_run"])
def test_host_bindings_fail_before_any_trace_write(tmp_path: Path, binding: str) -> None:
    scope, execution, ctx = setup(tmp_path)
    original = sorted(path.relative_to(scope.run_root).as_posix() for path in scope.run_root.rglob("*"))
    async def run() -> ToolResult:
        if binding == "none":
            return await get_registry().dispatch("code.repo_reader", {"path": "candidate.py"}, ctx)
        if binding == "project":
            with bind_project_scope(scope):
                return await get_registry().dispatch("code.repo_reader", {"path": "candidate.py"}, ctx)
        if binding == "execution":
            with bind_research_execution(execution):
                return await get_registry().dispatch("code.repo_reader", {"path": "candidate.py"}, ctx)
        target = replace(ctx, extra={"run_root": str(tmp_path / "outside")}) if binding == "wrong_root" else replace(ctx, run_id="another-run")
        with bind_research_execution(execution), bind_project_scope(scope):
            return await get_registry().dispatch("code.repo_reader", {"path": "candidate.py"}, target)
    assert not asyncio.run(run()).ok
    assert sorted(path.relative_to(scope.run_root).as_posix() for path in scope.run_root.rglob("*")) == original
    assert not (tmp_path / "outside").exists()


def test_cancel_actual_owned_dispatch_retains_reservation(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    async def cancel() -> None:
        with bind_research_execution(execution), bind_project_scope(scope):
            task = asyncio.create_task(get_registry().dispatch("code.apply_patch", {"diff": patch()}, ctx))
            while execution.ledger.snapshot().used["tool_executions"] == 0:
                assert not task.done()
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(cancel())
    assert execution.ledger.snapshot().used["tool_executions"] == 1
    assert len(execution.ledger.snapshot().unknown_reservations) == 2
    assert receipts(scope)[0]["outcome"] == "unknown"


def test_candidate_entry_is_atomic_bounded_and_not_counted_per_edit(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path, implemented_candidates=1)
    run = RunStore(scope.run_root.parent).get(scope.run_id)
    assert run is not None
    second = prepare_project_scope(run, candidate_id="two", snapshot_policy=SnapshotPolicy(allowed_paths=("*",)))
    first = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "VALUE = 2\n"})
    assert first.ok and first.metadata["candidate_entry_charged"]
    edited = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "VALUE = 3\n"})
    assert edited.ok and not edited.metadata["candidate_entry_charged"]
    denied = call(second, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "VALUE = 4\n"})
    assert not denied.ok and "implemented_candidates" in str(denied.error)
    assert (second.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"
    snapshot = execution.ledger.snapshot()
    assert snapshot.used["tool_executions"] == 2 and snapshot.used["implemented_candidates"] == 1


def test_second_reservation_denial_rolls_back_candidate_charge(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path, tool_executions=1)
    assert call(scope, execution, ctx, "code.repo_reader", {"path": "candidate.py"}).ok
    denied = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "changed"})
    assert not denied.ok and "tool_executions" in str(denied.error)
    snapshot = execution.ledger.snapshot()
    assert snapshot.used["tool_executions"] == 1 and snapshot.used["implemented_candidates"] == 0
    with sqlite3.connect(execution.ledger.journal.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_reservations").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM research_activity").fetchone() == (1,)


def test_unknown_candidate_stays_charged_after_later_valid_write(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    failed = call(scope, execution, ctx, "code.apply_patch", {"diff": patch(before="missing")})
    assert not failed.ok
    unknown = execution.ledger.snapshot().unknown_reservations
    assert len(unknown) == 2
    written = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "actual later edit"})
    assert written.ok and not written.metadata["candidate_entry_charged"]
    snapshot = execution.ledger.snapshot()
    assert snapshot.unknown_reservations == unknown and snapshot.used["implemented_candidates"] == 1


def test_normal_deny_preserves_clock_uncertainty(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    future = time.time_ns() // 1000 + 60_000_000
    with execution.ledger.transaction() as transaction:
        transaction.observe_clock(now_us=future)
    denied = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "changed"})
    assert not denied.ok
    with sqlite3.connect(execution.ledger.journal.path) as connection:
        assert connection.execute("SELECT high_watermark_us,clock_uncertain FROM research_budget").fetchone() == (future, 1)
        assert connection.execute("SELECT COUNT(*) FROM research_reservations").fetchone() == (0,)


def test_wait_for_real_held_file_lock_is_bounded_without_charge(tmp_path: Path) -> None:
    from app.harness.tools.research_accounting import execute_tool, prepare_tool
    from app.harness.tools.code import repo_reader_tool
    scope, execution, ctx = setup(tmp_path)
    lock_path = scope.run_file("resources/.contract-tools.lock")
    lock_path.parent.mkdir(exist_ok=True)
    with bind_research_execution(execution), bind_project_scope(scope):
        plan = prepare_tool(execution, scope, "code.repo_reader", {"path": "candidate.py"}, ctx, repo_reader_tool)
        with FileLock(lock_path):
            spec = get_registry().spec("code.repo_reader")
            assert spec is not None
            result = asyncio.run(execute_tool(plan, repo_reader_tool, ctx, spec.output_schema, 0.05))
    assert not result.ok and result.metadata["handler_started"] is False
    assert execution.ledger.snapshot().used["tool_executions"] == 0


def test_true_content_cycle_keeps_conservative_read_replay_block(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path)
    assert call(scope, execution, ctx, "code.repo_reader", {"path": "candidate.py"}).ok
    written = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "changed"})
    assert written.ok and written.rollback_ref
    assert call(scope, execution, ctx, "code.rollback_patch", {"rollback_ref": written.rollback_ref}).ok
    assert not call(scope, execution, ctx, "code.repo_reader", {"path": "candidate.py"}).ok
    assert execution.ledger.snapshot().used["tool_executions"] == 3


def test_real_unknown_activity_exhaustion_blocks_later_handler(tmp_path: Path) -> None:
    scope, execution, ctx = setup(tmp_path, research_activity_seconds=1)
    failed = call(scope, execution, ctx, "code.apply_patch", {"diff": patch(before="missing")})
    assert not failed.ok and len(execution.ledger.snapshot().unknown_reservations) == 2
    time.sleep(1.05)  # Actual elapsed wall time over an open, real SQLite activity interval.
    denied = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "changed"})
    assert not denied.ok and execution.ledger.snapshot().activity_remaining_us == 0
    assert execution.ledger.snapshot().used["tool_executions"] == 1
    assert (scope.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"


def _reserve_then_wait(scope: ProjectScope, execution: ResearchExecutionScope, ctx: ToolContext,
                       channel: Any) -> None:
    from app.harness.tools.research_accounting import _reserve, prepare_tool
    from app.harness.tools.code import write_file_tool
    with bind_research_execution(execution), bind_project_scope(scope):
        plan = prepare_tool(execution, scope, "code.write_file", {"path": "candidate.py", "content": "changed"}, ctx, write_file_tool)
        reason, implementation = _reserve(plan)
        channel.send((reason, implementation is not None, plan.identifier))
        channel.recv()  # Actual process waits after commit and before any handler.


def test_real_process_death_after_reservation_prevents_reexecution(tmp_path: Path) -> None:
    import multiprocessing
    scope, execution, ctx = setup(tmp_path)
    processes = multiprocessing.get_context("spawn")
    parent, child = processes.Pipe()
    process = processes.Process(target=_reserve_then_wait, args=(scope, execution, ctx, child))
    process.start()
    child.close()
    try:
        assert parent.poll(10), "child did not commit its real reservation"
        reason, candidate_charged, identifier = parent.recv()
        assert reason is None and candidate_charged and identifier.startswith("tool:")
        process.kill()
        process.join(10)
        assert process.exitcode is not None and process.exitcode != 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()
    repeated = call(scope, execution, ctx, "code.write_file", {"path": "candidate.py", "content": "changed"})
    assert not repeated.ok and (scope.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"
    snapshot = execution.ledger.snapshot()
    assert snapshot.used["tool_executions"] == 1 and snapshot.used["implemented_candidates"] == 1
    with sqlite3.connect(execution.ledger.journal.path) as connection:
        assert connection.execute("SELECT state FROM research_reservations").fetchall() == [("reserved",), ("reserved",)]
    assert not receipts(scope)  # No handler or completion evidence was fabricated.


def test_missing_contract_file_with_only_metadata_marker_still_blocks(tmp_path: Path) -> None:
    scope, _execution, ctx = setup(tmp_path)
    (scope.run_root / "input/research_task.v1.json").unlink()
    # Remove no authority: the real journal must still reject a missing frozen input.
    result = asyncio.run(get_registry().dispatch("code.write_file", {"path": "candidate.py", "content": "changed"}, ctx))
    assert not result.ok and not receipts(scope)
    assert (scope.candidate_root / "candidate.py").read_text() == "VALUE = 1\n"
