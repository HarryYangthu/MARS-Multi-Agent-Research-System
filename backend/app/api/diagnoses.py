"""Diagnosis artifact and feedback-loop APIs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_orchestrator, get_run_store
from app.bridge.commander_observability import build_commander_observability
from app.harness.schema.frontmatter_parser import parse as fm_parse
from app.storage.artifact_store import ArtifactStore
from app.storage.self_evolution_store import (
    approve_self_evolution_mutation,
    build_self_evolution_levers,
    create_self_evolution_mutation,
    list_self_evolution_mutations,
    read_jsonl,
    reject_self_evolution_mutation,
)

router = APIRouter(prefix="/api/runs", tags=["diagnoses"])


class DiagnosisView(BaseModel):
    run_id: str
    version: str
    path: str
    text: str
    metadata: dict[str, Any]


class FeedbackPacketView(BaseModel):
    run_id: str
    attempt: int
    path: str
    text: str
    metadata: dict[str, Any]


class RunMemoryEventView(BaseModel):
    run_id: str
    path: str
    items: list[dict[str, Any]]


class SelfEvolutionLeversView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_id: str = Field(alias="schema")
    run_id: str
    project: str
    mutation_mode: str
    allowed_actions: list[str]
    levers: dict[str, list[dict[str, Any]]]
    counts: dict[str, int]


class SelfEvolutionMutationPayload(BaseModel):
    lever_id: str = Field(..., min_length=1)
    agent: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    proposed_content: str = Field(..., min_length=1)
    rationale: str = ""


class SelfEvolutionMutationDecisionPayload(BaseModel):
    reviewer_note: str = ""


class SelfEvolutionMutationDecisionView(BaseModel):
    mutation_id: str
    agent: str
    path: str
    status: str
    applied_path: str = ""


@router.get("/{run_id}/diagnoses", response_model=list[DiagnosisView])
async def list_diagnoses(run_id: str) -> list[DiagnosisView]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    store = ArtifactStore(run)
    out: list[DiagnosisView] = []
    for ref in store.list_versions(agent_dir="diagnosis", stem="diagnosis"):
        text = ref.path.read_text(encoding="utf-8")
        out.append(
            DiagnosisView(
                run_id=run_id,
                version=ref.version,
                path=str(ref.path),
                text=text,
                metadata=fm_parse(text).metadata,
            )
        )
    return out


@router.get("/{run_id}/diagnoses/{version}", response_model=DiagnosisView)
async def get_diagnosis(run_id: str, version: str) -> DiagnosisView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("diagnosis") / f"diagnosis.{version}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="diagnosis not found")
    text = path.read_text(encoding="utf-8")
    return DiagnosisView(
        run_id=run_id,
        version=version,
        path=str(path),
        text=text,
        metadata=fm_parse(text).metadata,
    )


@router.get("/{run_id}/feedback-packets", response_model=list[FeedbackPacketView])
async def list_feedback_packets(run_id: str) -> list[FeedbackPacketView]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    out: list[FeedbackPacketView] = []
    for path in sorted(run.subdir("diagnosis").glob("feedback_packet.attempt_*.md")):
        text = path.read_text(encoding="utf-8")
        metadata = fm_parse(text).metadata
        out.append(
            FeedbackPacketView(
                run_id=run_id,
                attempt=int(metadata.get("attempt", 0) or 0),
                path=str(path),
                text=text,
                metadata=metadata,
            )
        )
    return out


@router.get(
    "/{run_id}/feedback-packets/{attempt}",
    response_model=FeedbackPacketView,
)
async def get_feedback_packet(run_id: str, attempt: int) -> FeedbackPacketView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("diagnosis") / f"feedback_packet.attempt_{attempt}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="feedback packet not found")
    text = path.read_text(encoding="utf-8")
    return FeedbackPacketView(
        run_id=run_id,
        attempt=attempt,
        path=str(path),
        text=text,
        metadata=fm_parse(text).metadata,
    )


@router.get("/{run_id}/memory-candidates", response_model=RunMemoryEventView)
async def list_memory_candidates(run_id: str) -> RunMemoryEventView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("memory") / "memory_candidates.jsonl"
    return RunMemoryEventView(
        run_id=run_id,
        path=str(path),
        items=read_jsonl(path),
    )


@router.get("/{run_id}/episode-memory", response_model=RunMemoryEventView)
async def list_episode_memory(run_id: str) -> RunMemoryEventView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("memory") / "episode_memory.jsonl"
    return RunMemoryEventView(
        run_id=run_id,
        path=str(path),
        items=read_jsonl(path),
    )


@router.get(
    "/{run_id}/self-evolution/levers",
    response_model=SelfEvolutionLeversView,
)
async def get_self_evolution_levers(run_id: str) -> SelfEvolutionLeversView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return SelfEvolutionLeversView(**build_self_evolution_levers(run=run))


@router.get(
    "/{run_id}/self-evolution/mutations",
    response_model=RunMemoryEventView,
)
async def list_self_evolution_mutation_proposals(run_id: str) -> RunMemoryEventView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("memory") / "self_evolution_mutations.jsonl"
    return RunMemoryEventView(
        run_id=run_id,
        path=str(path),
        items=list_self_evolution_mutations(run=run),
    )


@router.post("/{run_id}/self-evolution/mutations")
async def create_self_evolution_mutation_proposal(
    run_id: str,
    payload: SelfEvolutionMutationPayload,
) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return create_self_evolution_mutation(
            run=run,
            lever_id=payload.lever_id,
            agent=payload.agent,
            path=payload.path,
            proposed_content=payload.proposed_content,
            rationale=payload.rationale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{run_id}/self-evolution/mutations/{mutation_id}/approve",
    response_model=SelfEvolutionMutationDecisionView,
)
async def approve_self_evolution_mutation_proposal(
    run_id: str,
    mutation_id: str,
    payload: SelfEvolutionMutationDecisionPayload | None = None,
) -> SelfEvolutionMutationDecisionView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        result = approve_self_evolution_mutation(
            run=run,
            mutation_id=mutation_id,
            reviewer_note=payload.reviewer_note if payload is not None else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SelfEvolutionMutationDecisionView(**result)


@router.post(
    "/{run_id}/self-evolution/mutations/{mutation_id}/reject",
    response_model=SelfEvolutionMutationDecisionView,
)
async def reject_self_evolution_mutation_proposal(
    run_id: str,
    mutation_id: str,
    payload: SelfEvolutionMutationDecisionPayload | None = None,
) -> SelfEvolutionMutationDecisionView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        result = reject_self_evolution_mutation(
            run=run,
            mutation_id=mutation_id,
            reviewer_note=payload.reviewer_note if payload is not None else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SelfEvolutionMutationDecisionView(**result)


@router.get("/{run_id}/commander-observability")
async def get_commander_observability(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_commander_observability(run)


@router.post("/{run_id}/feedback-loop/{diagnosis_version}/start", status_code=202)
async def start_feedback_loop(run_id: str, diagnosis_version: str) -> dict[str, Any]:
    orch = get_orchestrator()
    try:
        session = orch.session(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc

    run = session.run
    path = run.subdir("diagnosis") / f"diagnosis.{diagnosis_version}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="diagnosis not found")
    result = await orch.start_feedback_loop(
        run_id=run_id,
        diagnosis_version=diagnosis_version,
    )
    if not result.get("ok", False):
        raise HTTPException(status_code=404, detail=str(result.get("error", "failed")))
    result.pop("ok", None)
    return result
