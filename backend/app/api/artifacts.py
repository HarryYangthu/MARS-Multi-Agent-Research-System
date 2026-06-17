"""REST endpoints for artifact viewing / editing / approval."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.api.dependencies import get_event_bus, get_orchestrator, get_run_store
from app.harness.schema.frontmatter_parser import dumps as fm_dumps, parse as fm_parse
from app.harness.schema.validator import validate_metadata
from app.harness.sedimentation.hooks import sediment_approved_artifact
from app.hitl.approval import approve as do_approve, reject as do_reject
from app.hitl.audit_log import read as read_audit
from app.hitl.diff_view import unified
from app.hitl.review_session import get_registry as get_review_registry
from app.hitl.revision_loop import apply_human_edit
from app.harness.tools.registry import ToolContext
from app.harness.tools.registry import get_registry as get_tool_registry
from app.storage.artifact_store import ArtifactStore

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class ArtifactView(BaseModel):
    run_id: str
    agent_dir: str
    stem: str
    version: str
    path: str
    text: str
    metadata: dict[str, Any]
    schema_id: str | None = None
    valid: bool
    errors: list[dict[str, str]] = []


class PatchView(BaseModel):
    run_id: str
    version: str
    path: str
    text: str
    approved: bool


class EditPayload(BaseModel):
    body: str | None = None
    metadata_patch: dict[str, Any] = Field(default_factory=dict)


class CommentPayload(BaseModel):
    text: str


class RejectPayload(BaseModel):
    reason: str = ""


def _resolve(run_id: str, agent_dir: str, stem: str, version: str) -> Path:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    p = run.subdir(agent_dir) / f"{stem}.{version}.md"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"artifact missing: {p.name}")
    return p


def _read_view(run_id: str, agent_dir: str, stem: str, version: str) -> ArtifactView:
    p = _resolve(run_id, agent_dir, stem, version)
    text = p.read_text(encoding="utf-8")
    parsed = fm_parse(text)
    metadata = parsed.metadata
    schema_id = str(metadata.get("schema") or "") or None
    result = validate_metadata(metadata, expected_schema=schema_id)
    return ArtifactView(
        run_id=run_id,
        agent_dir=agent_dir,
        stem=stem,
        version=version,
        path=str(p),
        text=text,
        metadata=metadata,
        schema_id=result.schema_id,
        valid=result.valid,
        errors=[{"path": e.path, "message": e.message} for e in result.errors],
    )


def _sediment_after_inline_approve(run_id: str, agent_dir: str, approved: Any) -> None:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        return
    try:
        sediment_approved_artifact(
            run=run,
            agent=agent_dir,
            artifact_ref=approved,
        )
    except Exception as exc:  # pragma: no cover - approval response remains durable
        logger.warning(
            "inline-approved artifact sedimentation failed: run={} agent={} artifact={} error={}",
            run_id,
            agent_dir,
            approved.path.name,
            exc,
        )


def _patch_path(run_id: str, version: str) -> Path:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    normalized = version if version.startswith("v") else f"v{version}"
    path = run.subdir("coding") / f"patch.{normalized}.diff"
    if not path.exists():
        raise HTTPException(status_code=404, detail="patch not found")
    return path


def _extract_diff_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        candidate = parts[3]
        if candidate.startswith("b/"):
            candidate = candidate[2:]
        paths.append(candidate)
    return paths


async def _apply_patch_if_present(run_id: str, version: str) -> None:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    normalized = version if version.startswith("v") else f"v{version}"
    patch_path = run.subdir("coding") / f"patch.{normalized}.diff"
    if not patch_path.exists():
        return
    diff = patch_path.read_text(encoding="utf-8")
    result = await get_tool_registry().dispatch(
        "code.apply_patch",
        {
            "version": normalized,
            "patch_path": str(patch_path),
            "diff": diff,
            "files": [{"path": p} for p in _extract_diff_paths(diff)],
        },
        ToolContext(run_id=run_id, project=run.project, agent="coding"),
    )
    if not result.ok:
        raise HTTPException(
            status_code=409,
            detail={
                "error": result.error,
                "blocked_by_gate": result.blocked_by_gate,
            },
        )


@router.get("/{run_id}/coding/patch/{version}", response_model=PatchView)
async def get_patch(run_id: str, version: str) -> PatchView:
    path = _patch_path(run_id, version)
    normalized = version if version.startswith("v") else f"v{version}"
    approved = (path.parent / f"patch.{normalized}.approved.json").exists()
    return PatchView(
        run_id=run_id,
        version=normalized,
        path=str(path),
        text=path.read_text(encoding="utf-8"),
        approved=approved,
    )


@router.post("/{run_id}/coding/patch/{version}/approve", response_model=ArtifactView)
async def approve_patch(run_id: str, version: str) -> ArtifactView:
    await _apply_patch_if_present(run_id, version)
    normalized = version if version.startswith("v") else f"v{version}"
    review = get_review_registry().get(run_id, "coding")
    if review is not None:
        bus = get_event_bus()
        approved = await do_approve(session=review, bus=bus)
        return _read_view(run_id, "coding", "code_spec", approved.version)

    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    art_store = ArtifactStore(run)
    versions = art_store.list_versions(agent_dir="coding", stem="code_spec")
    base = next((v for v in versions if v.version == normalized), None)
    if base is None:
        raise HTTPException(status_code=404, detail="code_spec version not found")
    approved = art_store.approve(base)
    _sediment_after_inline_approve(run_id, "coding", approved)
    return _read_view(run_id, "coding", "code_spec", approved.version)


@router.post("/{run_id}/coding/patch/{version}/reject", status_code=202)
async def reject_patch(
    run_id: str, version: str, payload: RejectPayload
) -> dict[str, str]:
    _patch_path(run_id, version)
    review = get_review_registry().get(run_id, "coding")
    if review is None:
        return {"status": "rejected"}
    bus = get_event_bus()
    await do_reject(session=review, bus=bus, reason=payload.reason)
    return {"status": "rejected"}


@router.get("/{run_id}/{agent_dir}/{stem}/versions")
async def list_versions(
    run_id: str, agent_dir: str, stem: str
) -> list[dict[str, str]]:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    art_store = ArtifactStore(run)
    versions = art_store.list_versions(agent_dir=agent_dir, stem=stem)
    return [
        {
            "version": v.version,
            "path": str(v.path),
            "filename": v.filename,
        }
        for v in versions
    ]


@router.get("/{run_id}/{agent_dir}/{stem}/{version}", response_model=ArtifactView)
async def get_artifact(
    run_id: str, agent_dir: str, stem: str, version: str
) -> ArtifactView:
    return _read_view(run_id, agent_dir, stem, version)


@router.get("/{run_id}/{agent_dir}/{stem}/diff")
async def diff_versions(
    run_id: str, agent_dir: str, stem: str, from_: str = "v1", to: str = "v2"
) -> dict[str, str]:
    left = _resolve(run_id, agent_dir, stem, from_).read_text(encoding="utf-8")
    right = _resolve(run_id, agent_dir, stem, to).read_text(encoding="utf-8")
    return {"diff": unified(left, right, label_left=from_, label_right=to)}


@router.post("/{run_id}/{agent_dir}/{stem}/{version}/edit", response_model=ArtifactView)
async def edit_artifact(
    run_id: str, agent_dir: str, stem: str, version: str, payload: EditPayload
) -> ArtifactView:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    art_store = ArtifactStore(run)
    versions = art_store.list_versions(agent_dir=agent_dir, stem=stem)
    base = next((v for v in versions if v.version == version), None)
    if base is None:
        raise HTTPException(status_code=404, detail="version not found")
    new_ref, validation = apply_human_edit(
        art_store=art_store,
        base=base,
        body=payload.body,
        metadata_patch=payload.metadata_patch,
        expected_schema=str(base.path.read_text(encoding="utf-8").split("\n")[1].split(":", 1)[1].strip()) if base.path.exists() else None,
    )
    # update review session if any
    review = get_review_registry().get(run_id, agent_dir)
    if review is not None:
        review.record_edit(new_ref)
    return _read_view(run_id, agent_dir, stem, new_ref.version)


@router.post("/{run_id}/{agent_dir}/{stem}/{version}/approve", response_model=ArtifactView)
async def approve_artifact(
    run_id: str, agent_dir: str, stem: str, version: str
) -> ArtifactView:
    if agent_dir == "coding":
        await _apply_patch_if_present(run_id, version)
    review = get_review_registry().get(run_id, agent_dir)
    if review is None:
        # No active review; promote inline.
        store = get_run_store()
        run = store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        art_store = ArtifactStore(run)
        versions = art_store.list_versions(agent_dir=agent_dir, stem=stem)
        base = next((v for v in versions if v.version == version), None)
        if base is None:
            raise HTTPException(status_code=404, detail="version not found")
        approved = art_store.approve(base)
        _sediment_after_inline_approve(run_id, agent_dir, approved)
        return _read_view(run_id, agent_dir, stem, approved.version)
    bus = get_event_bus()
    approved = await do_approve(session=review, bus=bus)
    return _read_view(run_id, agent_dir, stem, approved.version)


@router.post("/{run_id}/{agent_dir}/{stem}/reject", status_code=202)
async def reject_artifact(
    run_id: str, agent_dir: str, stem: str, payload: RejectPayload
) -> dict[str, str]:
    review = get_review_registry().get(run_id, agent_dir)
    if review is None:
        raise HTTPException(status_code=404, detail="no review session")
    bus = get_event_bus()
    await do_reject(session=review, bus=bus, reason=payload.reason)
    return {"status": "rejected"}


@router.post("/{run_id}/{agent_dir}/{stem}/comment", status_code=202)
async def comment_artifact(
    run_id: str, agent_dir: str, stem: str, payload: CommentPayload
) -> dict[str, str]:
    review = get_review_registry().get(run_id, agent_dir)
    if review is None:
        raise HTTPException(status_code=404, detail="no review session")
    review.comment(payload.text)
    return {"status": "ok"}


@router.get("/{run_id}/audit")
async def audit_log(run_id: str) -> list[dict[str, Any]]:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    entries = read_audit(run.subdir("hitl") / "review_log.jsonl")
    return [
        {
            "run_id": e.run_id,
            "agent": e.agent,
            "action": e.action,
            "actor": e.actor,
            "timestamp": e.timestamp,
            "detail": e.detail,
        }
        for e in entries
    ]


@router.get("/{run_id}/pending")
async def pending_reviews(run_id: str) -> list[dict[str, Any]]:
    sessions = get_review_registry().list(run_id)
    if sessions:
        return [s.to_summary() for s in sessions]
    try:
        session = get_orchestrator().session(run_id)
    except KeyError:
        return []
    out: list[dict[str, Any]] = []
    for node_key, state in session.graph.all_states().items():
        if state.value != "waiting_review":
            continue
        agent = node_key.split("_attempt_", 1)[0]
        out.append(
            {
                "run_id": run_id,
                "agent": agent,
                "node": node_key,
                "artifact_path": "",
                "version": "",
                "decision": None,
            }
        )
    return out


@router.get("/{run_id}/{agent_dir}/debate")
async def get_debate_transcript(run_id: str, agent_dir: str) -> dict[str, Any]:
    """Return the latest debate transcript for an agent (if any)."""
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    candidate = run.subdir(agent_dir) / "debate_transcript.v1.md"
    if not candidate.exists():
        return {"exists": False, "agent": agent_dir, "text": ""}
    return {
        "exists": True,
        "agent": agent_dir,
        "path": str(candidate),
        "text": candidate.read_text(encoding="utf-8"),
    }
