"""REST endpoints for the Run lifecycle."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_event_bus, get_orchestrator, get_run_store
from app.bridge.orchestrator import RunRequest
from app.bridge.run_observability import build_run_observability
from app.harness.runtime.readiness import ProductionReadinessError, assert_ready_for_run
from app.storage.data_source_store import DataSourceStore

router = APIRouter(prefix="/api/runs", tags=["runs"])


class CreateRunPayload(BaseModel):
    task: str = Field(..., min_length=1)
    project: str = Field(..., min_length=1)
    entrypoint: str = Field(default="pipeline")
    standalone: bool = False
    user_request: str = ""
    # When true, every agent auto-approves and the Commander self-heal loop
    # auto-appends repair attempts — the run executes end-to-end without HITL
    # clicks. Used for demos / autonomous runs; the UI default stays human-gated.
    auto_approve: bool = False
    # Optional: pre-written markdown for the entrypoint Agent's first artifact.
    # When provided, the API validates it against the matching schema, drops
    # it as <agent>/<stem>.v1.md, and the orchestrator skips the LLM draft for
    # that node — the run goes straight into HITL review.
    seed_artifact: str | None = None
    data_source: "DataSourceSelection | None" = None


class DataSourceSelection(BaseModel):
    id: str = Field(..., min_length=1)
    fs_mhz: float | None = None
    kind: str | None = None
    channel_count: int | None = None
    description: str | None = None


class RunSummary(BaseModel):
    run_id: str
    project: str
    task: str
    entrypoint: str
    created_at: str


class RunDetail(RunSummary):
    states: dict[str, str]
    graph: dict[str, Any]
    run_status: str = "created"


class RetryAgentPayload(BaseModel):
    reason: str = ""


@router.post("", response_model=RunDetail)
async def create_run(payload: CreateRunPayload) -> RunDetail:
    try:
        assert_ready_for_run(project=payload.project)
    except ProductionReadinessError as exc:
        raise HTTPException(
            status_code=503,
            detail=exc.report.to_dict(),
        ) from exc
    data_source = _resolve_data_source_selection(
        selection=payload.data_source,
        project=payload.project,
    )
    orch = get_orchestrator()
    request = RunRequest(
        task=payload.task,
        project=payload.project,
        entrypoint=payload.entrypoint,  # type: ignore[arg-type]
        standalone=payload.standalone,
        user_request=payload.user_request,
        auto_approve=payload.auto_approve,
        data_source=data_source,
    )
    try:
        session = orch.create_session(request)
    except ProductionReadinessError as exc:
        raise HTTPException(status_code=503, detail=exc.report.to_dict()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # If the caller supplied a seed artifact, validate + persist it under the
    # entrypoint Agent's directory as v1.md. The orchestrator's agent_runner
    # later detects the existing v1 and skips the LLM draft for that node.
    if payload.seed_artifact and payload.entrypoint != "pipeline":
        from app.api.templates import SCHEMA_TO_AGENT_AND_STEM
        from app.harness.schema.validator import validate_document
        from app.storage.artifact_store import ArtifactStore

        # Reverse-lookup: agent name -> (schema, stem)
        schema_for: dict[str, tuple[str, str]] = {
            agent: (sid, stem) for sid, (agent, stem) in SCHEMA_TO_AGENT_AND_STEM.items()
        }
        if payload.entrypoint in schema_for:
            schema_id, stem = schema_for[payload.entrypoint]
            result = validate_document(
                payload.seed_artifact, expected_schema=schema_id
            )
            if not result.valid:
                # Detailed schema error so the form can highlight problems.
                raise HTTPException(
                    status_code=422,
                    detail={
                        "schema": schema_id,
                        "errors": [
                            {"path": e.path, "message": e.message}
                            for e in result.errors
                        ],
                    },
                )
            store = ArtifactStore(session.run)
            store.write(text=payload.seed_artifact, expected_schema=schema_id)

    return _detail(session)


def _detail(session: Any) -> RunDetail:
    return RunDetail(
        run_id=session.run.run_id,
        project=session.run.project,
        task=session.run.task,
        entrypoint=session.run.entrypoint,
        created_at=session.run.created_at,
        states={k: s.value for k, s in session.graph.all_states().items()},
        graph=session.graph.to_dict(),
        run_status=session.run_status.value,
    )


def _resolve_data_source_selection(
    selection: DataSourceSelection | None,
    *,
    project: str,
) -> dict[str, Any] | None:
    store = DataSourceStore()
    if selection is None:
        return store.default_profile(project)
    try:
        profile = store.load(selection.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail="selected data source not found") from exc
    if selection.fs_mhz is not None:
        profile["fs_mhz"] = selection.fs_mhz
        profile["sample_rate_hz"] = selection.fs_mhz * 1_000_000.0
    if selection.kind is not None:
        profile["kind"] = selection.kind
    if selection.channel_count is not None:
        profile["channel_count"] = selection.channel_count
    if selection.description is not None:
        profile["description"] = selection.description
    return profile


@router.get("", response_model=list[RunSummary])
async def list_runs(project: str = "") -> list[RunSummary]:
    store = get_run_store()
    project_filter = project.strip()
    return [
        RunSummary(
            run_id=r.run_id,
            project=r.project,
            task=r.task,
            entrypoint=r.entrypoint,
            created_at=r.created_at,
        )
        for r in store.list()
        if not project_filter or r.project == project_filter
    ]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str) -> RunDetail:
    orch = get_orchestrator()
    # Disk fallback: serve state even for runs not in memory (e.g. after a
    # restart, or runs that finished before this process started).
    session = orch.get_or_load_session(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _detail(session)


def _require_session(run_id: str) -> Any:
    session = get_orchestrator().get_or_load_session(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")
    return session


@router.get("/{run_id}/observability")
async def get_run_observability(run_id: str, limit: int = 200) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_run_observability(run, limit=max(1, min(limit, 500)))


@router.get("/{run_id}/health")
async def get_run_health(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    view = build_run_observability(run, limit=20)
    return {
        "run_id": run_id,
        "status": view["status"],
        "health": view["health"],
        "latest_event_at": view["latest_event_at"],
    }


@router.post("/{run_id}/start", status_code=202)
async def start_run(run_id: str) -> dict[str, str]:
    orch = get_orchestrator()
    try:
        session = orch.session(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    try:
        assert_ready_for_run(project=session.run.project)
    except ProductionReadinessError as exc:
        raise HTTPException(status_code=503, detail=exc.report.to_dict()) from exc
    asyncio.create_task(orch.run(run_id), name=f"run:{run_id}")
    return {"status": "started", "run_id": run_id}


@router.post("/{run_id}/agents/{agent}/retry", status_code=202)
async def retry_agent(
    run_id: str,
    agent: str,
    payload: RetryAgentPayload,
) -> dict[str, str]:
    orch = get_orchestrator()
    reason = payload.reason.strip() or "人工请求重试失败 Agent。"
    try:
        result = await orch.request_artifact_revision(
            run_id=run_id,
            agent=agent,
            reason=reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return {
        "status": str(result.get("status") or "revision_started"),
        "run_id": run_id,
        "agent": agent,
        "node": str(result.get("node") or agent),
    }


@router.post("/{run_id}/stop", status_code=202)
async def stop_run(run_id: str) -> dict[str, str]:
    # V0 has no cancellation hook; placeholder for V2.
    return {"status": "stop_requested", "run_id": run_id}


class FeedbackPayload(BaseModel):
    text: str = Field(..., min_length=1)
    agent: str | None = None


@router.post("/{run_id}/feedback", status_code=201)
async def post_feedback(run_id: str, payload: FeedbackPayload) -> dict[str, str]:
    """Persist a human comment to runs/<id>/hitl/feedback.jsonl + broadcast it."""
    session = _require_session(run_id)
    entry = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "text": payload.text,
        "agent": payload.agent,
        "actor": "user",
    }
    fb_path = session.run.subdir("hitl") / "feedback.jsonl"
    with fb_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    await get_event_bus().publish(
        f"run.{run_id}.hitl", {"event": "feedback", **entry}
    )
    return {"status": "recorded", "run_id": run_id}


@router.delete("/{run_id}", status_code=200)
async def delete_run(run_id: str) -> dict[str, str]:
    if not get_orchestrator().delete_run(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return {"status": "deleted", "run_id": run_id}


def _run_root(run_id: str) -> Any:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run.root


@router.get("/{run_id}/context/{agent}")
async def get_context_manifest(run_id: str, agent: str) -> dict[str, Any]:
    """Latest ContextManifest for an agent — what was loaded + token estimate."""
    cdir = _run_root(run_id) / "context"
    files = sorted(cdir.glob(f"{agent}_context_pack.v*.json")) if cdir.exists() else []
    if not files:
        return {"exists": False}
    data: dict[str, Any] = json.loads(files[-1].read_text(encoding="utf-8"))
    return {"exists": True, **data}


@router.get("/{run_id}/thinking/{agent}")
async def get_thinking(run_id: str, agent: str) -> dict[str, Any]:
    """Replay the persisted LLM thinking (reasoning + content) for an agent."""
    p = _run_root(run_id) / agent / "thinking.md"
    return {
        "exists": p.exists(),
        "text": p.read_text(encoding="utf-8") if p.exists() else "",
    }


@router.get("/{run_id}/execution/planned")
async def get_planned_experiments(run_id: str) -> dict[str, Any]:
    """The experiment grid the Execution Agent proposes (pre-approval)."""
    p = _run_root(run_id) / "execution" / "planned_experiments.json"
    if not p.exists():
        return {"experiments": [], "count": 0}
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return data


@router.get("/{run_id}/execution/metrics")
async def get_execution_metrics(run_id: str) -> list[dict[str, Any]]:
    """Final per-experiment metrics (after the batch completes)."""
    p = _run_root(run_id) / "execution" / "metrics.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []
