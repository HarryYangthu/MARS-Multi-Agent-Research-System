"""Project-scoped experiments: named research efforts sharing project background."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.bridge.experiment_service import (
    create_experiment,
    get_experiment,
    list_experiments,
)

router = APIRouter(prefix="/api/projects", tags=["experiments"])


class ExperimentPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class ExperimentView(BaseModel):
    id: str
    project: str
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    run_count: int = 0
    latest_run_id: str | None = None
    latest_run_created_at: str | None = None


class ExperimentRunView(BaseModel):
    run_id: str
    experiment_id: str
    task: str
    entrypoint: str
    created_at: str


def _run_experiment_id(handle: Any) -> str:
    return str((handle.meta or {}).get("experiment_id", "") or "")


def _experiment_runs(project: str, exp_id: str) -> list[ExperimentRunView]:
    from app.api.dependencies import get_run_store

    out = []
    for handle in get_run_store().list():
        if handle.project != project or _run_experiment_id(handle) != exp_id:
            continue
        out.append(ExperimentRunView(
            run_id=handle.run_id, experiment_id=exp_id, task=handle.task,
            entrypoint=handle.entrypoint, created_at=handle.created_at))
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


def _view(record: dict[str, Any], runs: list[ExperimentRunView]) -> ExperimentView:
    latest = runs[0] if runs else None
    return ExperimentView(
        id=str(record["id"]), project=str(record.get("project", "")),
        name=str(record.get("name", "")), description=str(record.get("description", "")),
        created_at=str(record.get("created_at", "")), updated_at=str(record.get("updated_at", "")),
        run_count=len(runs),
        latest_run_id=latest.run_id if latest else None,
        latest_run_created_at=latest.created_at if latest else None,
    )


@router.get("/{name}/experiments", response_model=list[ExperimentView])
def list_project_experiments(name: str) -> list[ExperimentView]:
    try:
        records = list_experiments(name)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.api.dependencies import get_run_store

    project_runs = [h for h in get_run_store().list() if h.project == name]
    by_experiment: dict[str, list[ExperimentRunView]] = {}
    for handle in project_runs:
        exp = _run_experiment_id(handle)
        if not exp:
            continue
        by_experiment.setdefault(exp, []).append(ExperimentRunView(
            run_id=handle.run_id, experiment_id=exp, task=handle.task,
            entrypoint=handle.entrypoint, created_at=handle.created_at))
    for runs in by_experiment.values():
        runs.sort(key=lambda r: r.created_at, reverse=True)
    return [_view(record, by_experiment.get(str(record["id"]), [])) for record in records]


@router.post("/{name}/experiments", response_model=ExperimentView, status_code=201)
def create_project_experiment(name: str, payload: ExperimentPayload) -> ExperimentView:
    try:
        record = create_experiment(name, payload.name, payload.description)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _view(record, [])


@router.get("/{name}/experiments/{exp_id}", response_model=ExperimentView)
def get_project_experiment(name: str, exp_id: str) -> ExperimentView:
    try:
        record = get_experiment(name, exp_id)
    except (OSError, ValueError) as exc:
        status = 404 if str(exc) == "实验不存在" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _view(record, _experiment_runs(name, exp_id))


@router.get("/{name}/experiments/{exp_id}/runs", response_model=list[ExperimentRunView])
def get_project_experiment_runs(name: str, exp_id: str) -> list[ExperimentRunView]:
    try:
        get_experiment(name, exp_id)
    except (OSError, ValueError) as exc:
        status = 404 if str(exc) == "实验不存在" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _experiment_runs(name, exp_id)
