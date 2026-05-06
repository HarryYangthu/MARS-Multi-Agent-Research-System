"""Execution monitor REST endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.dependencies import get_run_store

router = APIRouter(prefix="/api/execution", tags=["execution"])


@router.get("/{run_id}/metrics")
async def get_metrics(run_id: str) -> list[dict[str, Any]]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    p = run.subdir("execution") / "metrics.json"
    if not p.exists():
        return []
    parsed = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


@router.get("/{run_id}/curves")
async def list_curves(run_id: str) -> list[str]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    d = run.subdir("execution") / "curves"
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("*.json"))


@router.get("/{run_id}/curves/{name}")
async def get_curve(run_id: str, name: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    p = run.subdir("execution") / "curves" / name
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"curve {name} not found")
    parsed = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="curve file malformed")
    return parsed


@router.get("/{run_id}/summary")
async def get_summary(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    p = run.subdir("execution") / "batch_summary.json"
    if not p.exists():
        return {"experiments": [], "failures": [], "total": 0}
    parsed = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        return parsed
    return {}
