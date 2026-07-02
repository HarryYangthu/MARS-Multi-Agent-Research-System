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
from app.hitl.approval import request_revision as do_request_revision
from app.hitl.audit_log import read as read_audit
from app.hitl.diff_view import unified
from app.hitl.review_session import get_registry as get_review_registry
from app.hitl.revision_loop import apply_human_edit
from app.harness.tools.registry import ToolContext
from app.harness.tools.registry import get_registry as get_tool_registry
from app.reporting import generate_report_bundle
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


class WorkspaceFileView(BaseModel):
    run_id: str
    agent_dir: str
    relative_path: str
    path: str
    exists: bool
    text: str
    size_bytes: int = 0
    content_type: str = "text/plain"


class WorkspaceTreeEntry(BaseModel):
    relative_path: str
    name: str
    kind: str
    path: str
    size_bytes: int = 0
    content_type: str = ""


class WorkspaceTreeView(BaseModel):
    run_id: str
    agent_dir: str
    root_path: str
    entries: list[WorkspaceTreeEntry]


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


def _resolve_agent_file(run_id: str, agent_dir: str, relative_path: str) -> Path:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    try:
        base = run.subdir(agent_dir).resolve()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rel = Path(relative_path)
    if rel.is_absolute() or not rel.parts or any(part == ".." for part in rel.parts):
        raise HTTPException(status_code=400, detail="path must stay inside agent workspace")
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="path must stay inside agent workspace")
    return target


def _content_type_for_path(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix in {".md", ".markdown"}:
        return "text/markdown"
    return "text/plain"


def _workspace_tree(run_id: str, agent_dir: str) -> WorkspaceTreeView:
    store = get_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    try:
        root = run.subdir(agent_dir).resolve()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not root.exists():
        return WorkspaceTreeView(
            run_id=run_id,
            agent_dir=agent_dir,
            root_path=str(root),
            entries=[],
        )
    entries: list[WorkspaceTreeEntry] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append(
                WorkspaceTreeEntry(
                    relative_path=rel,
                    name=path.name,
                    kind="directory",
                    path=str(path),
                )
            )
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entries.append(
            WorkspaceTreeEntry(
                relative_path=rel,
                name=path.name,
                kind="file",
                path=str(path),
                size_bytes=size,
                content_type=_content_type_for_path(path),
            )
        )
        if len(entries) >= 500:
            break
    return WorkspaceTreeView(
        run_id=run_id,
        agent_dir=agent_dir,
        root_path=str(root),
        entries=entries,
    )


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
    if agent_dir == "writing":
        try:
            generate_report_bundle(run, actor="inline_approve")
        except Exception as exc:  # pragma: no cover - approval response remains durable
            logger.warning(
                "inline-approved report bundle generation failed: run={} artifact={} error={}",
                run_id,
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
    resume = await get_orchestrator().resume_after_artifact_approval(
        run_id=run_id,
        agent="coding",
    )
    if not resume.get("ok"):
        logger.warning(
            "inline patch approval did not resume run: run={} status={}",
            run_id,
            resume,
        )
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


@router.get("/{run_id}/{agent_dir}/workspace-file", response_model=WorkspaceFileView)
async def get_agent_workspace_file(
    run_id: str,
    agent_dir: str,
    path: str,
) -> WorkspaceFileView:
    """Read a text artifact from one agent's run workspace."""
    target = _resolve_agent_file(run_id, agent_dir, path)
    if not target.exists():
        return WorkspaceFileView(
            run_id=run_id,
            agent_dir=agent_dir,
            relative_path=path,
            path=str(target),
            exists=False,
            text="",
            content_type=_content_type_for_path(target),
        )
    if not target.is_file():
        raise HTTPException(status_code=400, detail="path is not a file")
    size = target.stat().st_size
    if size > 750_000:
        raise HTTPException(status_code=413, detail="workspace file is too large")
    return WorkspaceFileView(
        run_id=run_id,
        agent_dir=agent_dir,
        relative_path=path,
        path=str(target),
        exists=True,
        text=target.read_text(encoding="utf-8", errors="replace"),
        size_bytes=size,
        content_type=_content_type_for_path(target),
    )


@router.get("/{run_id}/{agent_dir}/workspace-tree", response_model=WorkspaceTreeView)
async def get_agent_workspace_tree(
    run_id: str,
    agent_dir: str,
) -> WorkspaceTreeView:
    """List one agent's run workspace as a folder tree source."""
    return _workspace_tree(run_id, agent_dir)


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
        resume = await get_orchestrator().resume_after_artifact_approval(
            run_id=run_id,
            agent=agent_dir,
        )
        if not resume.get("ok"):
            logger.warning(
                "inline approval did not resume run: run={} agent={} status={}",
                run_id,
                agent_dir,
                resume,
            )
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
        result = await get_orchestrator().request_artifact_revision(
            run_id=run_id,
            agent=agent_dir,
            reason=payload.reason,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result)
        return {
            "status": str(result.get("status") or "revision_started"),
            "node": str(result.get("node") or ""),
        }
    bus = get_event_bus()
    await do_request_revision(session=review, bus=bus, reason=payload.reason)
    return {"status": "revision_requested", "node": agent_dir}


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
