"""Agent runtime configuration endpoints."""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_run_store
from app.bridge.agent_registry import get_registry
from app.harness.llm.post_training_loader import PostTrainingHandle
from app.storage.agent_context_store import (
    AgentContextFile,
    AgentResearchSite,
    SUPPORTED_AGENTS,
    create_agent_context_file,
    delete_agent_context_file,
    delete_agent_context_memory,
    list_agent_context_files,
    load_agent_research_sites,
    save_agent_research_sites,
    sync_agent_context_file_to_memory,
    update_agent_context_file,
)
from app.storage.coding_workspace_store import (
    CodeFileContent,
    CodeSource,
    CodeTreeItem,
    CodingMemoryItem,
    UpstreamContextItem,
    build_coding_workspace,
    read_code_file,
    save_coding_memory_items,
)
from app.storage.self_evolution_store import approve_memory_candidate
from app.storage.self_evolution_store import mark_memory_candidate_stale
from app.storage.self_evolution_store import reject_memory_candidate
from app.storage.self_evolution_store import supersede_memory_candidate

router = APIRouter(prefix="/api/agents", tags=["agents"])


class LoadPostTrainingPayload(BaseModel):
    enabled: bool = True
    mode: Literal["endpoint"] = "endpoint"
    endpoint_provider: Literal["local_vllm", "custom"] = "local_vllm"
    custom_endpoint: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    api_key_env: str = "LOCAL_VLLM_API_KEY"


class PostTrainingStatus(BaseModel):
    agent: str
    enabled: bool
    mode: str
    provider: str | None
    model: str | None
    endpoint: str | None
    source: str
    warnings: list[str]


class AgentContextFileView(BaseModel):
    agent: str
    path: str
    category: str
    source: str
    editable: bool
    deletable: bool
    size_chars: int
    content: str


class AgentResearchSiteView(BaseModel):
    id: str
    label: str
    url: str
    enabled: bool = True
    source: str = "custom"


class AgentContextView(BaseModel):
    agent: str
    files: list[AgentContextFileView]
    research_sites: list[AgentResearchSiteView]
    defaults: dict[str, Any]


class CreateContextItemPayload(BaseModel):
    category: str = Field(default="uploads/docs", min_length=1)
    filename: str = Field(..., min_length=1)
    content: str = ""


