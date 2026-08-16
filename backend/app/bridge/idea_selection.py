"""Turn an audited Idea selection request into the authoritative proposal."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.bridge.agent_registry import AgentRegistry
from app.bridge.discovery_service import IdeaSelectionRequest
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RunStore


class IdeaSelectionError(RuntimeError):
    """The durable selection could not be materialized as proposal.v1."""


class IdeaSelectionCoordinator:
    def __init__(self, *, run_store: RunStore, registry: AgentRegistry) -> None:
        self.run_store = run_store
        self.registry = registry
        self._locks: dict[str, asyncio.Lock] = {}

    async def apply(self, selection: IdeaSelectionRequest) -> str:
        lock = self._locks.setdefault(selection.run_id, asyncio.Lock())
        async with lock:
            run = self.run_store.get(selection.run_id)
            if run is None:
                raise IdeaSelectionError(f"run not found: {selection.run_id}")
            existing = _selected_proposal_ref(run, selection.hypothesis_id)
            if existing:
                return existing

            selection_path = run.root / "idea" / "discovery" / "selection.v1.json"
            if selection_path.is_file():
                return _materialize_saved_selection(
                    run,
                    selection_path=selection_path,
                    hypothesis_id=selection.hypothesis_id,
                )
            if not self.registry.has("idea"):
                raise IdeaSelectionError("Idea Agent is not registered")

            agent: Any = self.registry.get("idea")
            request = _agent_request(run, selection)
            context = await agent.build_context(request)
            run_loop = getattr(agent, "run_loop", None)
            artifact = (
                await run_loop(request, context)
                if callable(run_loop)
                else await agent.draft(request, context)
            )
            validation = await agent.validate_output(artifact)
            if not validation.valid:
                raise IdeaSelectionError(
                    "selected hypothesis produced an invalid proposal: "
                    + (validation.first_error() or "unknown validation error")
                )
            ref = ArtifactStore(run).write(
                text=str(artifact.text),
                expected_schema="proposal.v1",
            )
            return ref.path.relative_to(run.root).as_posix()


def _agent_request(run: RunHandle, selection: IdeaSelectionRequest) -> Any:
    # Imported lazily to preserve the Bridge registry inversion boundary.
    from app.agents.base import RunRequest as AgentRunRequest

    request_path = run.subdir("input") / "user_request.md"
    user_request = (
        request_path.read_text(encoding="utf-8") if request_path.is_file() else ""
    )
    return AgentRunRequest(
        project=run.project,
        user_request=user_request,
        extra={
            "run_id": run.run_id,
            "run_root": str(run.root),
            "agent_dir": str(run.subdir("idea")),
            "node_key": "idea",
            "attempt": 1,
            "idea_mode": "deep",
            "idea_selected_hypothesis_id": selection.hypothesis_id,
            "idea_selection_actor": selection.actor,
            "idea_selection_reason": selection.reason,
        },
    )


def _materialize_saved_selection(
    run: RunHandle,
    *,
    selection_path: Path,
    hypothesis_id: str,
) -> str:
    try:
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdeaSelectionError(f"selection checkpoint is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("hypothesis_id") != hypothesis_id:
        raise IdeaSelectionError("another hypothesis has already been selected")
    metadata = payload.get("proposal_metadata")
    body = payload.get("proposal_body")
    if not isinstance(metadata, dict) or not isinstance(body, str):
        raise IdeaSelectionError("selection checkpoint has no proposal payload")
    ref = ArtifactStore(run).write(
        text=fm_dumps(metadata, body),
        expected_schema="proposal.v1",
    )
    return ref.path.relative_to(run.root).as_posix()


def _selected_proposal_ref(run: RunHandle, hypothesis_id: str) -> str:
    versions = ArtifactStore(run).list_versions(
        agent_dir="idea",
        stem="idea_proposal",
    )
    for ref in reversed([item for item in versions if item.version != "approved"]):
        try:
            metadata = parse_frontmatter(ref.path.read_text(encoding="utf-8")).metadata
        except (OSError, ValueError):
            continue
        summary = metadata.get("discovery_summary")
        if (
            isinstance(summary, dict)
            and summary.get("selected_hypothesis_id") == hypothesis_id
        ):
            return ref.path.relative_to(run.root).as_posix()
    return ""
