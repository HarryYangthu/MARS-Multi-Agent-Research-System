"""REST API for model discovery and run-local Idea Discovery artifacts."""
from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.bridge.discovery_service import DiscoveryService, DiscoveryServiceError
from app.bridge.discovery_types import DiscoveryReplayView, DiscoveryRunSpec, DiscoveryRunView

router = APIRouter(prefix="/api", tags=["discovery"])
_configured_service: DiscoveryService | None = None


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


class HypothesisActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    reason: str = ""
    idempotency_key: str = ""


def configure_discovery_service(service: DiscoveryService | None) -> None:
    global _configured_service
    _configured_service = service


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


@router.post("/runs/{run_id}/idea-discovery/select")
def select_idea_hypothesis(
    run_id: str,
    payload: SelectHypothesisRequest,
    service: Service,
) -> dict[str, object]:
    try:
        selection = service.select_idea_hypothesis(
            run_id,
            hypothesis_id=payload.hypothesis_id,
            idempotency_key=payload.idempotency_key,
        )
        return selection.model_dump(mode="json")
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)


@router.post("/runs/{run_id}/idea-discovery/hypotheses/{hypothesis_id}/select")
def select_idea_hypothesis_action(
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
        return selection.model_dump(mode="json")
    except DiscoveryServiceError as exc:
        _raise_api_error(exc)