class UpdateContextItemPayload(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = ""


class DeleteContextItemPayload(BaseModel):
    path: str = Field(..., min_length=1)


class UpdateResearchSitesPayload(BaseModel):
    sites: list[AgentResearchSiteView]


class CodeSourceView(BaseModel):
    id: str
    label: str
    path: str
    exists: bool
    read_only: bool
    kind: str


class CodeTreeItemView(BaseModel):
    path: str
    name: str
    kind: str
    depth: int
    size_chars: int
    language: str


class CodeFileContentView(BaseModel):
    source_id: str
    path: str
    language: str
    size_chars: int
    truncated: bool
    content: str


class UpstreamContextItemView(BaseModel):
    id: str
    agent: str
    title: str
    path: str
    kind: str
    content: str


class CodingMemoryItemView(BaseModel):
    id: str
    label: str
    text: str
    enabled: bool = True
    source: str = "custom"
    editable: bool = True


class CodingWorkspaceView(BaseModel):
    project: str
    selected_source: str
    sources: list[CodeSourceView]
    files: list[CodeTreeItemView]
    upstream_context: list[UpstreamContextItemView]
    memory_items: list[CodingMemoryItemView]
    kb_memory_items: list[CodingMemoryItemView]


class UpdateCodingMemoryPayload(BaseModel):
    items: list[CodingMemoryItemView]


class MemoryPromotionView(BaseModel):
    candidate_id: str
    agent: str
    memory_id: str
    status: str


class MemoryCandidateDecisionPayload(BaseModel):
    reviewer_note: str = ""
    superseded_by: str = ""


def _coding_agent() -> Any:
    reg = get_registry()
    if not reg.has("coding"):
        raise HTTPException(status_code=503, detail="coding agent is not registered")
    return reg.get("coding")


def _ensure_context_agent(agent: str) -> None:
    if agent not in SUPPORTED_AGENTS:
        raise HTTPException(
            status_code=404,
            detail=f"unsupported agent context '{agent}'",
        )


def _file_view(item: AgentContextFile) -> AgentContextFileView:
    return AgentContextFileView(
        agent=item.agent,
        path=item.path,
        category=item.category,
        source=item.source,
        editable=item.editable,
        deletable=item.deletable,
        size_chars=item.size_chars,
        content=item.content,
    )


def _site_view(site: AgentResearchSite) -> AgentResearchSiteView:
    return AgentResearchSiteView(
        id=site.id,
        label=site.label,
        url=site.url,
        enabled=site.enabled,
        source=site.source,
    )


def _code_source_view(source: CodeSource) -> CodeSourceView:
    return CodeSourceView(
        id=source.id,
        label=source.label,
        path=source.path,
        exists=source.exists,
        read_only=source.read_only,
        kind=source.kind,
    )


def _code_tree_item_view(item: CodeTreeItem) -> CodeTreeItemView:
    return CodeTreeItemView(
        path=item.path,
        name=item.name,
        kind=item.kind,
        depth=item.depth,
        size_chars=item.size_chars,
        language=item.language,
    )


def _code_file_content_view(item: CodeFileContent) -> CodeFileContentView:
    return CodeFileContentView(
        source_id=item.source_id,
        path=item.path,
        language=item.language,
        size_chars=item.size_chars,
        truncated=item.truncated,
        content=item.content,
    )


def _upstream_context_view(item: UpstreamContextItem) -> UpstreamContextItemView:
    return UpstreamContextItemView(
        id=item.id,
        agent=item.agent,
        title=item.title,
        path=item.path,
        kind=item.kind,
        content=item.content,
    )


def _coding_memory_view(item: CodingMemoryItem) -> CodingMemoryItemView:
    return CodingMemoryItemView(
        id=item.id,
        label=item.label,
        text=item.text,
        enabled=item.enabled,
        source=item.source,
        editable=item.editable,
    )


def _run_root(run_id: str | None) -> Path | None:
    if not run_id:
        return None
    run = get_run_store().get(run_id)
    if run is not None:
        return run.root
    return None


def _handle(agent: Any) -> PostTrainingHandle:
    handle = getattr(agent, "post_training_handle", None)
    if not isinstance(handle, PostTrainingHandle):
        raise HTTPException(
            status_code=500,
            detail="coding agent does not expose post_training_handle",
        )
    return handle


@router.get("/{agent}/context", response_model=AgentContextView)
async def get_agent_context(agent: str) -> AgentContextView:
    _ensure_context_agent(agent)
    try:
        files = list_agent_context_files(agent)
        sites = load_agent_research_sites(agent)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentContextView(
        agent=agent,
        files=[_file_view(item) for item in files],
        research_sites=[_site_view(site) for site in sites],
        defaults={
            "editable_categories": [
                "docs",
                "prompts",
                "examples",
                "evals",
                "uploads/docs",
                "uploads/code",
            ],
            "read_only_sources": ["runtime_code"],
        },
    )


@router.post("/{agent}/context/items", response_model=AgentContextFileView)
async def create_context_item(
    agent: str,
    payload: CreateContextItemPayload,
    project: str = "moe-pimc",
) -> AgentContextFileView:
    _ensure_context_agent(agent)
    try:
        item = create_agent_context_file(
            agent,
            category=payload.category,
            filename=payload.filename,
            content=payload.content,
        )
        sync_agent_context_file_to_memory(agent, item, project=project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _file_view(item)


@router.put("/{agent}/context/items", response_model=AgentContextFileView)
async def update_context_item(
    agent: str,
    payload: UpdateContextItemPayload,
    project: str = "moe-pimc",
) -> AgentContextFileView:
    _ensure_context_agent(agent)
    try:
        item = update_agent_context_file(
            agent,
            path=payload.path,
            content=payload.content,
        )
        sync_agent_context_file_to_memory(agent, item, project=project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _file_view(item)


@router.delete("/{agent}/context/items", status_code=202)
async def delete_context_item(
    agent: str,
    payload: DeleteContextItemPayload,
) -> dict[str, str]:
    _ensure_context_agent(agent)
    try:
        delete_agent_context_file(agent, path=payload.path)
        delete_agent_context_memory(agent, path=payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "deleted", "path": payload.path}


@router.put("/{agent}/context/research-sites", response_model=list[AgentResearchSiteView])
async def update_research_sites(
    agent: str,
    payload: UpdateResearchSitesPayload,
) -> list[AgentResearchSiteView]:
    _ensure_context_agent(agent)
    try:
        saved = save_agent_research_sites(
            agent,
            [site.model_dump() for site in payload.sites],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [_site_view(site) for site in saved]


@router.get("/coding/workspace", response_model=CodingWorkspaceView)
async def get_coding_workspace(
    project: str = "moe-pimc",
    source: str = "auto",
    run_id: str | None = None,
) -> CodingWorkspaceView:
    try:
        workspace = build_coding_workspace(
            project=project,
            source=source,
            run_root=_run_root(run_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CodingWorkspaceView(
        project=workspace.project,
        selected_source=workspace.selected_source,
        sources=[_code_source_view(item) for item in workspace.sources],
        files=[_code_tree_item_view(item) for item in workspace.files],
        upstream_context=[
            _upstream_context_view(item) for item in workspace.upstream_context
        ],
        memory_items=[_coding_memory_view(item) for item in workspace.memory_items],
        kb_memory_items=[
            _coding_memory_view(item) for item in workspace.kb_memory_items
        ],
    )


@router.get("/coding/workspace/file", response_model=CodeFileContentView)
async def get_coding_workspace_file(
    project: str = "moe-pimc",
    source: str = "auto",
    path: str = "",
) -> CodeFileContentView:
    try:
        item = read_code_file(project=project, source=source, path=path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _code_file_content_view(item)


@router.put("/coding/workspace/memory", response_model=list[CodingMemoryItemView])
async def update_coding_workspace_memory(
    payload: UpdateCodingMemoryPayload,
) -> list[CodingMemoryItemView]:
    saved = save_coding_memory_items(
        [
            {
                "id": item.id,
                "label": item.label,
                "text": item.text,
                "enabled": item.enabled,
                "source": item.source,
            }
            for item in payload.items
        ]
    )
    return [_coding_memory_view(item) for item in saved]


@router.post(
    "/memory-candidates/{run_id}/{candidate_id}/approve",
    response_model=MemoryPromotionView,
)
async def approve_agent_memory_candidate(
    run_id: str,
    candidate_id: str,
) -> MemoryPromotionView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        result = approve_memory_candidate(run=run, candidate_id=candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MemoryPromotionView(**result)


@router.post(
    "/memory-candidates/{run_id}/{candidate_id}/reject",
    response_model=MemoryPromotionView,
)
async def reject_agent_memory_candidate(
    run_id: str,
    candidate_id: str,
    payload: MemoryCandidateDecisionPayload | None = None,
) -> MemoryPromotionView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        result = reject_memory_candidate(
            run=run,
            candidate_id=candidate_id,
            reviewer_note=payload.reviewer_note if payload is not None else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MemoryPromotionView(**result)


@router.post(
    "/memory-candidates/{run_id}/{candidate_id}/stale",
    response_model=MemoryPromotionView,
)
async def stale_agent_memory_candidate(
    run_id: str,
    candidate_id: str,
    payload: MemoryCandidateDecisionPayload | None = None,
) -> MemoryPromotionView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        result = mark_memory_candidate_stale(
            run=run,
            candidate_id=candidate_id,
            reviewer_note=payload.reviewer_note if payload is not None else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MemoryPromotionView(**result)


@router.post(
    "/memory-candidates/{run_id}/{candidate_id}/supersede",
    response_model=MemoryPromotionView,
)
async def supersede_agent_memory_candidate(
    run_id: str,
    candidate_id: str,
    payload: MemoryCandidateDecisionPayload | None = None,
) -> MemoryPromotionView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        result = supersede_memory_candidate(
            run=run,
            candidate_id=candidate_id,
            reviewer_note=payload.reviewer_note if payload is not None else "",
            superseded_by=payload.superseded_by if payload is not None else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MemoryPromotionView(**result)


def _warnings(handle: PostTrainingHandle) -> list[str]:
    warnings: list[str] = []
    if not handle.enabled:
        warnings.append("post_training disabled; Coding Agent uses configured model")
    if handle.enabled and handle.mode != "endpoint":
        warnings.append(f"mode={handle.mode} is load-only metadata in V0")
    if (
        handle.enabled
        and handle.mode == "endpoint"
        and handle.endpoint_provider == "custom"
        and not os.environ.get(handle.api_key_env or "")
    ):
        warnings.append(
            f"api_key_env={handle.api_key_env} is not set; provider will fall back to mock"
        )
    return warnings


def _status(handle: PostTrainingHandle) -> PostTrainingStatus:
    provider = (
        handle.endpoint_provider if handle.enabled and handle.mode == "endpoint" else None
    )
    endpoint = (
        handle.custom_endpoint if handle.enabled and handle.mode == "endpoint" else None
    )
    model = handle.model if handle.enabled and handle.mode == "endpoint" else None
    return PostTrainingStatus(
        agent="coding",
        enabled=handle.enabled,
        mode=handle.mode,
        provider=provider,
        model=model,
        endpoint=endpoint,
        source=handle.source,
        warnings=_warnings(handle),
    )


@router.get("/coding/post-training", response_model=PostTrainingStatus)
async def get_coding_post_training() -> PostTrainingStatus:
    return _status(_handle(_coding_agent()))


@router.post("/coding/post-training/load", response_model=PostTrainingStatus)
async def load_coding_post_training(
    payload: LoadPostTrainingPayload,
) -> PostTrainingStatus:
    agent = _coding_agent()
    loader = getattr(agent, "load_post_training", None)
    if not callable(loader):
        raise HTTPException(
            status_code=500,
            detail="coding agent does not support runtime post-training load",
        )
    load_fn = cast(Callable[[Mapping[str, object]], PostTrainingHandle], loader)
    try:
        handle = load_fn(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not isinstance(handle, PostTrainingHandle):
        raise HTTPException(status_code=500, detail="invalid post-training handle")
    return _status(handle)
