"""Thin project data-pipeline routes; all orchestration stays in bridge."""
from __future__ import annotations

import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.bridge import data_pipeline as service
from app.harness.runtime.project_scope import safe_scope_path

router = APIRouter(prefix='/api/projects/{project}/data-pipeline', tags=['data-pipeline'])


@router.get('')
async def list_jobs(project: str) -> list[dict[str, Any]]:
    return service.list_jobs(project)


@router.get('/fields/{source_id}')
async def fields(project: str, source_id: str) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(service.inspect_source, project, source_id)
    except (ValueError, OSError, ImportError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('')
async def start(project: str, payload: service.PipelineParameters) -> dict[str, Any]:
    try:
        return service.start(project, payload)
    except (ValueError, OSError, ImportError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{job_id}/{action}')
async def action(project: str, job_id: str, action: str) -> dict[str, Any]:
    try:
        if action == 'analyze':
            return service.analyze(project, job_id)
        if action == 'share':
            return service.publish(project, job_id)
        raise ValueError('未知操作')
    except (ValueError, OSError, ImportError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{job_id}/files/{name}')
async def artifact(project: str, job_id: str, name: str) -> FileResponse:
    if name not in {'spectrum.png', 'processed.npz', 'analysis.md'}:
        raise HTTPException(404, '文件不存在')
    try:
        path = safe_scope_path(service.job_directory(project, job_id), name, must_exist=True)
        if not path.is_file():
            raise FileNotFoundError(name)
        return FileResponse(path)
    except (ValueError, OSError, ImportError) as exc:
        raise HTTPException(404, '文件不存在') from exc
