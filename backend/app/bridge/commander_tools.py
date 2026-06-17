"""Commander tool set — the master Agent's hands.

Thin wrappers over the EXISTING engine (orchestrator / hitl / kb / bridge_agent).
Nothing here re-implements the self-healing loop; it just exposes callable
verbs the Commander LLM can pick from. Dependencies are injected via
``ToolContext`` so this stays in the bridge layer (no import of api/).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.observability.events import write_event
from app.harness.schema.validator import validate_document
from app.harness.tools.config import tool_config
from app.harness.tools.registry import (
    ToolContext as HarnessToolContext,
    ToolResult as HarnessToolResult,
    ToolSpec as HarnessToolSpec,
    get_registry as get_tool_registry,
)
from app.storage.artifact_store import SCHEMA_TO_AGENT, ArtifactStore
from app.storage.run_store import RunStore

if TYPE_CHECKING:
    from app.bridge.commander_session import CommanderSession

# agent -> (schema_id, stem)
_AGENT_TO_SCHEMA: dict[str, tuple[str, str]] = {
    agent: (sid, stem) for sid, (agent, stem) in SCHEMA_TO_AGENT.items()
}


@dataclass
class ToolContext:
    orchestrator: Orchestrator
    session: "CommanderSession"
    run_store: RunStore


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON-schema-ish, shown to the LLM
    handler: ToolHandler


_COMMANDER_CONTEXTS: dict[str, ToolContext] = {}
_REGISTRY_ADAPTERS_INSTALLED = False


# --------------------------------------------------------------------- tools


async def _create_and_start_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    created = await _create_run(args, ctx)
    if not created.get("ok"):
        return created
    return await _start_run({"run_id": created["run_id"]}, ctx)


async def _create_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    entrypoint = str(args.get("entrypoint", "pipeline"))
    user_request = str(args.get("user_request", ""))
    seed_artifact = args.get("seed_artifact")
    task = str(args.get("task") or user_request[:60] or "commander_run")

    request = RunRequest(
        task=task,
        project=ctx.session.project,
        entrypoint=entrypoint,  # type: ignore[arg-type]
        standalone=bool(args.get("standalone", False)),
        user_request=user_request,
        auto_approve=ctx.session.auto_mode,
    )
    rsession = ctx.orchestrator.create_session(request)
    run_id = rsession.run.run_id

    # Optional: seed the entrypoint agent's first artifact (skip its LLM draft).
    if seed_artifact and entrypoint in _AGENT_TO_SCHEMA:
        schema_id, _stem = _AGENT_TO_SCHEMA[entrypoint]
        result = validate_document(str(seed_artifact), expected_schema=schema_id)
        if not result.valid:
            return {
                "ok": False,
                "error": "seed_artifact failed schema validation",
                "schema": schema_id,
                "errors": [{"path": e.path, "message": e.message} for e in result.errors],
            }
        ArtifactStore(rsession.run).write(text=str(seed_artifact), expected_schema=schema_id)

    ctx.session.linked_run_id = run_id
    return {
        "ok": True,
        "run_id": run_id,
        "entrypoint": entrypoint,
        "auto_approve": ctx.session.auto_mode,
        "states": {k: s.value for k, s in rsession.graph.all_states().items()},
    }


async def _start_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    if not run_id:
        return {"ok": False, "error": "run_id required"}
    try:
        rsession = ctx.orchestrator.session(run_id)
    except KeyError:
        return {"ok": False, "error": f"run {run_id} not found"}
    ctx.session.linked_run_id = run_id
    asyncio.create_task(ctx.orchestrator.run(run_id), name=f"commander_run:{run_id}")
    return {
        "ok": True,
        "run_id": run_id,
        "entrypoint": rsession.request.entrypoint,
        "auto_approve": rsession.request.auto_approve,
        "states": {k: s.value for k, s in rsession.graph.all_states().items()},
    }


async def _get_run_status(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    if not run_id:
        return {"ok": False, "error": "no run_id and no linked run"}
    try:
        rsession = ctx.orchestrator.session(run_id)
    except KeyError:
        return {"ok": False, "error": f"run {run_id} not found"}
    states = {k: s.value for k, s in rsession.graph.all_states().items()}
    waiting = [k for k, v in states.items() if v == "waiting_review"]
    return {"ok": True, "run_id": run_id, "states": states, "waiting_review": waiting}


async def _feedback_loop(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    diagnosis_version = str(args.get("diagnosis_version") or args.get("version") or "v1")
    if not run_id:
        return {"ok": False, "error": "run_id required"}
    try:
        return await ctx.orchestrator.start_feedback_loop(
            run_id=run_id,
            diagnosis_version=diagnosis_version,
        )
    except KeyError:
        return {"ok": False, "error": f"run {run_id} not found"}


async def _get_diagnosis(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    run = ctx.run_store.get(run_id) if run_id else None
    if run is None:
        return {"ok": False, "error": f"run {run_id} not found"}
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    versions = sorted(run.subdir("diagnosis").glob("diagnosis.v*.md"))
    if not versions:
        return {"ok": True, "run_id": run_id, "exists": False}
    meta = parse_fm(versions[-1].read_text(encoding="utf-8")).metadata
    return {
        "ok": True,
        "run_id": run_id,
        "exists": True,
        "passed": meta.get("passed"),
        "recommended_target": meta.get("recommended_target"),
        "attempt": meta.get("attempt"),
        "failed_metrics": meta.get("failed_metrics"),
        "suspected_causes": meta.get("suspected_causes"),
        "budget_status": meta.get("budget_status"),
    }


async def _read_artifact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    agent = str(args.get("agent") or args.get("agent_dir") or "")
    version = str(args.get("version") or "approved")
    if not run_id:
        return {"ok": False, "error": "run_id required"}
    run = ctx.run_store.get(run_id)
    if run is None:
        return {"ok": False, "error": f"run {run_id} not found"}
    if not agent:
        return {"ok": False, "error": "agent required"}
    stem = str(args.get("stem") or "")
    if not stem:
        for _schema, (dir_name, candidate) in SCHEMA_TO_AGENT.items():
            if dir_name == agent:
                stem = candidate
                break
    if not stem:
        return {"ok": False, "error": f"unknown artifact stem for agent {agent}"}
    path = run.subdir(agent) / f"{stem}.{version}.md"
    if not path.exists():
        return {"ok": False, "error": f"artifact not found: {agent}/{stem}.{version}.md"}
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    text = path.read_text(encoding="utf-8")
    parsed = parse_fm(text)
    return {
        "ok": True,
        "run_id": run_id,
        "agent": agent,
        "stem": stem,
        "version": version,
        "path": str(path),
        "metadata": parsed.metadata,
        "body": parsed.body,
    }


async def _review_artifact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    action = str(args.get("action") or "approve")
    if action == "approve":
        return await _approve_node(args, ctx)
    if action == "reject":
        return await _reject_node(args, ctx)
    if action == "comment":
        run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
        agent = str(args.get("agent", ""))
        text = str(args.get("text") or args.get("comment") or "")
        if not (run_id and agent and text):
            return {"ok": False, "error": "run_id, agent, and text required"}
        from app.hitl.review_session import get_registry as get_review_registry

        review = get_review_registry().get(run_id, agent)
        if review is None:
            return {"ok": False, "error": f"no pending review for {agent} on {run_id}"}
        review.comment(text, actor="commander")
        return {"ok": True, "run_id": run_id, "agent": agent, "action": "commented"}
    return {"ok": False, "error": f"unknown review action '{action}'"}


async def _evaluate_metrics(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    run = ctx.run_store.get(run_id) if run_id else None
    if run is None:
        return {"ok": False, "error": f"run {run_id} not found"}
    path = run.subdir("execution") / "metrics.json"
    if not path.exists():
        return {"ok": True, "run_id": run_id, "exists": False, "passed": False}
    import json

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"metrics.json invalid: {exc}"}
    targets = dict(ctx.session.metric_targets)
    targets_raw = args.get("targets", {})
    if isinstance(targets_raw, dict):
        for key, value in targets_raw.items():
            try:
                targets[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    observed: dict[str, float] = {}
    if isinstance(rows, list):
        for key in targets:
            values: list[float] = []
            for row in rows:
                metrics = row.get("metrics", {}) if isinstance(row, dict) else {}
                if isinstance(metrics, dict) and key in metrics:
                    try:
                        values.append(float(metrics[key]))
                    except (TypeError, ValueError):
                        continue
            if values:
                observed[key] = sum(values) / len(values)
    comparisons = {
        key: {
            "target": target,
            "observed": observed.get(key),
            "passed": observed.get(key, float("-inf")) >= target,
        }
        for key, target in targets.items()
    }
    return {
        "ok": True,
        "run_id": run_id,
        "exists": True,
        "passed": all(item["passed"] for item in comparisons.values()) if comparisons else True,
        "comparisons": comparisons,
    }


async def _approve_node(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    agent = str(args.get("agent", ""))
    if not (run_id and agent):
        return {"ok": False, "error": "run_id and agent required"}
    from app.hitl.review_session import get_registry as get_review_registry

    review = get_review_registry().get(run_id, agent)
    if review is None:
        return {"ok": False, "error": f"no pending review for {agent} on {run_id}"}
    from app.hitl.approval import approve as approve_review

    await approve_review(session=review, bus=ctx.orchestrator.bus, actor="commander")
    return {"ok": True, "run_id": run_id, "agent": agent, "action": "approved"}


async def _reject_node(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    agent = str(args.get("agent", ""))
    reason = str(args.get("reason", "rejected via Commander"))
    if not (run_id and agent):
        return {"ok": False, "error": "run_id and agent required"}
    from app.hitl.review_session import get_registry as get_review_registry

    review = get_review_registry().get(run_id, agent)
    if review is None:
        return {"ok": False, "error": f"no pending review for {agent} on {run_id}"}
    review.reject(reason=reason)
    return {"ok": True, "run_id": run_id, "agent": agent, "action": "rejected"}


async def _set_metric_targets(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    targets_raw = args.get("targets", {})
    targets: dict[str, float] = {}
    if isinstance(targets_raw, dict):
        for k, v in targets_raw.items():
            try:
                targets[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    ctx.session.metric_targets.update(targets)
    return {"ok": True, "metric_targets": dict(ctx.session.metric_targets)}


async def _query_kb(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    zone = str(args.get("zone", "literature"))
    q = str(args.get("q", ""))
    top_k = int(args.get("top_k", 3) or 3)
    if not q:
        return {"ok": False, "error": "q required"}
    from app.harness.kb.retriever import query as kb_query

    try:
        hits = kb_query(query=q, zones=[zone], top_k=top_k)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "zone": zone,
        "hits": [
            {"score": round(h.score, 4), "excerpt": h.record.text[:240],
             "meta": h.record.metadata}
            for h in hits
        ],
    }


async def _list_runs(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    runs = ctx.run_store.list()
    return {
        "ok": True,
        "runs": [
            {"run_id": r.run_id, "task": r.task, "project": r.project,
             "entrypoint": r.entrypoint}
            for r in runs[-15:]
        ],
    }


TOOLS: dict[str, ToolSpec] = {
    "run.create": ToolSpec(
        name="run.create",
        description="Create a run without starting it; supports pipeline or single-stage entrypoints.",
        parameters={
            "entrypoint": "one of pipeline|idea|experiment|coding|execution|writing",
            "user_request": "natural-language task",
            "task": "optional short slug",
            "seed_artifact": "optional complete schema-valid markdown doc",
        },
        handler=_create_run,
    ),
    "run.start": ToolSpec(
        name="run.start",
        description="Start an existing run (defaults to the linked run).",
        parameters={"run_id": "optional; defaults to linked run"},
        handler=_start_run,
    ),
    "run.status": ToolSpec(
        name="run.status",
        description="Get the per-node states of a run.",
        parameters={"run_id": "optional; defaults to linked run"},
        handler=_get_run_status,
    ),
    "run.feedback_loop": ToolSpec(
        name="run.feedback_loop",
        description="Start the approved self-healing feedback loop from a diagnosis artifact.",
        parameters={"run_id": "optional", "diagnosis_version": "v1/v2/..."},
        handler=_feedback_loop,
    ),
    "artifact.read": ToolSpec(
        name="artifact.read",
        description="Read an artifact body and frontmatter.",
        parameters={"run_id": "optional", "agent": "stage name", "version": "default approved"},
        handler=_read_artifact,
    ),
    "artifact.review": ToolSpec(
        name="artifact.review",
        description="Approve, reject, or comment on a pending HITL artifact.",
        parameters={"run_id": "optional", "agent": "stage", "action": "approve|reject|comment"},
        handler=_review_artifact,
    ),
    "metrics.evaluate": ToolSpec(
        name="metrics.evaluate",
        description="Compare execution metrics.json against Commander metric targets.",
        parameters={"run_id": "optional", "targets": "optional metric -> numeric target"},
        handler=_evaluate_metrics,
    ),
    "diagnosis.failure_analysis": ToolSpec(
        name="diagnosis.failure_analysis",
        description="Read the latest self-healing diagnosis for a run.",
        parameters={"run_id": "optional; defaults to linked run"},
        handler=_get_diagnosis,
    ),
    "user.approval": ToolSpec(
        name="user.approval",
        description="Record a user's approve/reject decision for a waiting node.",
        parameters={"run_id": "optional", "agent": "stage", "action": "approve|reject"},
        handler=_review_artifact,
    ),
    "create_and_start_run": ToolSpec(
        name="create_and_start_run",
        description=(
            "Create and immediately start a pipeline/standalone run. Use the "
            "`entrypoint` to SKIP agents the user doesn't need: 'pipeline' runs "
            "the full Idea→Experiment→Coding→Execution→Writing chain; or pick a "
            "single stage ('idea'|'experiment'|'coding'|'execution'|'writing') to "
            "enter mid-chain (upstream stages are skipped). The entered stage's "
            "Agent drafts its artifact from `user_request` automatically. DO NOT "
            "pass seed_artifact unless the user gave a COMPLETE structured doc "
            "with YAML frontmatter — a natural-language idea/goal is NOT a "
            "seed_artifact; in that case just set entrypoint + user_request."
        ),
        parameters={
            "entrypoint": "one of pipeline|idea|experiment|coding|execution|writing",
            "user_request": "the research question / task description (natural language)",
            "task": "optional short slug for the run",
            "seed_artifact": "RARELY used — only a complete schema-valid markdown doc",
        },
        handler=_create_and_start_run,
    ),
    "get_run_status": ToolSpec(
        name="get_run_status",
        description="Get the per-node states of a run (defaults to the linked run).",
        parameters={"run_id": "optional; defaults to the conversation's linked run"},
        handler=_get_run_status,
    ),
    "get_diagnosis": ToolSpec(
        name="get_diagnosis",
        description=(
            "Read the latest self-healing diagnosis for a run: whether metrics "
            "passed, which stage is suspected (the pull-back target), failed "
            "metrics, and budget status."
        ),
        parameters={"run_id": "optional; defaults to the linked run"},
        handler=_get_diagnosis,
    ),
    "approve_node": ToolSpec(
        name="approve_node",
        description="Approve a node that is waiting for HITL review, so the pipeline advances.",
        parameters={"run_id": "optional; defaults to linked run", "agent": "stage name"},
        handler=_approve_node,
    ),
    "reject_node": ToolSpec(
        name="reject_node",
        description="Reject a waiting node (marks it failed, halts the chain).",
        parameters={"run_id": "optional", "agent": "stage name", "reason": "why"},
        handler=_reject_node,
    ),
    "set_metric_targets": ToolSpec(
        name="set_metric_targets",
        description=(
            "Record the user's success criteria (the 'expectation' to drive the "
            "self-healing loop toward), e.g. {'RES': -42}. Stored on the session."
        ),
        parameters={"targets": "object mapping metric name -> numeric target"},
        handler=_set_metric_targets,
    ),
    "query_kb": ToolSpec(
        name="query_kb",
        description="Semantic search a KB zone (literature|methodology|code_assets|run_archive).",
        parameters={"zone": "zone name", "q": "query text", "top_k": "int, default 3"},
        handler=_query_kb,
    ),
    "list_runs": ToolSpec(
        name="list_runs",
        description="List recent runs (run_id, task, project, entrypoint).",
        parameters={},
        handler=_list_runs,
    ),
}


async def execute_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        return {"ok": False, "error": f"unknown tool '{name}'"}
    started = time.perf_counter()
    _record_commander_tool_event(
        ctx=ctx,
        run_id=str(args.get("run_id") or ctx.session.linked_run_id or ""),
        kind="commander.tool_started",
        tool=name,
        args=args,
        result={},
    )
    try:
        if _is_registry_bridge_tool(name):
            result = await _execute_via_registry(name, args, ctx)
        else:
            result = await spec.handler(args, ctx)
    except Exception as exc:
        logger.exception("commander tool {} failed", name)
        result = {"ok": False, "error": str(exc)}
    duration_ms = int((time.perf_counter() - started) * 1000)
    result_run_id = str(result.get("run_id") or args.get("run_id") or ctx.session.linked_run_id or "")
    _record_commander_tool_event(
        ctx=ctx,
        run_id=result_run_id,
        kind="commander.tool_completed" if result.get("ok", False) else "commander.tool_failed",
        tool=name,
        args=args,
        result={**result, "duration_ms": duration_ms},
    )
    return result


def tools_for_prompt() -> str:
    """Render the tool catalogue for the Commander's system prompt."""
    lines: list[str] = []
    for spec in TOOLS.values():
        params = ", ".join(f"{k}: {v}" for k, v in spec.parameters.items()) or "(none)"
        lines.append(f"- {spec.name}({params})\n    {spec.description}")
    return "\n".join(lines)


