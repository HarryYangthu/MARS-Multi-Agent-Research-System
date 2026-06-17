"""Evaluation report and run scorecard endpoints."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.dependencies import get_event_bus, get_run_store
from app.bridge.commander_eval import run_commander_attribution_eval
from app.bridge.evaluation_export_service import (
    create_run_post_training_export,
    get_run_post_training_export,
)
from app.bridge.evaluation_service import build_artifact_evaluation_summary
from app.bridge.evaluation_policy import evaluate_scorecard
from app.harness.evaluation.artifacts import read_reports_for_artifact
from app.storage.artifact_store import ArtifactRef

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


@router.get("/commander-attribution")
async def get_commander_attribution_eval(project: str = "moe-pimc") -> dict[str, Any]:
    return run_commander_attribution_eval(project=project)


@router.get("/runs/{run_id}/scorecard")
async def get_scorecard(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = run.subdir("events") / "evaluation_scorecard.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="evaluation scorecard not found")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="evaluation scorecard malformed")
    quality_gate_path = run.subdir("events") / "evaluation_quality_gate.json"
    if quality_gate_path.exists():
        quality_gate = json.loads(quality_gate_path.read_text(encoding="utf-8"))
        if isinstance(quality_gate, dict):
            parsed["quality_gate"] = quality_gate
    else:
        parsed["quality_gate"] = evaluate_scorecard(parsed)
    return parsed


@router.get("/runs/{run_id}/post-training-export")
async def get_post_training_export(
    run_id: str,
    preview_limit: int = 5,
) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    manifest = get_run_post_training_export(run=run, preview_limit=preview_limit)
    if manifest is None:
        raise HTTPException(status_code=404, detail="post-training export not found")
    return manifest


@router.post("/runs/{run_id}/post-training-export")
async def create_post_training_export(
    run_id: str,
    include_drafts: bool | None = None,
) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return await create_run_post_training_export(
            run=run,
            include_drafts=include_drafts,
            bus=get_event_bus(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}/artifacts/{agent_dir}/{stem}/{version}")
async def list_artifact_evaluations(
    run_id: str,
    agent_dir: str,
    stem: str,
    version: str,
) -> list[dict[str, Any]]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        run.subdir(agent_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return read_reports_for_artifact(
        run_root=run.root,
        agent_dir=agent_dir,
        stem=stem,
        version=version,
    )


@router.get("/runs/{run_id}/artifacts/{agent_dir}/{stem}/{version}/summary")
async def get_artifact_evaluation_summary(
    run_id: str,
    agent_dir: str,
    stem: str,
    version: str,
) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        directory = run.subdir(agent_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = directory / f"{stem}.{version}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact not found")
    return build_artifact_evaluation_summary(
        run=run,
        ref=ArtifactRef(
            run_id=run.run_id,
            agent_dir=agent_dir,
            stem=stem,
            version=version,
            path=path,
        ),
        node_key=agent_dir,
    )
