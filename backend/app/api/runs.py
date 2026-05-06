"""REST endpoints for the Run lifecycle."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_orchestrator, get_run_store
from app.bridge.orchestrator import RunRequest

router = APIRouter(prefix="/api/runs", tags=["runs"])


class CreateRunPayload(BaseModel):
    task: str = Field(..., min_length=1)
    project: str = Field(..., min_length=1)
    entrypoint: str = Field(default="pipeline")
    standalone: bool = False
    user_request: str = ""
    # Optional: pre-written markdown for the entrypoint Agent's first artifact.
    # When provided, the API validates it against the matching schema, drops
    # it as <agent>/<stem>.v1.md, and the orchestrator skips the LLM draft for
    # that node — the run goes straight into HITL review.
    seed_artifact: str | None = None


class RunSummary(BaseModel):
    run_id: str
    project: str
    task: str
    entrypoint: str
    created_at: str


class RunDetail(RunSummary):
    states: dict[str, str]
    graph: dict[str, Any]


@router.post("", response_model=RunDetail)
async def create_run(payload: CreateRunPayload) -> RunDetail:
    orch = get_orchestrator()
    request = RunRequest(
        task=payload.task,
        project=payload.project,
        entrypoint=payload.entrypoint,  # type: ignore[arg-type]
        standalone=payload.standalone,
        user_request=payload.user_request,
    )
    session = orch.create_session(request)

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


@router.get("", response_model=list[RunSummary])
async def list_runs() -> list[RunSummary]:
    store = get_run_store()
    return [
        RunSummary(
            run_id=r.run_id,
            project=r.project,
            task=r.task,
            entrypoint=r.entrypoint,
            created_at=r.created_at,
        )
        for r in store.list()
    ]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str) -> RunDetail:
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


@router.post("/{run_id}/start", status_code=202)
async def start_run(run_id: str) -> dict[str, str]:
    orch = get_orchestrator()
    try:
        orch.session(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    asyncio.create_task(orch.run(run_id), name=f"run:{run_id}")
    return {"status": "started", "run_id": run_id}


@router.post("/{run_id}/stop", status_code=202)
async def stop_run(run_id: str) -> dict[str, str]:
    # V0 has no cancellation hook; placeholder for V1.
    return {"status": "stop_requested", "run_id": run_id}
