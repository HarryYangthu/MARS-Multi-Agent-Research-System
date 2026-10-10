"""Project execution target endpoints: local simulation default, optional GPU."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/projects", tags=["execution-config"])


class ExecutionConfigPayload(BaseModel):
    device: str
    remote_gpu: dict[str, Any] | None = None


@router.get("/{name}/execution-config")
def get_execution_config(name: str) -> dict[str, Any]:
    from app.bridge.project_execution_config import load_execution_config
    try:
        return load_execution_config(name)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{name}/execution-config")
def put_execution_config(name: str, payload: ExecutionConfigPayload) -> dict[str, Any]:
    from app.bridge.project_execution_config import save_execution_config
    try:
        return save_execution_config(name, payload.model_dump(exclude_none=True))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{name}/execution-config/status")
def get_execution_config_status(name: str) -> dict[str, Any]:
    from app.bridge.project_execution_config import execution_config_status
    try:
        return execution_config_status(name)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
