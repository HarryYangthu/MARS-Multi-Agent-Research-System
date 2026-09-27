"""Conservative budget adapter for actual, host-scoped source-file tools.

This is not a scheduler. Uncertain executions retain their reservation; no
retry or additional tool capability is inferred from model-controlled input.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from filelock import FileLock, Timeout
from jsonschema import validate

from app.harness.persistence import atomic_write_json
from app.harness.runtime.project_scope import ProjectScope, current_project_scope, validated_diff_paths
from app.harness.runtime.research_budget_ledger import BudgetAmounts, BudgetReservation, BudgetSettlement
from app.harness.runtime.research_execution_scope import (
    ResearchExecutionScope, bound_research_execution, current_research_execution,
)
from app.settings import repo_root

if TYPE_CHECKING:
    from app.harness.tools.registry import ToolContext, ToolFn, ToolResult


def digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def bind_context(ctx: ToolContext) -> tuple[ToolContext, ResearchExecutionScope | None, ProjectScope | None]:
    """Validate host identities before registry tracing can write any files."""
    execution = bound_research_execution()
    project = current_project_scope(ctx.project, ctx.run_id)
    if execution is None and project is None:
        if ctx.extra.get("research_task_sha256") is not None:
            raise ValueError("contract context requires host scopes")
        roots = {repo_root() / "runs" / ctx.run_id}
        if ctx.extra.get("run_root"):
            roots.add(Path(str(ctx.extra["run_root"])))
        for root in roots:
            current_research_execution(root)
            metadata_path = root / "run_meta.json"
            if metadata_path.is_symlink():
                raise ValueError("run metadata cannot be redirected")
            if metadata_path.is_file():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(metadata, dict) and metadata.get("research_task_sha256") is not None:
                    raise ValueError("saved contract requires host scopes")
        return ctx, None, None
    if execution is None or project is None:
        raise ValueError("contract tools require both host execution and project scope")
    if (execution.root != project.run_root or execution.ledger.journal.run_id != ctx.run_id
            or execution.ledger.task_sha256 != project.task_sha256):
        raise ValueError("contract tool scopes disagree")
    raw_root = ctx.extra.get("run_root")
    if raw_root is not None and Path(str(raw_root)).resolve() != execution.root:
        raise ValueError("tool context redirects its host run root")
    execution.ledger.snapshot()
    return replace(ctx, extra={**ctx.extra, "run_root": str(execution.root)},
                   project_repo_root=str(project.candidate_root)), execution, project


@dataclass(frozen=True)
class ToolPlan:
    execution: ResearchExecutionScope
    project: ProjectScope
    name: str
    args: dict[str, Any]
    paths: tuple[str, ...]
    before: dict[str, object]
    fingerprint: str
    backup: tuple[str, str] | None = None
    requested_version_digest: str | None = None

    @property
    def identifier(self) -> str:
        return "tool:" + self.fingerprint.removeprefix("sha256:")


def _states(scope: ProjectScope, paths: tuple[str, ...], *, write: bool) -> dict[str, object]:
    out: dict[str, object] = {}
    for name in paths:
        target = scope.resolve_file(name, write=write)
        out[name] = ({"sha256": "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
                      "executable": bool(target.stat().st_mode & 0o111)} if target.exists() else None)
    return out


def _rollback(scope: ProjectScope, value: str) -> tuple[str, list[dict[str, Any]], str]:
    given = Path(value)
    relative = given.relative_to(scope.run_root).as_posix() if given.is_absolute() else value
    if not relative.startswith("coding/tool_applications/"):
        raise ValueError("rollback must belong to this run")
    path = scope.run_file(relative, must_exist=True)
    raw = path.read_bytes()
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("schema") != "tool_rollback.v1"
            or data.get("run_id") != scope.run_id or data.get("project") != scope.project
            or not isinstance(data.get("snapshots"), list) or not data["snapshots"]):
        raise ValueError("rollback identity is invalid")
    snapshots: list[dict[str, Any]] = []
    for item in data["snapshots"]:
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                or type(item.get("existed")) is not bool or not isinstance(item.get("content"), str)):
            raise ValueError("rollback snapshot is invalid")
        content_hash = "sha256:" + hashlib.sha256(item["content"].encode()).hexdigest()
        if item["existed"] and item.get("sha256") != content_hash:
            raise ValueError("rollback content hash differs")
        scope.resolve_file(item["path"], write=True)
        snapshots.append({"path": item["path"], "existed": item["existed"],
                          "content_sha256": content_hash if item["existed"] else None})
    if len({item["path"] for item in snapshots}) != len(snapshots):
        raise ValueError("duplicate rollback target")
    return relative, snapshots, "sha256:" + hashlib.sha256(raw).hexdigest()


def prepare_tool(execution: ResearchExecutionScope, project: ProjectScope, name: str,
                 args: dict[str, Any], ctx: ToolContext, handler: ToolFn) -> ToolPlan:
    # Identity-check the real adapters, not merely their configurable names.
    from app.harness.tools import code
    adapters: dict[str, ToolFn] = {
        "code.repo_reader": code.repo_reader_tool, "code.write_file": code.write_file_tool,
        "code.apply_patch": code.apply_patch_tool, "code.delete_file": code.delete_file_tool,
        "code.rollback_patch": code.rollback_patch_tool,
    }
    if name not in adapters or handler is not adapters[name]:
        raise ValueError("unsupported contract budget adapter")
    if ctx.dry_run:
        raise ValueError("contract dry-run tool accounting is not admitted")
    fields = {"code.repo_reader": {"path", "char_offset"}, "code.write_file": {"path", "content"},
              "code.apply_patch": {"diff", "version", "files"}, "code.delete_file": {"path"},
              "code.rollback_patch": {"rollback_ref"}}[name]
    if args.keys() - fields - {"_approval_id"}:
        raise ValueError("unsupported contract tool argument")
    actual = dict(args)
    meaningful: dict[str, Any]
    backup: tuple[str, str] | None = None
    requested_version_digest = None
    if name == "code.apply_patch":
        diff = args.get("diff")
        if not isinstance(diff, str) or not diff:
            raise ValueError("contract patch requires textual diff")
        paths = validated_diff_paths(diff)
        if "files" in args:
            declared = args["files"]
            if not isinstance(declared, list) or any(not isinstance(item, dict) or set(item) != {"path"} for item in declared):
                raise ValueError("patch file declaration is invalid")
            if sorted(item["path"] for item in declared) != sorted(paths):
                raise ValueError("patch file declaration differs from actual diff")
        actual["files"] = [{"path": path} for path in paths]
        meaningful = {"diff": diff}
        requested_version_digest = digest(args["version"]) if "version" in args else None
    elif name == "code.rollback_patch":
        reference, snapshots, backup_hash = _rollback(project, str(args["rollback_ref"]).strip())
        paths = tuple(item["path"] for item in snapshots)
        actual["rollback_ref"] = reference
        backup = reference, backup_hash
        meaningful = {"snapshots": snapshots}
    else:
        path = str(args["path"]).strip()
        target = project.resolve_file(path, write=False,
                                      must_exist=name in {"code.repo_reader", "code.delete_file"})
        paths = (target.relative_to(project.candidate_root).as_posix(),)
        actual["path"] = paths[0]
        meaningful = {"path": paths[0]}
        if name == "code.repo_reader":
            offset = args.get("char_offset", 0)
            raw = target.read_text(encoding="utf-8", errors="replace")
            if (target.suffix.lower() not in code._TEXT_SUFFIXES or type(offset) is not int
                    or offset < 0 or (raw and offset >= len(raw))):
                raise ValueError("unsupported source read or character offset")
            actual["char_offset"] = offset
            meaningful["char_offset"] = offset
        elif name == "code.write_file":
            meaningful["content"] = args["content"]
    before = _states(project, paths, write=False)
    fingerprint = digest({"schema": "contract_tool_operation.v1", "task": project.task_sha256,
        "candidate": project.candidate_root.relative_to(project.run_root).as_posix(),
        "snapshot": project.snapshot_id, "tool": name, "arguments": meaningful, "before": before})
    if name == "code.apply_patch":
        actual["version"] = "host_" + fingerprint.removeprefix("sha256:")
    return ToolPlan(execution, project, name, actual, paths, before, fingerprint, backup, requested_version_digest)


def _blocked(reason: str) -> ToolResult:
    from app.harness.tools.registry import ToolResult
    return ToolResult(ok=False, status="contract_tool_blocked", error=reason,
                      metadata={"handler_started": False, "budget_reserved": False})


def _reserve(plan: ToolPlan) -> tuple[str | None, BudgetReservation | None]:
    tool = BudgetReservation(reservation_id=plan.identifier, operation_id=plan.identifier,
        operation_fingerprint=plan.fingerprint, kind="tool", amounts=BudgetAmounts(tool_executions=1))
    implementation = None
    if plan.name != "code.repo_reader":
        fingerprint = digest({"schema": "code_candidate_entry.v1", "task": plan.project.task_sha256,
            "snapshot": plan.project.snapshot_id,
            "candidate": plan.project.candidate_root.relative_to(plan.project.run_root).as_posix()})
        identity = "code-candidate:" + fingerprint.removeprefix("sha256:")
        implementation = BudgetReservation(reservation_id=identity, operation_id=identity,
            operation_fingerprint=fingerprint, kind="implementation", amounts=BudgetAmounts(implemented_candidates=1))
    specifications = (implementation, tool) if implementation is not None else (tool,)
    with plan.execution.ledger.transaction() as transaction:
        observed = time.time_ns() // 1000
        # Retain the clock observation on a normal budget denial even when the
        # second reservation requires rolling the admission group back.
        transaction.observe_clock(now_us=observed)
        transaction.connection.execute("SAVEPOINT contract_tool_admission")
        try:
            new_implementation = None
            for specification in specifications:
                admission = transaction.reserve(specification, now_us=observed)
                if not admission.admitted or (specification is tool and admission.replay):
                    transaction.connection.execute("ROLLBACK TO contract_tool_admission")
                    transaction.connection.execute("RELEASE contract_tool_admission")
                    transaction.observe_clock(now_us=observed)
                    return admission.reason or "operation already reserved or observed", None
                if specification is implementation and not admission.replay:
                    new_implementation = implementation
        except BaseException:
            transaction.connection.execute("ROLLBACK TO contract_tool_admission")
            transaction.connection.execute("RELEASE contract_tool_admission")
            raise
        transaction.connection.execute("RELEASE contract_tool_admission")
        return None, new_implementation


def _receipt(plan: ToolPlan, result: ToolResult, after: dict[str, object] | None, *, outcome: str, handler_started: bool = True) -> tuple[str, str]:
    relative = "resources/tool_receipts/" + plan.fingerprint.removeprefix("sha256:") + ".json"
    path = plan.project.run_file(relative)
    if path.exists():
        raise ValueError("tool receipt already exists")
    data = {"schema": "contract_tool_receipt.v1", "operation_id": plan.identifier,
        "operation_fingerprint": plan.fingerprint, "run_id": plan.project.run_id,
        "task_sha256": plan.project.task_sha256, "tool": plan.name, "outcome": outcome,
        "before": plan.before, "after": after, "handler_started": handler_started,
        "changed_paths": [name for name in plan.paths if after is not None and plan.before[name] != after[name]],
        "output_sha256": digest(result.output), "error_sha256": digest(result.error) if result.error else None,
        "host_version": plan.args.get("version"), "requested_version_sha256": plan.requested_version_digest}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)
    return relative, digest(data)


def _unknown(plan: ToolPlan, result: ToolResult, *, reason: str, handler_started: bool, implementation: BudgetReservation | None) -> None:
    errors: list[str] = []
    try:
        after = _states(plan.project, plan.paths, write=plan.name != "code.repo_reader")
        reference, _ = _receipt(plan, result, after, outcome="unknown", handler_started=handler_started)
        result.evidence_refs.append(reference)
    except Exception as exc:
        errors.append(type(exc).__name__)
    try:
        with plan.execution.ledger.transaction() as transaction:
            transaction.mark_unknown(plan.identifier, reason=reason,
                observed_lower_bound=BudgetAmounts(tool_executions=int(handler_started)))
            if implementation is not None:
                transaction.mark_unknown(implementation.reservation_id, reason=reason,
                    observed_lower_bound=BudgetAmounts(implemented_candidates=int(handler_started)))
    except Exception as exc:
        # SQLite still contains the original reservation if this update fails.
        errors.append(type(exc).__name__)
    result.metadata.update({"handler_started": handler_started, "budget_reserved": True,
                            "budget_outcome": "unknown", "operation_id": plan.identifier})
    if errors:
        result.metadata["accounting_errors"] = errors


async def execute_tool(plan: ToolPlan, handler: ToolFn, ctx: ToolContext,
                       output_schema: dict[str, Any], timeout_seconds: float) -> ToolResult:
    from app.harness.tools.registry import ToolResult
    deadline = time.monotonic() + timeout_seconds
    lock_path = plan.project.run_file("resources/.contract-tools.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Never share a reentrant lock instance between asyncio tasks on one thread.
    lock = FileLock(lock_path, thread_local=False)
    while True:
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            if time.monotonic() >= deadline or plan.execution.ledger.snapshot().activity_remaining_us <= 0:
                with plan.execution.ledger.transaction() as transaction:
                    transaction.observe_clock()
                return _blocked("contract tool lock wait exhausted")
            await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
    try:
        if _states(plan.project, plan.paths, write=plan.name != "code.repo_reader") != plan.before:
            return _blocked("source changed after tool preflight; prepare a new operation")
        if plan.backup:
            path = plan.project.run_file(plan.backup[0], must_exist=True)
            if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != plan.backup[1]:
                return _blocked("rollback changed after tool preflight")
        remaining = plan.execution.ledger.snapshot().activity_remaining_us / 1_000_000
        timeout = min(deadline - time.monotonic(), remaining)
        if timeout <= 0:
            with plan.execution.ledger.transaction() as transaction:
                transaction.observe_clock()
            return _blocked("contract tool activity or timeout exhausted")
        reason, implementation = _reserve(plan)
        if reason is not None:
            return _blocked("contract tool refused: " + reason)
        handler_started = False
        async def invoke() -> ToolResult:
            nonlocal handler_started
            handler_started = True
            return await handler(plan.args, ctx)
        try:
            # Includes synchronous handler elapsed time in the post-check; asyncio
            # cannot preempt a blocking filesystem syscall or SQLite transaction.
            timeout = min(deadline - time.monotonic(), plan.execution.ledger.snapshot().activity_remaining_us / 1_000_000)
            if timeout <= 0:
                raise TimeoutError("activity exhausted after reservation")
            handler_deadline = time.monotonic() + timeout
            result = await asyncio.wait_for(invoke(), timeout=timeout)
            handler_ended_us = time.time_ns() // 1000
            if time.monotonic() > handler_deadline:
                result = ToolResult(ok=False, error="tool exceeded its remaining activity deadline", status="timeout")
            if not result.ok:
                _unknown(plan, result, reason="tool_failed_or_uncertain", handler_started=handler_started, implementation=implementation)
                return result
            validate(instance=result.output, schema=output_schema)
            after = _states(plan.project, plan.paths, write=plan.name != "code.repo_reader")
            if plan.name == "code.repo_reader" and after != plan.before:
                raise ValueError("source changed during read")
            reference, evidence_hash = _receipt(plan, result, after, outcome="observed_success")
            with plan.execution.ledger.transaction() as transaction:
                transaction.settle(plan.identifier, BudgetSettlement(actual=BudgetAmounts(tool_executions=1),
                    outcome="success", evidence_refs=(reference,), evidence_fingerprint=evidence_hash),
                    activity_ended_us=handler_ended_us)
                if implementation is not None:
                    transaction.settle(implementation.reservation_id,
                        BudgetSettlement(actual=implementation.amounts, outcome="success",
                            evidence_refs=(reference,), evidence_fingerprint=evidence_hash),
                        activity_ended_us=handler_ended_us)
            result.evidence_refs.append(reference)
            result.metadata.update({"handler_started": True, "budget_reserved": True,
                "budget_outcome": "settled", "operation_id": plan.identifier,
                "candidate_entry_charged": implementation is not None,
                "changed_paths": [name for name in plan.paths if plan.before[name] != after[name]]})
            if "version" in plan.args:
                result.metadata["host_version"] = plan.args["version"]
            return result
        except asyncio.CancelledError:
            result = ToolResult(ok=False, status="cancelled", error="contract tool cancelled")
            _unknown(plan, result, reason="tool_cancelled", handler_started=handler_started, implementation=implementation)
            raise
        except Exception as exc:
            result = ToolResult(ok=False, status="contract_tool_uncertain",
                                error="contract tool outcome is uncertain: " + type(exc).__name__)
            _unknown(plan, result, reason="tool_failed_or_uncertain", handler_started=handler_started, implementation=implementation)
            return result
    finally:
        lock.release()
