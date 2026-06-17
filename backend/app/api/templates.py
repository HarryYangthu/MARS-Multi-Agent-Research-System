"""Schema-bound markdown templates for the New Run / Standalone form."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.harness.schema.validator import SUPPORTED_SCHEMAS
from app.settings import repo_root

router = APIRouter(prefix="/api/templates", tags=["templates"])


class Template(BaseModel):
    schema_id: str
    text: str


class TemplateMeta(BaseModel):
    schema_id: str
    agent: str
    stem: str


# Inverse of SCHEMA_TO_AGENT — kept here so the API doesn't need to
# import storage internals.
SCHEMA_TO_AGENT_AND_STEM: dict[str, tuple[str, str]] = {
    "proposal.v1": ("idea", "idea_proposal"),
    "experiment_plan.v1": ("experiment", "experiment_plan"),
    "code_spec.v1": ("coding", "code_spec"),
    "run_log.v1": ("execution", "run_log"),
    "diagnosis.v1": ("diagnosis", "diagnosis"),
    "feedback_packet.v1": ("diagnosis", "feedback_packet"),
    "report.v1": ("writing", "research_report"),
}


def _path_for(schema_id: str) -> Path:
    return repo_root() / "templates" / "artifacts" / f"{schema_id}.md"


@router.get("", response_model=list[TemplateMeta])
async def list_templates() -> list[TemplateMeta]:
    out: list[TemplateMeta] = []
    for sid in SUPPORTED_SCHEMAS:
        agent, stem = SCHEMA_TO_AGENT_AND_STEM.get(sid, (sid, sid))
        out.append(TemplateMeta(schema_id=sid, agent=agent, stem=stem))
    return out


@router.get("/{schema_id}", response_model=Template)
async def get_template(schema_id: str) -> Template:
    if schema_id not in SUPPORTED_SCHEMAS:
        raise HTTPException(status_code=404, detail=f"unknown schema '{schema_id}'")
    p = _path_for(schema_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"template missing: {p.name}")
    return Template(schema_id=schema_id, text=p.read_text(encoding="utf-8"))


@router.get("/by_agent/{agent}", response_model=Template)
async def get_template_by_agent(agent: str) -> Template:
    for sid, (a, _stem) in SCHEMA_TO_AGENT_AND_STEM.items():
        if a == agent:
            return await get_template(sid)
    raise HTTPException(status_code=404, detail=f"no template for agent '{agent}'")
