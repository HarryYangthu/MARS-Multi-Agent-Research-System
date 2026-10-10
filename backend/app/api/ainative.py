"""AI Native repo detection and generation endpoints (project-scoped)."""
from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["ainative"])


@router.post("/{name}/ainative/detect")
def detect(name: str) -> dict[str, Any]:
    from app.bridge.ainative_service import _bound_repo, detect_ainative
    try:
        return detect_ainative(_bound_repo(name))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{name}/ainative/generate")
async def generate(name: str) -> dict[str, Any]:
    from app.bridge.ainative_service import start_generation
    try:
        return start_generation(name)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{name}/ainative/status")
def status(name: str) -> dict[str, Any]:
    from app.bridge.ainative_service import generation_status
    try:
        return generation_status(name)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