def install_registry_adapters() -> None:
    global _REGISTRY_ADAPTERS_INSTALLED
    reg = get_tool_registry()
    bridge_tool_names = [name for name in TOOLS if _is_registry_bridge_tool(name)]
    if _REGISTRY_ADAPTERS_INSTALLED and all(reg.has(name) for name in bridge_tool_names):
        return
    for name in bridge_tool_names:
        spec = TOOLS[name]
        if reg.has(name):
            continue
        reg.register(
            name,
            _make_registry_adapter(name),
            spec=HarnessToolSpec(
                name=name,
                namespace=name.split(".", 1)[0],
                description=spec.description,
                bridge_only=True,
            ),
        )
    _REGISTRY_ADAPTERS_INSTALLED = True


def _is_registry_bridge_tool(name: str) -> bool:
    return tool_config(name).bridge_only and name in TOOLS


async def _execute_via_registry(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
) -> dict[str, Any]:
    install_registry_adapters()
    context_key = str(id(ctx))
    _COMMANDER_CONTEXTS[context_key] = ctx
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    run_root = ""
    if run_id:
        run = ctx.run_store.get(run_id)
        if run is not None:
            run_root = str(run.root)
    try:
        result = await get_tool_registry().dispatch(
            name,
            args,
            HarnessToolContext(
                run_id=run_id,
                project=ctx.session.project,
                agent="commander",
                extra={
                    "commander_context_key": context_key,
                    "run_root": run_root,
                },
                session_id=ctx.session.conv_id,
            ),
        )
    finally:
        _COMMANDER_CONTEXTS.pop(context_key, None)
    return _harness_result_to_commander_payload(result)


