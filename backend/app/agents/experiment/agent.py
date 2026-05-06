"""Experiment Agent — proposal → experiment_plan."""
from __future__ import annotations

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest


class ExperimentAgent(BaseAgent):
    name = "experiment"
    output_schema = "experiment_plan.v1"

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        return await self._draft_via_llm(request, context)
