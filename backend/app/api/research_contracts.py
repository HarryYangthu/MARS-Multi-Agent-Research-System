"""Project contracts and persisted run admission; never starts model execution."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.api.dependencies import get_orchestrator
from app.bridge.research_contract_service import (
    FrozenResearchTask, ProjectPreflight, ProjectPreflightError, default_research_budget,
    freeze_research_task, preflight_project, StaleResearchContractError,
)
from app.bridge.research_run_service import (
    ResearchExecutionAdmission, create_research_run, research_execution_admission,
)
from app.harness.runtime.research_contract import ContractModel, ProjectContract, ResearchBudget

router = APIRouter(prefix="/api/research-contracts", tags=["research-contracts"])


class PrepareResearchPayload(ContractModel):
    project: ProjectContract
    goal: str = Field(min_length=1)
    mode: Literal["bounded_auto", "manual"]
    budget: ResearchBudget


class CreateResearchRunPayload(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    contract: FrozenResearchTask


class ResearchRunCreated(ContractModel):
    run_id: str
    project: str
    task: str
    entrypoint: Literal["pipeline"] = "pipeline"
    created_at: str
    task_sha256: str
    status: Literal["created"] = "created"
    research_started: Literal[False] = False
    execution_admission: ResearchExecutionAdmission


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


@router.post("/runs", response_model=ResearchRunCreated, status_code=201)
async def create_contract_run(payload: CreateResearchRunPayload) -> ResearchRunCreated:
    try:
        session = create_research_run(get_orchestrator(), name=payload.name, contract=payload.contract)
    except ProjectPreflightError as exc:
        raise HTTPException(status_code=422, detail=exc.report.model_dump(mode="json")) from exc
    except StaleResearchContractError as exc:
        raise HTTPException(status_code=409, detail={"code": "stale_research_contract",
            "message": "Declared source files changed; freeze a new research contract"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_research_contract",
            "message": "Research contract or run declaration is invalid"}) from exc
    admission = research_execution_admission(session.run, session.request.extra)
    assert admission is not None
    return ResearchRunCreated(run_id=session.run.run_id, project=session.run.project, task=session.run.task,
        created_at=session.run.created_at, task_sha256=payload.contract.task_sha256, execution_admission=admission)
