"""Project contract inspection only; this router never starts a research run."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.bridge.research_contract_service import (
    FrozenResearchTask, ProjectPreflight, ProjectPreflightError, default_research_budget,
    freeze_research_task, preflight_project,
)
from app.harness.runtime.research_contract import ContractModel, ProjectContract, ResearchBudget

router = APIRouter(prefix="/api/research-contracts", tags=["research-contracts"])


class PrepareResearchPayload(ContractModel):
    project: ProjectContract
    goal: str = Field(min_length=1)
    mode: Literal["bounded_auto", "manual"]
    budget: ResearchBudget


@router.get("/defaults", response_model=ResearchBudget)
def research_defaults() -> ResearchBudget:
    return default_research_budget()


@router.post("/preflight", response_model=ProjectPreflight)
def preflight(project: ProjectContract) -> ProjectPreflight:
    return preflight_project(project)


@router.post("/prepare", response_model=FrozenResearchTask)
def prepare(payload: PrepareResearchPayload) -> FrozenResearchTask:
    try:
        return freeze_research_task(payload.project, goal=payload.goal, mode=payload.mode, budget=payload.budget)
    except ProjectPreflightError as exc:
        raise HTTPException(status_code=422, detail=exc.report.model_dump(mode="json")) from exc
