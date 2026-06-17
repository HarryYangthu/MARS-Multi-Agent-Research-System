"""Tool registry + dispatch with Gate 5 hook.

★ CLAUDE.md hard constraint: Gate 5 (baseline_compatibility) sits **here**,
on the dispatch path — not as a RunGraph checkpoint. Every tool call goes
through ``dispatch()`` and is screened by the Gate before the tool runs.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable

import yaml
from jsonschema import ValidationError, validate
from loguru import logger

from app.harness.observability.tracing import TraceRecorder
from app.settings import repo_root


ToolFn = Callable[[dict[str, Any], "ToolContext"], Awaitable["ToolResult"]]

DEFAULT_REDACT_KEYS: frozenset[str] = frozenset(
    {"api_key", "apikey", "authorization", "cookie", "password", "secret", "token"}
)
DEFAULT_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": True}


@dataclass
class ToolContext:
    run_id: str
    project: str
    agent: str
    extra: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    span_id: str = ""
    user_id: str = ""
    session_id: str = ""
    workspace_root: str = ""
    project_repo_root: str = ""
    dry_run: bool = False
    approval_mode: str = "auto"


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None
    blocked_by_gate: str | None = None
    status: str | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    rollback_ref: str | None = None
    evidence_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status is not None:
            return
        if self.blocked_by_gate:
            self.status = "blocked"
        elif self.requires_approval:
            self.status = "requires_approval"
        elif self.ok:
            self.status = "success"
        else:
            self.status = "error"


@dataclass(frozen=True)
class ToolPolicy:
    mutation_level: str = "read"
    allowed_agents: tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    requires_approval: bool = False
    network: bool = False
    command_allowlist: tuple[tuple[str, ...], ...] = ()
    redaction: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    namespace: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_INPUT_SCHEMA))
    output_schema: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_INPUT_SCHEMA))
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    bridge_only: bool = False


@dataclass
class ToolExecutionRecord:
    call_id: str
    run_id: str
    project: str
    agent: str
    tool: str
    status: str
    started_at: str
    ended_at: str
    duration_ms: float
    args: dict[str, Any]
    error: str | None = None
    blocked_by_gate: str | None = None
    rollback_ref: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._gates: list[Callable[[str, dict[str, Any], ToolContext], Awaitable["GateDecision"]]] = []

    def register(
        self,
        name: str,
        fn: ToolFn,
        *,
        spec: ToolSpec | None = None,
        override: bool = False,
    ) -> None:
        if name in self._tools and not override:
            raise ValueError(f"tool '{name}' already registered")
        self._tools[name] = fn
        self._specs[name] = _spec_from_config(spec or _default_spec(name))

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def spec(self, name: str) -> ToolSpec | None:
        if name in self._specs:
            return self._specs[name]
        from app.harness.tools.config import tool_config

        cfg = tool_config(name)
        if cfg.bridge_only:
            return _spec_from_config(_default_spec(name, bridge_only=True))
        return None

    def specs(self, *, include_bridge_only: bool = False) -> list[ToolSpec]:
        out = list(self._specs.values())
        if include_bridge_only:
            from app.harness.tools.config import load_tool_configs

            for name, cfg in load_tool_configs().items():
                if name not in self._specs and cfg.bridge_only:
                    out.append(_spec_from_config(_default_spec(name, bridge_only=True)))
        return sorted(out, key=lambda item: item.name)

    def install_gate(
        self,
        check: Callable[[str, dict[str, Any], ToolContext], Awaitable["GateDecision"]],
    ) -> None:
        self._gates.append(check)

    async def dispatch(
        self, tool_name: str, args: dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        call_id = uuid.uuid4().hex
        started_at = _now()
        started = time.perf_counter()
        span = _start_tool_span(call_id=call_id, tool_name=tool_name, args=args, ctx=ctx)
        _record_tool_event(
            kind="tool.started",
            call_id=call_id,
            tool_name=tool_name,
            args=args,
            ctx=ctx,
            payload={"args": args},
        )
        result: ToolResult
        if tool_name not in self._tools:
            result = ToolResult(
                ok=False,
                error=f"unknown tool '{tool_name}'",
                status="unknown_tool",
            )
            _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
            return result

        spec = self._specs[tool_name]
        if not _tool_enabled(tool_name):
            result = ToolResult(
                ok=False,
                error=f"tool '{tool_name}' is disabled by configs/tools.yaml",
                status="disabled",
            )
            _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
            return result

        if not _allowed_for_agent(tool_name, ctx.agent, spec):
            result = ToolResult(
                ok=False,
                error=f"tool '{tool_name}' is not allowed for agent '{ctx.agent}'",
                status="not_allowed",
            )
            _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
            return result

        schema_error = _validate_args(args, spec.input_schema)
        if schema_error is not None:
            result = ToolResult(ok=False, error=schema_error, status="error")
            _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
            return result

        approval_reason = _approval_required_reason(tool_name, args, ctx, spec)
        if approval_reason:
            result = ToolResult(
                ok=False,
                error=approval_reason,
                status="requires_approval",
                requires_approval=True,
            )
            _record_pending_approval(
                tool_name=tool_name,
                args=args,
                ctx=ctx,
                result=result,
                call_id=call_id,
                reason=approval_reason,
            )
            _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
            return result

        # ★ Gate 5 (and any future gate hooks) run here, before the tool fn.
        for gate in self._gates:
            decision = await gate(tool_name, args, ctx)
            if decision.action == "require_human":
                result = ToolResult(
                    ok=False,
                    error=decision.reason,
                    status="requires_approval",
                    requires_approval=True,
                )
                _record_pending_approval(
                    tool_name=tool_name,
                    args=args,
                    ctx=ctx,
                    result=result,
                    call_id=call_id,
                    reason=decision.reason,
                )
                _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
                return result
            if decision.action == "block":
                logger.warning(
                    "Gate '{}' blocked tool '{}' (run={}, agent={}): {}",
                    decision.gate_id, tool_name, ctx.run_id, ctx.agent, decision.reason,
                )
                result = ToolResult(
                    ok=False,
                    error=decision.reason,
                    blocked_by_gate=decision.gate_id,
                    status="blocked",
                )
                _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
                return result
        try:
            result = await asyncio.wait_for(
                self._tools[tool_name](args, ctx),
                timeout=spec.policy.timeout_seconds,
            )
            if result.status is None:
                result.status = "success" if result.ok else "error"
            if result.status == "requires_approval":
                result.requires_approval = True
        except Exception as exc:
            logger.exception("tool '{}' raised", tool_name)
            result = ToolResult(ok=False, error=str(exc), status="error")
        _finalize_and_record(tool_name, args, ctx, result, started, started_at, call_id, span)
        return result


@dataclass
class GateDecision:
    gate_id: str
    action: str  # "allow" | "block" | "require_human"
    reason: str = ""


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _install_default_gates(_registry)
        _install_default_tools(_registry)
        _validate_agent_tool_references(_registry)
    return _registry


def reset_for_tests() -> ToolRegistry:
    global _registry
    _registry = ToolRegistry()
    _install_default_gates(_registry)
    _install_default_tools(_registry)
    _validate_agent_tool_references(_registry)
    return _registry


def _install_default_gates(reg: ToolRegistry) -> None:
    # Lazy import to avoid pulling gates/ during the very early bootstrap.
    from app.harness.gates.baseline_compatibility import gate_check

    reg.install_gate(gate_check)


def _install_default_tools(reg: ToolRegistry) -> None:
    from app.harness.tools.code import (
        apply_patch_tool,
        delete_file_tool,
        lint_tool,
        patch_generator_tool,
        repo_reader_tool,
        rollback_patch_tool,
        test_runner_tool,
        write_file_tool,
    )
    from app.harness.tools.execution import (
        batch_runner_tool,
        log_streamer_tool,
        metrics_collector_tool,
        simulation_runner_tool,
    )
    from app.harness.tools.knowledge import (
        baseline_match_tool,
        code_assets_tool,
        experiment_memory_tool,
        ingest_document_tool,
        kb_query_tool,
        methodology_tool,
        run_archive_tool,
    )
    from app.harness.tools.search import arxiv_search_tool, local_docs_tool, web_search_tool

    # search.*
    reg.register("search.local_docs", local_docs_tool)
    reg.register("search.arxiv_search", arxiv_search_tool)
    reg.register("search.web_search", web_search_tool)
    # knowledge.*
    reg.register("knowledge.kb_query", kb_query_tool)
    reg.register("knowledge.baseline_match", baseline_match_tool)
    reg.register("knowledge.experiment_memory", experiment_memory_tool)
    reg.register("knowledge.code_assets", code_assets_tool)
    reg.register("knowledge.methodology", methodology_tool)
    reg.register("knowledge.run_archive", run_archive_tool)
    reg.register("knowledge.ingest_document", ingest_document_tool)
    # code.*
    reg.register("code.repo_reader", repo_reader_tool)
    reg.register("code.patch_generator", patch_generator_tool)
    reg.register("code.apply_patch", apply_patch_tool)
    reg.register("code.write_file", write_file_tool)
    reg.register("code.delete_file", delete_file_tool)
    reg.register("code.rollback_patch", rollback_patch_tool)
    reg.register("code.test_runner", test_runner_tool)
    reg.register("code.lint", lint_tool)
    # execution.*
    reg.register("execution.simulation_runner", simulation_runner_tool)
    reg.register("execution.batch_runner", batch_runner_tool)
    reg.register("execution.log_streamer", log_streamer_tool)
    reg.register("execution.metrics_collector", metrics_collector_tool)


def _validate_agent_tool_references(reg: ToolRegistry) -> None:
    """Fail fast when agent config references a missing non-bridge tool."""
    from app.harness.llm.model_registry import list_agent_configs
    from app.harness.tools.config import load_tool_configs

    configured = load_tool_configs()
    missing: list[str] = []
    for agent in list_agent_configs():
        for tool_name in agent.tools:
            if reg.has(tool_name):
                continue
            cfg = configured.get(tool_name)
            if cfg is not None and cfg.bridge_only:
                continue
            missing.append(f"{agent.name}:{tool_name}")
    if missing:
        raise RuntimeError(
            "configs/agents.yaml references unregistered tools: " + ", ".join(missing)
        )


def _allowed_for_agent(tool_name: str, agent_name: str, spec: ToolSpec) -> bool:
    if agent_name in {"system", "bridge"}:
        return True
    if spec.policy.allowed_agents and agent_name not in spec.policy.allowed_agents:
        return False
    try:
        from app.harness.llm.model_registry import get_agent_config

        cfg = get_agent_config(agent_name)
    except KeyError:
        logger.warning("no agent config for '{}'; blocking tool '{}'", agent_name, tool_name)
        return False
    return tool_name in set(cfg.tools)


def _tool_enabled(tool_name: str) -> bool:
    from app.harness.tools.config import tool_config

    return tool_config(tool_name).enabled


def _finalize_and_record(
    tool_name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    result: ToolResult,
    started: float,
    started_at: str,
    call_id: str,
    span: Any | None,
) -> None:
    result.duration_ms = round((time.perf_counter() - started) * 1000, 3)
    result.metadata.setdefault("tool_call_id", call_id)
    ended_at = _now()
    _record_tool_event(
        kind=_event_kind(result),
        call_id=call_id,
        tool_name=tool_name,
        args=args,
        ctx=ctx,
        payload={
            "status": result.status,
            "ok": result.ok,
            "error": result.error,
            "blocked_by_gate": result.blocked_by_gate,
            "requires_approval": result.requires_approval,
            "rollback_ref": result.rollback_ref,
            "evidence_refs": result.evidence_refs,
            "metadata": result.metadata,
            "duration_ms": result.duration_ms,
        },
    )
    for event in result.events:
        event_kind = str(event.get("event") or "")
        if not event_kind:
            continue
        _record_tool_event(
            kind=event_kind,
            call_id=call_id,
            tool_name=tool_name,
            args=args,
            ctx=ctx,
            payload={key: value for key, value in event.items() if key != "event"},
        )
    _record_invocation(
        tool_name=tool_name,
        args=args,
        ctx=ctx,
        result=result,
        record=ToolExecutionRecord(
            call_id=call_id,
            run_id=ctx.run_id,
            project=ctx.project,
            agent=ctx.agent,
            tool=tool_name,
            status=str(result.status or ""),
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=result.duration_ms or 0.0,
            args=_redact(args, _redaction_keys(tool_name)),
            error=result.error,
            blocked_by_gate=result.blocked_by_gate,
            rollback_ref=result.rollback_ref,
            evidence_refs=result.evidence_refs,
        ),
    )
    _finish_tool_span(
        span,
        status="ok" if result.ok else "error",
        attributes={
            "tool_status": result.status,
            "blocked_by_gate": result.blocked_by_gate,
            "requires_approval": result.requires_approval,
            "rollback_ref": result.rollback_ref,
            "error": result.error,
            "duration_ms": result.duration_ms,
        },
    )


def _record_invocation(
    *,
    tool_name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    result: ToolResult,
    record: ToolExecutionRecord,
) -> None:
    run_root_raw = ctx.extra.get("run_root") if ctx.extra else None
    run_root = Path(str(run_root_raw)) if run_root_raw else repo_root() / "runs" / ctx.run_id
    if not ctx.run_id or not run_root.exists():
        return
    events_dir = run_root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "run_id": ctx.run_id,
        "project": ctx.project,
        "agent": ctx.agent,
        "tool": tool_name,
        "args": _redact(_safe_json(args), _redaction_keys(tool_name)),
        "ok": result.ok,
        "status": result.status,
        "error": result.error,
        "blocked_by_gate": result.blocked_by_gate,
        "requires_approval": result.requires_approval,
        "rollback_ref": result.rollback_ref,
        "evidence_refs": result.evidence_refs,
        "metadata": result.metadata,
        "duration_ms": result.duration_ms,
    }
    with (events_dir / "tool_calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    applications = run_root / "coding" / "tool_applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / f"{record.call_id}.json").write_text(
        json.dumps(asdict(record), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
    return value


def _default_spec(name: str, *, bridge_only: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        namespace=name.split(".", 1)[0] if "." in name else "custom",
        description=f"{name} tool",
        bridge_only=bridge_only,
    )


def _spec_from_config(spec: ToolSpec) -> ToolSpec:
    from app.harness.tools.config import tool_config

    cfg = tool_config(spec.name)
    policy = ToolPolicy(
        mutation_level=cfg.mutation_level or spec.policy.mutation_level,
        allowed_agents=cfg.allowed_agents or spec.policy.allowed_agents,
        timeout_seconds=cfg.timeout_seconds or spec.policy.timeout_seconds,
        requires_approval=cfg.requires_approval or spec.policy.requires_approval,
        network=cfg.network or spec.policy.network,
        command_allowlist=cfg.command_allowlist or spec.policy.command_allowlist,
        redaction=cfg.redaction or spec.policy.redaction,
    )
    return ToolSpec(
        name=spec.name,
        namespace=spec.namespace,
        description=cfg.description or spec.description,
        input_schema=cfg.input_schema or spec.input_schema,
        output_schema=cfg.output_schema or spec.output_schema,
        policy=policy,
        bridge_only=cfg.bridge_only or spec.bridge_only,
    )


def _validate_args(args: dict[str, Any], schema: dict[str, Any]) -> str | None:
    try:
        validate(instance=args, schema=schema)
    except ValidationError as exc:
        return f"tool args failed schema validation: {exc.message}"
    return None


def _approval_required_reason(
    tool_name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    spec: ToolSpec,
) -> str:
    if spec.policy.mutation_level != "write":
        return ""
    if _approval_is_valid(tool_name=tool_name, args=args, ctx=ctx):
        return ""
    if spec.policy.requires_approval:
        return f"tool '{tool_name}' requires approval by policy"
    if tool_name == "code.delete_file":
        return "delete_file requires human approval"
    touched = _touched_files(args)
    threshold = _large_refactor_threshold()
    if len(touched) > threshold:
        return f"large refactor requires approval: {len(touched)} files > threshold {threshold}"
    return ""


def _record_pending_approval(
    *,
    tool_name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    result: ToolResult,
    call_id: str,
    reason: str,
) -> None:
    approval_id = call_id
    result.metadata["approval_id"] = approval_id
    result.metadata["approval_status"] = "pending"
    run_root = _run_root(ctx)
    if run_root is None:
        return
    target_dir = run_root / "events" / "tool_approvals"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{approval_id}.json"
    payload = {
        "schema": "tool_approval_request.v1",
        "approval_id": approval_id,
        "call_id": call_id,
        "run_id": ctx.run_id,
        "project": ctx.project,
        "agent": ctx.agent,
        "tool": tool_name,
        "reason": reason,
        "status": "pending",
        "created_at": _now(),
        "args": _safe_json(args),
        "context": {
            "run_id": ctx.run_id,
            "project": ctx.project,
            "agent": ctx.agent,
            "trace_id": ctx.trace_id,
            "span_id": ctx.span_id,
            "user_id": ctx.user_id,
            "session_id": ctx.session_id,
            "workspace_root": ctx.workspace_root,
            "project_repo_root": ctx.project_repo_root,
            "dry_run": ctx.dry_run,
            "extra": _safe_json(_approval_extra(ctx)),
        },
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    result.artifacts.append({"kind": "tool_approval_request", "path": str(target)})


def _approval_extra(ctx: ToolContext) -> dict[str, Any]:
    allowed = {"run_root", "project_repo_root"}
    return {
        key: value
        for key, value in (ctx.extra or {}).items()
        if key in allowed and isinstance(value, str)
    }


def _approval_is_valid(*, tool_name: str, args: dict[str, Any], ctx: ToolContext) -> bool:
    if ctx.approval_mode != "approved" or ctx.agent not in {"bridge", "system"}:
        return False
    approval_id = str(args.get("_approval_id", "") or "")
    if not approval_id:
        return False
    run_root = _run_root(ctx)
    if run_root is None:
        return False
    path = run_root / "events" / "tool_approvals" / f"{approval_id}.json"
    if not path.is_file():
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(record, dict)
        and record.get("status") == "approved"
        and record.get("tool") == tool_name
        and record.get("run_id") == ctx.run_id
    )


def _touched_files(args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if args.get("path"):
        out.append(str(args["path"]))
    files = args.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and item.get("path"):
                out.append(str(item["path"]))
            elif isinstance(item, str):
                out.append(item)
    diff = args.get("diff")
    if isinstance(diff, str):
        for match in re.finditer(r"^\+\+\+\s+b/(.+)$", diff, flags=re.MULTILINE):
            path = match.group(1).strip()
            if path != "/dev/null":
                out.append(path)
        for match in re.finditer(r"^---\s+a/(.+)$", diff, flags=re.MULTILINE):
            path = match.group(1).strip()
            if path != "/dev/null":
                out.append(path)
    return sorted(set(out))


def _large_refactor_threshold() -> int:
    path = repo_root() / "configs" / "gates.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        return 5
    gates = raw.get("gates", {}) if isinstance(raw, dict) else {}
    large = gates.get("large_refactor", {}) if isinstance(gates, dict) else {}
    value = large.get("files_changed_threshold", 5) if isinstance(large, dict) else 5
    try:
        return int(value)
    except (TypeError, ValueError):
        return 5


def _start_tool_span(
    *,
    call_id: str,
    tool_name: str,
    args: dict[str, Any],
    ctx: ToolContext,
) -> Any | None:
    run_root = _run_root(ctx)
    if run_root is None:
        return None
    recorder = TraceRecorder(_RunRef(run_id=ctx.run_id, root=run_root))
    return recorder.start_span(
        name=f"tool:{tool_name}",
        kind="tool",
        attributes={
            "run_id": ctx.run_id,
            "project": ctx.project,
            "agent": ctx.agent,
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "args": _redact(args, _redaction_keys(tool_name)),
        },
        parent_span_id=ctx.span_id or None,
    )


def _finish_tool_span(span: Any | None, *, status: str, attributes: dict[str, Any]) -> None:
    if span is None:
        return
    recorder = getattr(span, "recorder", None)
    span_id = getattr(span, "span_id", "")
    if recorder is None or not span_id:
        return
    recorder.finish_span(span_id, status=status, attributes=_redact(attributes))


def _record_tool_event(
    *,
    kind: str,
    call_id: str,
    tool_name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    payload: dict[str, Any],
) -> None:
    run_root = _run_root(ctx)
    if run_root is None:
        return
    events_dir = run_root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "tool_event.v1",
        "event_id": uuid.uuid4().hex,
        "timestamp": _now(),
        "event": kind,
        "run_id": ctx.run_id,
        "project": ctx.project,
        "agent": ctx.agent,
        "tool": tool_name,
        "call_id": call_id,
        **_redact(payload, _redaction_keys(tool_name)),
    }
    with (events_dir / "tool_events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _event_kind(result: ToolResult) -> str:
    if result.status == "blocked":
        return "tool.blocked"
    if result.status == "requires_approval":
        return "tool.requires_approval"
    if result.ok:
        return "tool.completed"
    return "tool.failed"


def _run_root(ctx: ToolContext) -> Path | None:
    run_root_raw = ctx.extra.get("run_root") if ctx.extra else None
    run_root = Path(str(run_root_raw)) if run_root_raw else repo_root() / "runs" / ctx.run_id
    if not ctx.run_id or not run_root.exists():
        return None
    return run_root


@dataclass
class _RunRef:
    run_id: str
    root: Path

    def subdir(self, name: str) -> Path:
        return self.root / name


def _redaction_keys(tool_name: str) -> frozenset[str]:
    from app.harness.tools.config import tool_config

    configured = {item.lower() for item in tool_config(tool_name).redaction}
    spec_keys: set[str] = set()
    if _registry is not None and tool_name in _registry._specs:
        spec_keys = {item.lower() for item in _registry._specs[tool_name].policy.redaction}
    return frozenset(set(DEFAULT_REDACT_KEYS) | configured | spec_keys)


def _redact(value: Any, keys: Iterable[str] = DEFAULT_REDACT_KEYS) -> Any:
    redaction_keys = {str(key).lower() for key in keys}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            out[key_text] = (
                "[redacted]"
                if key_text.lower() in redaction_keys
                else _redact(item, redaction_keys)
            )
        return out
    if isinstance(value, list):
        return [_redact(item, redaction_keys) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, redaction_keys) for item in value]
    return value


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