def _make_registry_adapter(name: str) -> Callable[[dict[str, Any], HarnessToolContext], Awaitable[HarnessToolResult]]:
    async def _adapter(args: dict[str, Any], hctx: HarnessToolContext) -> HarnessToolResult:
        context_key = str(hctx.extra.get("commander_context_key", "") if hctx.extra else "")
        bridge_ctx = _COMMANDER_CONTEXTS.get(context_key)
        if bridge_ctx is None:
            return HarnessToolResult(ok=False, error="commander context is not available")
        payload = await TOOLS[name].handler(args, bridge_ctx)
        run_id = str(payload.get("run_id") or hctx.run_id or "")
        if run_id:
            hctx.run_id = run_id
            run = bridge_ctx.run_store.get(run_id)
            if run is not None:
                hctx.extra["run_root"] = str(run.root)
        ok = bool(payload.get("ok", False))
        return HarnessToolResult(
            ok=ok,
            output=payload,
            error=None if ok else str(payload.get("error") or "commander tool failed"),
            evidence_refs=[run_id] if run_id else [],
        )

    return _adapter


def _harness_result_to_commander_payload(result: HarnessToolResult) -> dict[str, Any]:
    if isinstance(result.output, dict):
        payload = dict(result.output)
    else:
        payload = {"ok": result.ok, "output": result.output}
    payload.setdefault("ok", result.ok)
    if result.error:
        payload.setdefault("error", result.error)
    payload["tool_status"] = result.status
    payload["tool_call_id"] = result.metadata.get("tool_call_id")
    payload["requires_approval"] = result.requires_approval
    payload["blocked_by_gate"] = result.blocked_by_gate
    return payload


def _record_commander_tool_event(
    *,
    ctx: ToolContext,
    run_id: str,
    kind: str,
    tool: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if not run_id:
        return
    run = ctx.run_store.get(run_id)
    if run is None:
        return
    ok = bool(result.get("ok", True))
    write_event(
        run=run,
        stream="commander_tool_events",
        channel=f"run.{run.run_id}.tool",
        kind=kind,
        severity="info" if ok else "error",
        source={
            "component": "bridge.commander",
            "agent": "commander",
            "conversation_id": ctx.session.conv_id,
        },
        evidence=[run.run_id],
        payload={
            "tool": tool,
            "args": args,
            "result": result,
        },
    )
