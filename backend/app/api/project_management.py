"""Human-facing project setup and reversible removal operations."""
from __future__ import annotations

from typing import Any, Literal
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.api.dependencies import existing_orchestrator, get_run_store
from app.bridge import project_lifecycle, project_training

router = APIRouter(prefix='/api/projects', tags=['project-management'])


def _idle(name: str) -> None:
    store = get_run_store()
    project_lifecycle.require_idle_project(name, store)
    owner = existing_orchestrator()
    if owner is not None:
        for run_id in owner.owned_tasks.run_ids():
            if owner.session(run_id).run.project == name and owner.owned_tasks.active(run_id):
                raise ValueError('项目仍有后台任务，请先停止：' + run_id)


class TrainingPayload(BaseModel):
    python: str = Field(min_length=1, max_length=4096)
    entrypoint: str = Field(default='mars_job.py', min_length=1, max_length=500)
    config_pattern: str = Field(default='configs/*.yaml', min_length=1, max_length=500)
    required_metrics: list[str] = Field(default_factory=list, max_length=100)
    consumes_data_path: bool = True


@router.get('/{name}/training')
def get_training(name: str) -> dict[str, Any]:
    try:
        return project_training.training_status(name)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put('/{name}/training')
def put_training(name: str, payload: TrainingPayload) -> dict[str, Any]:
    try:
        _idle(name)
        return project_training.save_training(name, **payload.model_dump())
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/removed/list')
def list_removed() -> list[dict[str, Any]]:
    from app.harness.project_workspace import _entries, registry_path
    active = _entries(registry_path())
    return [row for row in project_lifecycle.removed_projects().values() if row['project'] not in active]


@router.post('/removed/{removal_id}/restore')
def restore_removed(removal_id: str) -> dict[str, Any]:
    try:
        return project_lifecycle.restore_project(removal_id)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{name}/removal')
def preview_removal(name: str) -> dict[str, Any]:
    try:
        return project_lifecycle.removal_preview(name, get_run_store())
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


class RemovalPayload(BaseModel):
    mode: Literal['detach', 'recycle']
    expected_path: str
    confirmation: str


@router.delete('/{name}')
def delete_project(name: str, payload: RemovalPayload) -> dict[str, Any]:
    try:
        _idle(name)
        return project_lifecycle.remove_project(name, **payload.model_dump(), store=get_run_store())
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
