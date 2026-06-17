"""Trace manifest APIs."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.dependencies import get_run_store
from app.harness.observability.tracing import TraceRecorder

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("/{run_id}")
async def get_trace(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("context") / "trace_manifest.v1.json"
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
