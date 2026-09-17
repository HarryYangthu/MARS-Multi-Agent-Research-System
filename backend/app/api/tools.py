"""Tool catalogue and audit APIs."""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, HTTPException

from app.api.dependencies import get_run_store
from app.api.idea_materials import enrich_source_errors
from app.harness.tools.mcp_adapters import (
    AdapterKind,
    MCPTransportError,
    adapter_for_tool,
    adapter_status,
    all_adapter_statuses,
    list_mcp_tools,
)
from app.harness.tools.registry import ToolContext
from app.harness.tools.registry import get_registry as get_tool_registry
from app.harness.tools.config import load_tool_configs
from app.harness.llm.model_registry import get_agent_config
from app.harness.persistence import atomic_write_json, path_lock

router = APIRouter(prefix="/api/tools", tags=["tools"])
run_router = APIRouter(prefix="/api/runs", tags=["tools"])


@router.get("")
async def list_tools() -> list[dict[str, Any]]:
    return [_spec_to_dict(spec) for spec in get_tool_registry().specs(include_bridge_only=True)]


@router.get("/adapters")
async def list_tool_adapters() -> list[dict[str, Any]]:
    return [asdict(status) for status in all_adapter_statuses()]


@router.get("/adapters/{kind}")
async def get_tool_adapter(kind: str) -> dict[str, Any]:
    if kind not in {"chroma", "filesystem", "git", "github"}:
        raise HTTPException(status_code=404, detail=f"adapter '{kind}' not found")
    return asdict(adapter_status(cast(AdapterKind, kind)))


@router.get("/adapters/{kind}/tools")
async def list_adapter_mcp_tools(kind: str) -> dict[str, Any]:
    if kind not in {"chroma", "filesystem", "git", "github"}:
        raise HTTPException(status_code=404, detail=f"adapter '{kind}' not found")
    try:
        result = await list_mcp_tools(cast(AdapterKind, kind))
    except MCPTransportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "kind": kind, "result": result}


