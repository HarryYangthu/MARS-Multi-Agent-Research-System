"""REST API for model discovery and run-local Idea Discovery artifacts."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.bridge.discovery_service import (
    CandidateDecisionRecord,
    DiscoveryService,
    DiscoveryServiceError,
    HypothesisActionRecord,
    IdeaSelectionRequest,
)
from app.bridge.discovery_types import DiscoveryReplayView, DiscoveryRunSpec, DiscoveryRunView

router = APIRouter(prefix="/api", tags=["discovery"])
_configured_service: DiscoveryService | None = None
IdeaSelectionHandler = Callable[[IdeaSelectionRequest], Awaitable[str]]
_configured_idea_selection_handler: IdeaSelectionHandler | None = None


class CreateDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: DiscoveryRunSpec
    idempotency_key: str = Field(min_length=1)


class WaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wait: bool = False


class ReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = "user_requested"


class SelectHypothesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    actor: str = "api-researcher"
    reason: str = ""


class HypothesisActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    reason: str = ""
    idempotency_key: str = ""


class AddHypothesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    statement: str = Field(min_length=1)


class EditHypothesisRequest(AddHypothesisRequest):
    pass


class CandidateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "promote"]
    actor: str = Field(min_length=1)
    reason: str = ""
    idempotency_key: str = Field(min_length=1)


def configure_discovery_service(service: DiscoveryService | None) -> None:
    global _configured_service
    _configured_service = service


def configure_idea_selection_handler(handler: IdeaSelectionHandler | None) -> None:
    global _configured_idea_selection_handler
    _configured_idea_selection_handler = handler


def get_discovery_service() -> DiscoveryService:
    if _configured_service is None:
        raise HTTPException(status_code=503, detail="DiscoveryService is not configured")
    return _configured_service


Service = Annotated[DiscoveryService, Depends(get_discovery_service)]


def _raise_api_error(exc: DiscoveryServiceError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code.value, "message": exc.detail},
    ) from exc


@router.post("/discovery/runs", response_model=DiscoveryRunView)
async def create_discovery(payload: CreateDiscoveryRequest, service: Service) -> DiscoveryRunView:
    try:
        return await service.create(payload.spec, idempotency_key=payload.idempotency_key)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post("/discovery/runs/{run_id}/start", response_model=DiscoveryRunView)
async def start_discovery(run_id: str, payload: WaitRequest, service: Service) -> DiscoveryRunView:
    try:
        return await service.start(run_id, wait=payload.wait)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.get("/discovery/runs/{run_id}", response_model=DiscoveryRunView)
def discovery_status(run_id: str, service: Service) -> DiscoveryRunView:
    try:
        return service.status(run_id)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post("/discovery/runs/{run_id}/pause", response_model=DiscoveryRunView)
async def pause_discovery(run_id: str, payload: ReasonRequest, service: Service) -> DiscoveryRunView:
    try:
        return await service.pause(run_id, reason=payload.reason)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post("/discovery/runs/{run_id}/resume", response_model=DiscoveryRunView)
async def resume_discovery(run_id: str, payload: WaitRequest, service: Service) -> DiscoveryRunView:
    try:
        return await service.resume(run_id, wait=payload.wait)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post("/discovery/runs/{run_id}/stop", response_model=DiscoveryRunView)
async def stop_discovery(run_id: str, payload: ReasonRequest, service: Service) -> DiscoveryRunView:
    try:
        return await service.stop(run_id, reason=payload.reason)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.get("/discovery/runs/{run_id}/replay", response_model=DiscoveryReplayView)
def replay_discovery(run_id: str, service: Service) -> DiscoveryReplayView:
    try:
        return service.replay(run_id)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post(
    "/discovery/runs/{run_id}/candidates/{candidate_id}/decision",
    response_model=CandidateDecisionRecord,
)
async def decide_discovery_candidate(
    run_id: str,
    candidate_id: str,
    payload: CandidateDecisionRequest,
    service: Service,
) -> CandidateDecisionRecord:
    try:
        return await service.decide_candidate(
            run_id,
            candidate_id,
            action=payload.action,
            actor=payload.actor,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
        )
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.get("/runs/{run_id}/idea-discovery")
def idea_discovery(run_id: str, service: Service) -> dict[str, object]:
    try:
        return service.idea_discovery(run_id)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.get("/runs/{run_id}/idea-discovery/hypotheses")
def idea_hypotheses(run_id: str, service: Service) -> dict[str, object]:
    try:
        return {"run_id": run_id, "hypotheses": service.idea_hypotheses(run_id)}
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post(
    "/runs/{run_id}/idea-discovery/hypotheses",
    response_model=HypothesisActionRecord,
)
def add_idea_hypothesis(
    run_id: str,
    payload: AddHypothesisRequest,
    service: Service,
) -> HypothesisActionRecord:
    try:
        return service.add_idea_hypothesis(
            run_id,
            statement=payload.statement,
            actor=payload.actor,
            reason=payload.reason,
        )
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.patch(
    "/runs/{run_id}/idea-discovery/hypotheses/{hypothesis_id}",
    response_model=HypothesisActionRecord,
)
def edit_idea_hypothesis(
    run_id: str,
    hypothesis_id: str,
    payload: EditHypothesisRequest,
    service: Service,
) -> HypothesisActionRecord:
    try:
        return service.edit_idea_hypothesis(
            run_id,
            hypothesis_id,
            statement=payload.statement,
            actor=payload.actor,
            reason=payload.reason,
        )
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post(
    "/runs/{run_id}/idea-discovery/hypotheses/{hypothesis_id}/reject",
    response_model=HypothesisActionRecord,
)
def reject_idea_hypothesis(
    run_id: str,
    hypothesis_id: str,
    payload: HypothesisActionRequest,
    service: Service,
) -> HypothesisActionRecord:
    try:
        return service.reject_idea_hypothesis(
            run_id,
            hypothesis_id,
            actor=payload.actor,
            reason=payload.reason,
        )
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post("/runs/{run_id}/idea-discovery/select")
async def select_idea_hypothesis(
    run_id: str,
    payload: SelectHypothesisRequest,
    service: Service,
) -> dict[str, object]:
    try:
        selection = service.select_idea_hypothesis(
            run_id,
            hypothesis_id=payload.hypothesis_id,
            idempotency_key=payload.idempotency_key,
            actor=payload.actor,
            reason=payload.reason,
        )
        return await _materialize_idea_selection(selection)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post("/runs/{run_id}/idea-discovery/hypotheses/{hypothesis_id}/select")
async def select_idea_hypothesis_action(
    run_id: str,
    hypothesis_id: str,
    payload: HypothesisActionRequest,
    service: Service,
) -> dict[str, object]:
    key = payload.idempotency_key or (
        f"select:{run_id}:{hypothesis_id}:{payload.actor}:{payload.reason}"
    )
    try:
        selection = service.select_idea_hypothesis(
            run_id,
            hypothesis_id=hypothesis_id,
            idempotency_key=key,
            actor=payload.actor,
            reason=payload.reason,
        )
        return await _materialize_idea_selection(selection)
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


async def _materialize_idea_selection(
    selection: IdeaSelectionRequest,
) -> dict[str, object]:
    payload = selection.model_dump(mode="json")
    if _configured_idea_selection_handler is None:
        return {**payload, "status": "pending"}
    try:
        proposal_ref = await _configured_idea_selection_handler(selection)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "idea_selection_failed", "message": str(exc)},
        ) from exc
    return {**payload, "status": "completed", "proposal_ref": proposal_ref}
