"""Execution monitor REST endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

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


@router.get("/{run_id}/plots")
async def list_plots(run_id: str) -> list[dict[str, Any]]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    d = run.subdir("execution") / "live_plots"
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.png")):
        if not _ready_png(p):
            continue
        stat = p.stat()
        out.append(
            {
                "filename": p.name,
                "experiment_id": p.stem.removesuffix("_loss"),
                "metric": "loss",
                "url": f"/api/execution/{run_id}/plots/{p.name}",
                "updated_at": stat.st_mtime,
                "size_bytes": stat.st_size,
            }
        )
    return out


@router.get("/{run_id}/plots/{name}")
async def get_plot(run_id: str, name: str) -> FileResponse:
    if "/" in name or "\\" in name or not name.endswith(".png"):
        raise HTTPException(status_code=400, detail="invalid plot name")
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    p = run.subdir("execution") / "live_plots" / name
    if not _ready_png(p):
        raise HTTPException(status_code=404, detail=f"plot {name} not found")
    return FileResponse(p, media_type="image/png")


def _ready_png(path: Path) -> bool:
    try:
        if path.stat().st_size < 8:
            return False
        with path.open("rb") as fh:
            return fh.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False
