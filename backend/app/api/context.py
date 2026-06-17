"""Context Engineering V1 APIs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_run_store
from app.harness.context.engine import CompileContextInput, compile_context
from app.harness.context.manifest_v2 import manifest_file_for_id
from app.harness.context.raw_store import read_raw_context
from app.harness.llm.model_registry import get_agent_config
from app.settings import get_settings

router = APIRouter(prefix="/api/context", tags=["context"])


class ContextPreviewPayload(BaseModel):
    agent: str = Field(..., min_length=1)
    project: str = Field(default="moe-pimc", min_length=1)
    task: str = ""
    upstream: dict[str, str] = Field(default_factory=dict)


@router.get("/runs/{run_id}")
async def get_context_run(run_id: str) -> dict[str, Any]:
    _ensure_enabled()
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    manifests = [_manifest_summary(path, run_root=run.root) for path in _manifest_paths(run.root)]
    risk_summary: dict[str, int] = {}
    used_tokens = 0
    over_budget = 0
    agents: set[str] = set()
    for item in manifests:
        agents.add(str(item.get("agent", "")))
        budget = item.get("budget", {})
        if isinstance(budget, dict):
            used_tokens += int(budget.get("used", 0) or 0)
            over_budget += 1 if bool(budget.get("over_budget", False)) else 0
        risks = item.get("risk_counts", {})
        if isinstance(risks, dict):
            for key, value in risks.items():
                risk_summary[str(key)] = risk_summary.get(str(key), 0) + int(value or 0)
    return {
        "run_id": run_id,
        "project": run.project,
        "agents": sorted(agent for agent in agents if agent),
        "manifests": manifests,
        "budget_summary": {
            "manifest_count": len(manifests),
            "used_tokens": used_tokens,
            "over_budget_count": over_budget,
        },
        "risk_summary": risk_summary,
    }


@router.get("/runs/{run_id}/manifests/{manifest_id}")
async def get_context_manifest(run_id: str, manifest_id: str) -> dict[str, Any]:
    _ensure_enabled()
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = manifest_file_for_id(run_root=run.root, manifest_id=manifest_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="manifest not found")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="manifest is invalid")
    return raw


@router.get("/runs/{run_id}/raw/{raw_ref:path}")
async def get_context_raw(run_id: str, raw_ref: str) -> dict[str, Any]:
    _ensure_enabled()
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return read_raw_context(run_root=run.root, raw_ref=raw_ref)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="raw_ref not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/preview")
async def preview_context(payload: ContextPreviewPayload) -> dict[str, Any]:
    _ensure_enabled()
    try:
        cfg = get_agent_config(payload.agent)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = compile_context(
        CompileContextInput(
            agent=payload.agent,
            node_key=payload.agent,
            project=payload.project,
            output_schema=cfg.output_schema,
            system=f"MARS {payload.agent} agent. Output schema: {cfg.output_schema}.",
            project_context=f"Project: {payload.project}.",
            task=payload.task,
            upstream=payload.upstream,
            metadata={},
            run_id="preview",
            run_root=None,
            purpose="preview",
            tool_names=cfg.tools,
        ),
        write=False,
    )
    return result.manifest.to_dict()


def _ensure_enabled() -> None:
    if not get_settings().mars_context_workbench_enabled:
        raise HTTPException(status_code=404, detail="context workbench disabled")


def _manifest_paths(run_root: Path) -> list[Path]:
    context_dir = run_root / "context"
    if not context_dir.exists():
        return []
    return sorted(
        path
        for path in context_dir.glob("context_manifest.v2.*.json")
        if path.name != "context_manifest.v2.json"
    )


def _manifest_summary(path: Path, *, run_root: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    diagnostics = raw.get("diagnostics", {})
    risk_counts = diagnostics.get("risk_counts", {}) if isinstance(diagnostics, dict) else {}
    return {
        "manifest_id": str(raw.get("manifest_id") or path.stem),
        "agent": str(raw.get("agent", "")),
        "node_key": str(raw.get("node_key", "")),
        "purpose": str(raw.get("purpose", "")),
        "created_at": str(raw.get("created_at", "")),
        "path": path.relative_to(run_root).as_posix(),
        "budget": raw.get("budget", {}),
        "segment_count": len(raw.get("segments", [])) if isinstance(raw.get("segments"), list) else 0,
        "risk_counts": risk_counts if isinstance(risk_counts, dict) else {},
    }