@router.post("/adapters/{kind}/call")
async def call_adapter_mcp_tool(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind not in {"chroma", "filesystem", "git", "github"}:
        raise HTTPException(status_code=404, detail=f"adapter '{kind}' not found")
    run_id = str(payload.get("run_id") or "")
    agent = str(payload.get("agent") or "")
    if not run_id or not agent:
        raise HTTPException(status_code=422, detail="run_id and configured agent are required")
    if agent in {"bridge", "system"}:
        raise HTTPException(status_code=403, detail="API callers cannot select privileged internal agents")
    try:
        agent_config = get_agent_config(agent)
        run = get_run_store().get(run_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid run or agent") from exc
    if not agent_config.enabled:
        raise HTTPException(status_code=403, detail="agent is disabled")
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if payload.get("project") is not None and payload.get("project") != run.project:
        raise HTTPException(status_code=409, detail="project must match the stored run")
    tool_name = str(payload.get("tool_name") or payload.get("name") or "")
    cfg = load_tool_configs().get(tool_name)
    if cfg is None or cfg.mcp_kind != kind or not cfg.mcp_tool:
        raise HTTPException(status_code=403, detail="MCP tool has no host-approved registry binding")
    arguments_raw = payload.get("arguments", {})
    if not isinstance(arguments_raw, dict) or "_approval_id" in arguments_raw:
        raise HTTPException(status_code=422, detail="arguments must be an object without internal approval fields")
    if not tool_name:
        raise HTTPException(status_code=422, detail="tool_name is required")
    result = await get_tool_registry().dispatch(
        tool_name, arguments_raw,
        ToolContext(run_id=run.run_id, project=run.project, agent=agent,
                    extra={"run_root": str(run.root)}),
    )
    response = {"ok": result.ok, "kind": kind, "tool_name": tool_name, "result": _result_to_dict(result)}
    if not result.ok:
        code = 403 if result.status in {"not_allowed", "disabled", "unknown_tool"} else 409
        raise HTTPException(status_code=code, detail=response)
    return response


@router.get("/{name}")
async def get_tool(name: str) -> dict[str, Any]:
    spec = get_tool_registry().spec(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"tool '{name}' not found")
    return _spec_to_dict(spec)


@run_router.get("/{run_id}/tools")
async def list_run_tool_calls(
    run_id: str,
    tool: str = "",
    status: str = "",
    call_id: str = "",
    event: str = "",
    limit: int = 0,
) -> list[dict[str, Any]]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    entries: list[dict[str, Any]] = []
    for path in (
        run.subdir("events") / "tool_events.jsonl",
        run.subdir("events") / "tool_calls.jsonl",
    ):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
    entries = [
        entry
        for entry in entries
        if _matches_filter(entry, "tool", tool)
        and _matches_filter(entry, "status", status)
        and _matches_filter(entry, "call_id", call_id)
        and _matches_filter(entry, "event", event)
    ]
    entries.sort(key=lambda item: str(item.get("timestamp", "")))
    if limit > 0:
        entries = entries[-limit:]
    return enrich_source_errors(run.root, entries)


@run_router.get("/{run_id}/tools/approvals")
async def list_tool_approvals(run_id: str) -> list[dict[str, Any]]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    approvals_dir = run.subdir("events") / "tool_approvals"
    if not approvals_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(approvals_dir.glob("*.json")):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    out.sort(key=lambda item: str(item.get("created_at", "")))
    return out


@run_router.post("/{run_id}/tools/{call_id}/approve")
async def approve_tool_call(
    run_id: str,
    call_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    record_path = run.subdir("events") / "tool_approvals" / f"{call_id}.json"
    with path_lock(record_path.with_suffix(".lock")):
        record = _read_approval_record(record_path)
        if record.get("status") != "pending":
            raise HTTPException(status_code=409, detail=f"approval is {record.get('status')}")
        actor = str((payload or {}).get("actor") or "user")
        record["status"] = "approved"
        record["approved_at"] = _now()
        record["approved_by"] = actor
        _write_approval_record(record_path, record)

    raw_args = record.get("args", {})
    if not isinstance(raw_args, dict):
        raise HTTPException(status_code=409, detail="approval args are malformed")
    replay_args = dict(raw_args)
    replay_args["_approval_id"] = call_id
    context = record.get("context", {})
    context = context if isinstance(context, dict) else {}
    extra_raw = context.get("extra", {})
    extra = extra_raw if isinstance(extra_raw, dict) else {}
    extra = {key: value for key, value in extra.items() if isinstance(key, str)}
    extra["run_root"] = str(run.root)

    result = await get_tool_registry().dispatch(
        str(record.get("tool", "")),
        replay_args,
        ToolContext(
            run_id=run.run_id,
            project=str(record.get("project") or run.project),
            agent="bridge",
            extra=extra,
            trace_id=str(context.get("trace_id") or ""),
            span_id=str(context.get("span_id") or ""),
            user_id=str(context.get("user_id") or ""),
            session_id=str(context.get("session_id") or ""),
            workspace_root=str(context.get("workspace_root") or ""),
            project_repo_root=str(context.get("project_repo_root") or ""),
            dry_run=bool(context.get("dry_run", False)),
            approval_mode="approved",
        ),
    )
    record["replay_status"] = result.status
    record["replay_ok"] = result.ok
    record["replayed_at"] = _now()
    record["replay_error"] = result.error
    record["status"] = "applied" if result.ok else "approved_failed"
    _write_approval_record(record_path, record)
    if not result.ok:
        raise HTTPException(
            status_code=409,
            detail={
                "error": result.error,
                "status": result.status,
                "blocked_by_gate": result.blocked_by_gate,
            },
        )
    return {
        "ok": True,
        "approval_id": call_id,
        "status": record["status"],
        "result": _result_to_dict(result),
    }


@run_router.post("/{run_id}/tools/{call_id}/reject")
async def reject_tool_call(
    run_id: str,
    call_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    record_path = run.subdir("events") / "tool_approvals" / f"{call_id}.json"
    with path_lock(record_path.with_suffix(".lock")):
        record = _read_approval_record(record_path)
        if record.get("status") != "pending":
            raise HTTPException(status_code=409, detail=f"approval is {record.get('status')}")
        record["status"] = "rejected"
        record["rejected_at"] = _now()
        record["rejected_by"] = str((payload or {}).get("actor") or "user")
        record["rejection_reason"] = str((payload or {}).get("reason") or "")
        _write_approval_record(record_path, record)
    return {"ok": True, "approval_id": call_id, "status": "rejected"}


@run_router.post("/{run_id}/tools/{call_id}/rollback")
async def rollback_tool_call(run_id: str, call_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    record_path = run.subdir("coding") / "tool_applications" / f"{call_id}.json"
    if not record_path.exists():
        raise HTTPException(status_code=404, detail="tool call not found")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    rollback_ref = str(record.get("rollback_ref") or "")
    if not rollback_ref:
        raise HTTPException(status_code=409, detail="tool call has no rollback_ref")
    result = await get_tool_registry().dispatch(
        "code.rollback_patch",
        {"rollback_ref": rollback_ref},
        ToolContext(
            run_id=run.run_id,
            project=run.project,
            agent="bridge",
            extra={"run_root": str(run.root)},
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.error)
    return {"ok": True, "result": result.output}


def _spec_to_dict(spec: Any) -> dict[str, Any]:
    data = asdict(spec)
    adapter = adapter_for_tool(str(data.get("name", "")))
    data["mcp_adapter"] = asdict(adapter) if adapter is not None else None
    cfg = load_tool_configs().get(str(data.get("name", "")))
    if cfg is not None and cfg.mcp_kind:
        data["mcp_binding"] = {"kind": cfg.mcp_kind, "tool": cfg.mcp_tool,
                               "execution_backend": cfg.process_backend,
                               "require_isolation": cfg.require_isolation}
    return data


def _matches_filter(entry: dict[str, Any], key: str, expected: str) -> bool:
    if not expected:
        return True
    value = entry.get(key)
    if value is None and key == "tool":
        value = entry.get("tool_name")
    return str(value or "") == expected


def _read_approval_record(path: Any) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail="tool approval not found")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail=f"approval record invalid: {exc}") from exc
    if not isinstance(record, dict):
        raise HTTPException(status_code=409, detail="approval record is malformed")
    return record


def _write_approval_record(path: Any, record: dict[str, Any]) -> None:
    atomic_write_json(Path(path), record)


def _result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status": result.status,
        "output": result.output,
        "error": result.error,
        "blocked_by_gate": result.blocked_by_gate,
        "requires_approval": result.requires_approval,
        "artifacts": result.artifacts,
        "metrics": result.metrics,
        "rollback_ref": result.rollback_ref,
        "evidence_refs": result.evidence_refs,
        "metadata": result.metadata,
    }


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
