"""Service-start Idea configuration; the ordinary IdeaAgent/CLI stays unchanged."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from app.agents.base import Artifact, ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.runtime_profile import (
    ResolvedIdeaProfile, bind_profile_snapshot, public_agent_configuration,
)
from app.harness.llm.model_registry import AgentConfig


class ServiceIdeaAgent(IdeaAgent):
    def __init__(self, *, profile: ResolvedIdeaProfile | None = None) -> None:
        self._service_profile = deepcopy(profile)
        super().__init__(agent_config=self._service_profile.lead if self._service_profile else None)

    @property
    def service_profile_snapshot(self) -> dict[str, Any] | None:
        return self._service_profile.snapshot() if self._service_profile else None

    def _bind_service_profile(self, request: RunRequest) -> dict[str, Any] | None:
        root = request.extra.get("run_root")
        if not root:
            if self._service_profile is not None:
                raise ValueError("experimental service Idea profile requires a persisted run_root")
            return None
        snapshot = bind_profile_snapshot(Path(str(root)), self._service_profile,
                                         resume=bool(request.extra.get("resume_invocation")))
        if snapshot is None:
            return None
        assert self._service_profile is not None
        marker = request.runtime.get("idea_service_profile_sha256")
        if marker is not None and marker != snapshot["configuration_sha256"]:
            raise ValueError("request is already bound to a different Idea runtime profile")
        if request.runtime.get("idea_research_session") is not None and marker is None:
            raise ValueError("research session existed before service profile binding")
        child = request.runtime.get("idea_research_config")
        if child is not None and (not isinstance(child, AgentConfig)
                or public_agent_configuration(child) != snapshot["configuration"]["child"]):
            raise ValueError("request researcher differs from the service-start Idea profile")
        if child is None:
            request.runtime["idea_research_config"] = deepcopy(self._service_profile.child)
        request.runtime["idea_service_profile_sha256"] = snapshot["configuration_sha256"]
        return snapshot

    async def build_context(self, request: RunRequest) -> ContextPack:
        snapshot = self._bind_service_profile(request)
        context = await super().build_context(request)
        if snapshot:
            context.metadata["idea_runtime_profile"] = {
                "profile_id": snapshot["profile_id"], "configuration_sha256": snapshot["configuration_sha256"],
                "status": snapshot["status"],
            }
        return context

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        self._bind_service_profile(request)
        return await super().draft(request, context)
