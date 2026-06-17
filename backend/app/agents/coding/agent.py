"""Coding Agent — experiment_plan → code_spec + patch."""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from loguru import logger

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.openai_provider import CustomEndpointProvider, LocalVllmProvider
from app.harness.llm.post_training_loader import PostTrainingHandle, load_handle
from app.harness.llm.provider_base import LLMConfig, LLMProvider
from app.settings import get_settings


class CodingAgent(BaseAgent):
    name = "coding"
    output_schema = "code_spec.v1"
    agent_brief = (
        "你负责把实验方案转化为代码规格 (code_spec) 与补丁。先用 code.repo_reader 阅读"
        "待改文件、knowledge.code_assets 复用既有实现,严格保持 baseline 接口兼容"
        "(baseline_compat.preserved=true,违反会触发 Gate 5)。产物列出 files_changed、"
        "new_dependencies 与测试覆盖,diff 在正文给出,审核后由 code.apply_patch 落地。"
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Eagerly resolve post_training so configuration errors surface early.
        pt_raw = self._config.raw.get("post_training", {}) or {}
        self._post_training = load_handle(self._normalize_post_training(pt_raw))

    @property
    def post_training_handle(self) -> PostTrainingHandle:
        return self._post_training

    def load_post_training(
        self, config: Mapping[str, object]
    ) -> PostTrainingHandle:
        self._post_training = load_handle(config, source="runtime")
        return self._post_training

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        return await self._draft_via_llm(request, context)

    def _select_provider(self) -> tuple[LLMProvider, LLMConfig]:
        handle = self._post_training
        if not handle.enabled or handle.mode != "endpoint":
            return super()._select_provider()

        cfg = LLMConfig(
            provider=handle.endpoint_provider,
            model=handle.model or self._config.model_name,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            response_schema=self.output_schema,
            extra={
                "post_training": {
                    "enabled": True,
                    "mode": handle.mode,
                    "source": handle.source,
                }
            },
        )

        settings = get_settings()
        if settings.mars_mock_mode == "always":
            logger.info(
                "MARS_MOCK_MODE=always — coding post-training endpoint uses mock"
            )
            return MockProvider(default_schema=self.output_schema), cfg

        try:
            api_key = os.environ.get(handle.api_key_env or "", "")
            endpoint = handle.custom_endpoint or ""
            if handle.endpoint_provider == "local_vllm":
                return (
                    LocalVllmProvider(
                        base_url=endpoint,
                        api_key=api_key or "EMPTY",
                    ),
                    cfg,
                )
            return (
                CustomEndpointProvider(api_key=api_key, base_url=endpoint),
                cfg,
            )
        except Exception as exc:
            logger.warning(
                "coding post-training provider failed to load ({}); falling back to mock",
                exc,
            )
            return MockProvider(default_schema=self.output_schema), cfg

    @staticmethod
    def _normalize_post_training(raw: object) -> Mapping[str, object]:
        if not isinstance(raw, Mapping):
            return {}
        return {str(k): v for k, v in raw.items()}
