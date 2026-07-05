"""Runtime readiness endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.harness.runtime.readiness import check_readiness

router = APIRouter(prefix="/api/readiness", tags=["readiness"])


class ReadinessCheckView(BaseModel):
    name: str
    ready: bool
    severity: str
    message: str
    details: dict[str, Any]


class ReadinessView(BaseModel):
    ready: bool
    runtime_mode: str
    mock_mode: str
    execution_backend: str
    project: str
    checks: list[ReadinessCheckView]


@router.get("", response_model=ReadinessView)
async def get_readiness(project: str | None = None) -> ReadinessView:
    report = check_readiness(project=project)
    return ReadinessView(**report.to_dict())
