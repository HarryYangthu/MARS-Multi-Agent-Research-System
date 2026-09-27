"""Project contracts and persisted run admission; never starts model execution."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_orchestrator
from app.bridge.research_contract_service import (
    FrozenResearchTask, ProjectPreflight, ProjectPreflightError, default_research_budget,
    freeze_research_task, preflight_project, StaleResearchContractError,
)
from app.bridge.research_run_service import (
    ResearchCreationStatus, ResearchRunCreated, lookup_research_creation, save_research_run,
)
from app.storage.research_creation_store import (
    REQUEST_ID_PATTERN, CreationCatalogIntegrityError, CreationRequestConflict,
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
    request_id: str | None = Field(default=None, pattern=REQUEST_ID_PATTERN)


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


@router.post("/runs", response_model=ResearchRunCreated | ResearchCreationStatus, status_code=201)
async def create_contract_run(payload: CreateResearchRunPayload, response: Response) -> ResearchRunCreated | ResearchCreationStatus:
    try:
        owner = get_orchestrator()  # Resolve the shared owner on the event-loop thread.
        result = await run_in_threadpool(save_research_run, owner, name=payload.name, contract=payload.contract,
                                   request_id=payload.request_id)
    except CreationRequestConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "creation_request_conflict",
            "request_id": payload.request_id, "message": "Request ID already belongs to a different declaration"}) from exc
    except CreationCatalogIntegrityError as exc:
        raise HTTPException(status_code=503, detail={"code": "creation_catalog_unavailable",
            "request_id": payload.request_id, "message": "Creation evidence is unavailable; do not submit a replacement"}) from exc
    except ProjectPreflightError as exc:
        raise HTTPException(status_code=422, detail=exc.report.model_dump(mode="json")) from exc
    except StaleResearchContractError as exc:
        raise HTTPException(status_code=409, detail={"code": "stale_research_contract",
            "message": "Declared source files changed; freeze a new research contract"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_research_contract",
            "message": "Research contract or run declaration is invalid"}) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail={"code": "creation_storage_unavailable",
            "request_id": payload.request_id, "message": "Creation outcome could not be confirmed; query the request ID"}) from exc
    if isinstance(result, ResearchCreationStatus):
        if result.status in {"unknown", "rejected"}:
            raise HTTPException(status_code=422 if result.status == "rejected" else 409,
                                detail=result.model_dump(mode="json"))
        response.status_code = 202
    return result


@router.get("/requests/{request_id}", response_model=ResearchCreationStatus)
async def creation_status(request_id: str = Path(pattern=REQUEST_ID_PATTERN)) -> ResearchCreationStatus:
    try:
        store = get_orchestrator().run_store
        result = await run_in_threadpool(lookup_research_creation, store, request_id)
    except (CreationCatalogIntegrityError, OSError) as exc:
        raise HTTPException(status_code=503, detail={"code": "creation_catalog_unavailable",
            "request_id": request_id, "message": "Creation evidence is unavailable; do not submit a replacement"}) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "creation_request_not_found", "request_id": request_id})
    return result
