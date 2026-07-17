"""REST endpoints for the Run lifecycle."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_orchestrator, get_run_store
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


class TrashRunSummary(RunSummary):
    deleted_at: str
    expires_at: str
    days_remaining: int


class RunDetail(RunSummary):
    states: dict[str, str]
    graph: dict[str, Any]


class RetryAgentPayload(BaseModel):
    reason: str = ""


def _ensure_active_run(run_id: str) -> None:
    try:
        run = get_run_store().get(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")


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

    return RunDetail(
        run_id=session.run.run_id,
        project=session.run.project,
        task=session.run.task,
        entrypoint=session.run.entrypoint,
        created_at=session.run.created_at,
        states={k: s.value for k, s in session.graph.all_states().items()},
        graph=session.graph.to_dict(),
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


@router.get("/trash", response_model=list[TrashRunSummary])
async def list_trashed_runs(project: str = "") -> list[TrashRunSummary]:
    store = get_run_store()
    project_filter = project.strip()
    return [
        TrashRunSummary(
            run_id=r.run_id,
            project=r.project,
            task=r.task,
            entrypoint=r.entrypoint,
            created_at=r.created_at,
            deleted_at=r.deleted_at,
            expires_at=r.expires_at,
            days_remaining=r.days_remaining,
        )
        for r in store.list_trashed()
        if not project_filter or r.project == project_filter
    ]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str) -> RunDetail:
    _ensure_active_run(run_id)
    orch = get_orchestrator()
    try:
        session = orch.session(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return RunDetail(
        run_id=session.run.run_id,
        project=session.run.project,
        task=session.run.task,
        entrypoint=session.run.entrypoint,
        created_at=session.run.created_at,
        states={k: s.value for k, s in session.graph.all_states().items()},
        graph=session.graph.to_dict(),
    )


@router.delete("/{run_id}", response_model=TrashRunSummary)
async def delete_run(run_id: str) -> TrashRunSummary:
    store = get_run_store()
    try:
        trashed = store.trash(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    get_orchestrator().discard_session(run_id)
    return TrashRunSummary(
        run_id=trashed.run_id,
        project=trashed.project,
        task=trashed.task,
        entrypoint=trashed.entrypoint,
        created_at=trashed.created_at,
        deleted_at=trashed.deleted_at,
        expires_at=trashed.expires_at,
        days_remaining=trashed.days_remaining,
    )


@router.post("/{run_id}/restore", response_model=RunSummary)
async def restore_run(run_id: str) -> RunSummary:
    store = get_run_store()
    try:
        restored = store.restore(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found in trash") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RunSummary(
        run_id=restored.run_id,
        project=restored.project,
        task=restored.task,
        entrypoint=restored.entrypoint,
        created_at=restored.created_at,
    )


@router.delete("/trash/{run_id}", status_code=204)
async def permanently_delete_run(run_id: str) -> None:
    store = get_run_store()
    try:
        store.delete_trashed(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found in trash") from exc


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
    _ensure_active_run(run_id)
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
    _ensure_active_run(run_id)
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
    _ensure_active_run(run_id)
    # V0 has no cancellation hook; placeholder for V2.
    return {"status": "stop_requested", "run_id": run_id}
