"""Report Office export and instruction skill endpoints, through the bridge."""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.api.dependencies import get_run_store
from app.bridge import report_service
from app.storage.run_store import RunHandle

router = APIRouter(prefix="/api/reports", tags=["reports"])


class SkillImport(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class SkillChoice(BaseModel):
    ids: list[str] = Field(max_length=20)


class ReportGeneration(BaseModel):
    formats: list[Literal["excel", "word", "powerpoint"]] = Field(default_factory=list, max_length=3)


def _run(run_id: str) -> RunHandle:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(404, "没有找到此研究任务")
    return run


@router.get("/{run_id}")
def get_report_bundle(run_id: str) -> dict[str, Any]:
    return report_service.get_bundle(_run(run_id))


@router.post("/{run_id}/regenerate")
def regenerate_report_bundle(run_id: str, body: ReportGeneration | None = None) -> dict[str, Any]:
    try:
        return report_service.export_bundle(_run(run_id), formats=tuple(body.formats) if body else ())
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{run_id}/files/{filename}")
def download_report_file(run_id: str, filename: str, manifest: str | None = None) -> FileResponse:
    try:
        path = report_service.download_path(_run(run_id), filename, manifest)
        return FileResponse(path, filename=filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, "导出文件不存在，请重新生成") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{run_id}/skills")
def get_report_skills(run_id: str) -> dict[str, Any]:
    try:
        return report_service.get_skills(_run(run_id))
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{run_id}/images")
def preview_report_image(run_id: str, path: str) -> FileResponse:
    try:
        return FileResponse(report_service.report_image_path(_run(run_id), path))
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise HTTPException(404, "图片未保存或不属于此任务") from exc


@router.post("/{run_id}/skills/import")
def import_report_skill(run_id: str, body: SkillImport) -> dict[str, Any]:
    try:
        return report_service.import_skill(_run(run_id), body.content)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{run_id}/skills")
def select_report_skills(run_id: str, body: SkillChoice) -> dict[str, Any]:
    try:
        return report_service.choose_skills(_run(run_id), body.ids)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
