"""Execution Agent — code_spec → run_log.

In Phase 3 this agent only validates / serializes the run_log shape via the
LLM (or mock). Phase 6 wires in the real ``execution/simulation_runner.py``
and the multi-experiment WS plumbing.
"""
from __future__ import annotations

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest


class ExecutionAgent(BaseAgent):
    name = "execution"
    output_schema = "run_log.v1"

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        return await self._draft_via_llm(request, context)
