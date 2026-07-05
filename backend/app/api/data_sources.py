"""Dataset selection and preview endpoints for simulation runs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.storage.data_source_store import DataSourceStore, sha256_file

router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])


class DataSourceProfile(BaseModel):
    id: str
    project: str
    kind: str
    original_name: str
    stored_path: str
    size_bytes: int
    checksum: str
    fs_mhz: float | None = None
    sample_rate_hz: float | None = None
    channel_count: int | None = None
    description: str = ""
    format: str
    shape: list[int] | None = None
    dtype: str | None = None
    preview_key: str = ""
    sample_points: int = 0
    dict_entries: list[dict[str, Any]] = Field(default_factory=list)
    spectrum_available: bool = False
    spectrum_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    is_default: bool = False


class DataSourceMetadataPatch(BaseModel):
    fs_mhz: float | None = None
    kind: str | None = None
    channel_count: int | None = None
    description: str | None = None


class DataSourceDefaultPayload(BaseModel):
    id: str = Field(..., min_length=1)
    project: str = Field(default="pimc", min_length=1)


@router.get("", response_model=list[DataSourceProfile])
async def list_data_sources(project: str = "") -> list[DataSourceProfile]:
    store = DataSourceStore()
    return [DataSourceProfile(**profile) for profile in store.list(project=project)]


@router.get("/default", response_model=DataSourceProfile)
async def get_default_data_source(project: str = "pimc") -> DataSourceProfile:
    store = DataSourceStore()
    profile = store.default_profile(project)
    if profile is None:
        raise HTTPException(status_code=404, detail="default data source not configured")
    return DataSourceProfile(**profile)


@router.put("/default", response_model=DataSourceProfile)
async def set_default_data_source(
    payload: DataSourceDefaultPayload,
) -> DataSourceProfile:
    store = DataSourceStore()
    try:
        profile = store.set_default(project=payload.project, source_id=payload.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="data source not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DataSourceProfile(**profile)


@router.post("/upload", response_model=DataSourceProfile)
async def upload_data_source(
    request: Request,
    filename: str = Query(..., min_length=1),
    project: str = Query("pimc", min_length=1),
    fs_mhz: float | None = Query(None),
    kind: str = Query("auto"),
    channel_count: int | None = Query(None),
    description: str = "",
) -> DataSourceProfile:
    store = DataSourceStore()
    source_id, target = store.allocate(original_name=filename, project=project)
    try:
        await _write_request_body(request, target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"failed to save upload: {exc}") from exc
    if not target.exists() or target.stat().st_size == 0:
        raise HTTPException(status_code=422, detail="uploaded dataset is empty")

    profile = store.profile_uploaded_file(
        source_id=source_id,
        path=target,
        project=project,
        original_name=filename,
        fs_mhz=fs_mhz,
        kind=kind,
        channel_count=channel_count,
        description=description,
        checksum=sha256_file(target),
    )
    return DataSourceProfile(**profile)


@router.get("/{source_id}", response_model=DataSourceProfile)
async def get_data_source(source_id: str) -> DataSourceProfile:
    store = DataSourceStore()
    try:
        profile = store.load(source_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="data source not found") from exc
    return DataSourceProfile(**profile)


@router.patch("/{source_id}", response_model=DataSourceProfile)
async def update_data_source(
    source_id: str,
    payload: DataSourceMetadataPatch,
) -> DataSourceProfile:
    store = DataSourceStore()
    try:
        profile = store.update_metadata(
            source_id=source_id,
            fs_mhz=payload.fs_mhz,
            kind=payload.kind,
            channel_count=payload.channel_count,
            description=payload.description,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="data source not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DataSourceProfile(**profile)


@router.get("/{source_id}/spectrum")
async def get_data_source_spectrum(source_id: str) -> FileResponse:
    store = DataSourceStore()
    path = store.spectrum_path(source_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="spectrum preview not found")
    return FileResponse(path, media_type="image/png")


async def _write_request_body(request: Request, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        async for chunk in request.stream():
            if chunk:
                fh.write(chunk)
