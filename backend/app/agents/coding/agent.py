"""Coding Agent — experiment_plan → code_spec + patch."""
from __future__ import annotations

from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.llm.post_training_loader import load_handle


class CodingAgent(BaseAgent):
    name = "coding"
    output_schema = "code_spec.v1"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Eagerly resolve post_training so configuration errors surface early.
        pt_raw = self._config.raw.get("post_training", {}) or {}
        # post_training fields are str|bool, but YAML may give us other types
        # — coerce defensively.
        normalized: dict[str, str | bool] = {}
        for k, v in pt_raw.items():
            if isinstance(v, bool):
                normalized[str(k)] = v
            else:
                normalized[str(k)] = str(v) if v is not None else ""
        self._post_training = load_handle(normalized)

    @property
    def post_training_handle(self) -> object:
        return self._post_training

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        return await self._draft_via_llm(request, context)
