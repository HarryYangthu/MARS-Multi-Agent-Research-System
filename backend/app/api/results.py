"""Generic, evidence-qualified results and bounded offline exports."""
from __future__ import annotations

from typing import Any
import zipfile

from fastapi import APIRouter, HTTPException, Response

from app.api.dependencies import get_run_store
from app.bridge.results_export import create_results_export, read_results_export
from app.bridge.results_service import collect_run_results
from app.storage.run_store import RunHandle

router = APIRouter(prefix="/api/results", tags=["results"])


def _run(run_id: str) -> RunHandle:
    try:
        run = get_run_store().get(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid run identifier") from exc
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{run_id}")
def get_results(run_id: str) -> dict[str, Any]:
    try:
        return collect_run_results(_run(run_id))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail={"code": "results_unavailable", "message": "Results could not be read safely"}) from exc


@router.post("/{run_id}/exports", status_code=201)
def export_results(run_id: str) -> dict[str, Any]:
    try:
        return create_results_export(_run(run_id))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail={"code": "export_unavailable", "message": "Results could not be exported safely"}) from exc


@router.get("/{run_id}/exports/{export_id}/download")
def download_results(run_id: str, export_id: str) -> Response:
    try:
        payload = read_results_export(_run(run_id), export_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Result export not found") from exc
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail={"code": "export_invalid", "message": "Result export verification failed"}) from exc
    return Response(payload, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="mars-results-{export_id}.zip"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})
