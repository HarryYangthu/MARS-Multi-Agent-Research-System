"""Report bundle APIs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.dependencies import get_run_store
from app.reporting import generate_report_bundle, read_latest_report_bundle

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{run_id}")
async def get_report_bundle(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    bundle = read_latest_report_bundle(run)
    return bundle or {"exists": False, "run_id": run_id}


@router.post("/{run_id}/regenerate")
async def regenerate_report_bundle(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return generate_report_bundle(run, actor="api")


@router.get("/{run_id}/files/{filename}")
async def download_report_file(run_id: str, filename: str) -> FileResponse:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    safe_name = Path(filename).name
    candidates = [
        run.subdir("writing") / "deliverables" / safe_name,
        run.subdir("writing") / safe_name,
    ]
    for path in candidates:
        if path.exists() and path.is_file() and _is_inside(path, run.root):
            return FileResponse(path, filename=safe_name)
    raise HTTPException(status_code=404, detail="report file not found")


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
