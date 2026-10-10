"""Trace manifest APIs."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.dependencies import get_run_store
from app.bridge.agent_inspection import inspection_catalog, inspection_event
from app.harness.observability.tracing import TraceRecorder

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("/{run_id}/agents/{agent}")
def agent_inspection(run_id: str, agent: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return {"run_id": run_id, "project": run.project, **inspection_catalog(run.root, agent)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{run_id}/agents/{agent}/events/{event_id}")
def agent_inspection_event(run_id: str, agent: str, event_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return inspection_event(run.root, agent, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="event not recorded") from exc


@router.get("/{run_id}")
async def get_trace(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("context") / "trace_manifest.v2.json"
    if not path.exists():
        return TraceRecorder(run).ensure_manifest()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="trace manifest is invalid")
    return raw


@router.get("/{run_id}/spans/{span_id}")
async def get_span(run_id: str, span_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    span = TraceRecorder(run).get_span(span_id)
    if span is None:
        raise HTTPException(status_code=404, detail="span not found")
    return span
