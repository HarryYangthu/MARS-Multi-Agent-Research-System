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
    # Optional: dedupe double-submits — same key returns the same run.
    idempotency_key: str | None = None


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
    session = orch.create_session(request, idempotency_key=payload.idempotency_key)

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


@router.post("/{run_id}/start", status_code=202)
async def start_run(run_id: str) -> dict[str, str]:
    orch = get_orchestrator()
    _require_session(run_id)
    asyncio.create_task(orch.run(run_id), name=f"run:{run_id}")
    return {"status": "started", "run_id": run_id}


@router.post("/{run_id}/pause", status_code=202)
async def pause_run(run_id: str) -> dict[str, str]:
    _require_session(run_id)
    get_orchestrator().request_pause(run_id)
    return {"status": "pause_requested", "run_id": run_id}


@router.post("/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str) -> dict[str, str]:
    _require_session(run_id)
    get_orchestrator().request_cancel(run_id)
    return {"status": "cancel_requested", "run_id": run_id}


@router.post("/{run_id}/retry", status_code=202)
async def retry_run(run_id: str) -> dict[str, str]:
    orch = get_orchestrator()
    _require_session(run_id)
    orch.prepare_retry(run_id)
    asyncio.create_task(orch.run(run_id), name=f"retry:{run_id}")
    return {"status": "retrying", "run_id": run_id}


@router.post("/{run_id}/stop", status_code=202)
async def stop_run(run_id: str) -> dict[str, str]:
    # Back-compat alias for cancel.
    _require_session(run_id)
    get_orchestrator().request_cancel(run_id)
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
