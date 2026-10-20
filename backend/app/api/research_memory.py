"""Project-owned research memory; local evidence import is explicit."""
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import yaml

from app.storage.research_memory import ResearchMemory

router = APIRouter(prefix="/api/projects/{project}/research-memory", tags=["research-memory"])
T = TypeVar("T")


class ImportPayload(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    label: str = Field(default="", max_length=200)
    protocol: dict[str, Any] | None = None


class SelectPayload(BaseModel):
    record_id: str
    role: Literal["original", "current_best"]


class QueryPayload(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    protocol: dict[str, Any] | None = None
    limit: int = Field(default=4, ge=1, le=10)


def guarded(action: Callable[[], T]) -> T:
    try:
        return action()
    except (ValueError, OSError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def overview(project: str) -> dict[str, Any]:
    return guarded(lambda: ResearchMemory(project).overview())


@router.post("/import")
def import_records(project: str, payload: ImportPayload) -> dict[str, Any]:
    return guarded(lambda: ResearchMemory(project).import_directory(payload.path, label=payload.label, protocol=payload.protocol))


@router.post("/capture-code")
def capture_code(project: str) -> dict[str, Any]:
    return guarded(lambda: ResearchMemory(project).capture_code()[0])


@router.post("/select")
def select(project: str, payload: SelectPayload) -> dict[str, Any]:
    return guarded(lambda: ResearchMemory(project).select(payload.record_id, payload.role))


@router.post("/query")
def retrieve(project: str, payload: QueryPayload) -> dict[str, Any]:
    return guarded(lambda: ResearchMemory(project).retrieve(payload.query, limit=payload.limit, protocol=payload.protocol))


@router.get("/{record_id}")
def detail(project: str, record_id: str) -> dict[str, Any]:
    return guarded(lambda: ResearchMemory(project).get(record_id))


@router.patch("/{record_id}")
def annotate(project: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return guarded(lambda: ResearchMemory(project).annotate(record_id, payload))


@router.get("/{record_id}/evidence/{index}")
def evidence(project: str, record_id: str, index: int, download: bool = Query(False)) -> FileResponse:
    memory = guarded(lambda: ResearchMemory(project))
    record = guarded(lambda: memory.get(record_id))
    if not record["integrity"]["intact"]:
        raise HTTPException(status_code=409, detail="证据损坏或缺失")
    if index < 0 or index >= len(record["evidence"]):
        raise HTTPException(status_code=404, detail="证据不存在")
    asset = record["evidence"][index]
    path = memory.asset_path(asset)
    return FileResponse(path, filename=asset["name"], content_disposition_type="attachment" if download else "inline")
