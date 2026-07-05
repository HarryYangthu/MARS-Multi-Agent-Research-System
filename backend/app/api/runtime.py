"""Runtime operator status APIs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.harness.runtime.system_status import build_runtime_status

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/status")
async def get_runtime_status(project: str | None = None) -> dict[str, Any]:
    return build_runtime_status(project=project)
