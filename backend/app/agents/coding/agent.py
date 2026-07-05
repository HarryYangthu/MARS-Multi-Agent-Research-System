"""Coding Agent — experiment_plan → code_spec + patch."""
from __future__ import annotations

import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.coding.opencode_adapter import OpenCodeAdapter
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.openai_provider import CustomEndpointProvider, LocalVllmProvider
from app.harness.llm.post_training_loader import PostTrainingHandle, load_handle
from app.harness.llm.provider_base import LLMConfig, LLMProvider
from app.settings import env_or_local, get_settings


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
        settings = get_settings()
        if (
            settings.mars_coding_backend == "opencode"
            and settings.mars_mock_mode != "always"
        ):
            adapter = OpenCodeAdapter()
            if not adapter.is_available():
                if settings.is_production:
                    raise RuntimeError(
                        "MARS_CODING_BACKEND=opencode but opencode is not installed"
                    )
                message = (
                    "MARS_CODING_BACKEND=opencode, but opencode is not installed. "
                    "Falling back to the governed native LLM Coding Agent; no mock "
                    "coding artifact is generated."
                )
                logger.warning(message)
                _write_backend_note(request, message)
                context.upstream["coding_backend_fallback"] = message
                context.metadata["coding_backend_fallback"] = "opencode_unavailable"
                return await self._draft_via_llm(request, context)

            result = await adapter.run(request, context)
            acceptable_statuses = {"completed", "completed_with_warnings"}
            if result.status not in acceptable_statuses:
                message = (
                    f"OpenCode backend failed with status={result.status}; "
                    f"transcript={result.transcript_path}; error={result.error[:500]}"
                )
                raise RuntimeError(message)
            files_changed = result.files_changed
            metadata: dict[str, Any] = {
                "schema": "code_spec.v1",
                "project": request.project,
                "agent": "coding",
                "upstream_artifact": _first_upstream_ref(request.upstream_artifacts),
                "target_lang": "python",
                "baseline_compat": {
                    "preserved": True,
                    "rationale": "External coding backend is sandboxed by a MARS task packet; patches still require code.apply_patch through ToolRegistry Gate 5.",
                },
                "files_changed": files_changed,
                "new_dependencies": [],
                "test_coverage": {
                    "unit_tests_added": 0,
                    "baseline_smoke_test": "skipped",
                },
                "coding_backend": {
                    "name": result.backend,
                    "status": result.status,
                    "task_packet": result.task_packet_path,
                    "transcript": result.transcript_path,
                    "diff": result.diff_path,
                    "checks": result.checks,
                    "diff_stats": result.diff_stats,
                    "error": result.error,
                },
            }
            if result.status == "completed_with_warnings":
                metadata["quality_warnings"] = [
                    "OpenCode changed files but did not finish cleanly; inspect transcript and patch before approval."
                ]
            stats = result.diff_stats
            changed = int(stats.get("files_changed", len(files_changed)) or 0)
            insertions = int(stats.get("insertions", 0) or 0)
            deletions = int(stats.get("deletions", 0) or 0)
            body = (
                "# Code Spec\n\n"
                f"- Backend: {result.backend}\n"
                f"- Status: {result.status}\n"
                f"- Code changes: {changed} files changed, +{insertions} -{deletions}\n"
                f"- Task packet: `{result.task_packet_path}`\n"
                f"- Transcript: `{result.transcript_path}`\n"
                f"- Diff: `{result.diff_path or 'none'}`\n\n"
                "Patch application remains blocked until HITL approval and "
                "`code.apply_patch` dispatch.\n"
            )
            return Artifact(
                text=fm_dumps(metadata, body),
                schema_id=self.output_schema,
                metadata=metadata,
                body=body,
            )
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
            if settings.is_production:
                raise RuntimeError("production mode cannot use MARS_MOCK_MODE=always")
            logger.info(
                "MARS_MOCK_MODE=always — coding post-training endpoint uses mock"
            )
            return MockProvider(default_schema=self.output_schema), cfg

        try:
            api_key = env_or_local(handle.api_key_env or "")
            endpoint = handle.custom_endpoint or ""
            if handle.endpoint_provider == "local_vllm":
                if not _endpoint_reachable(endpoint):
                    if settings.is_production:
                        raise RuntimeError(
                            f"coding post-training endpoint is unreachable: {endpoint}"
                        )
                    logger.warning(
                        "coding post-training endpoint {} is unreachable; "
                        "falling back to configured provider {}",
                        endpoint,
                        self._config.model_provider,
                    )
                    return super()._select_provider()
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
            if settings.is_production or settings.mars_mock_mode == "never":
                raise RuntimeError(
                    "coding post-training provider failed to initialize"
                ) from exc
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


def _first_upstream_ref(upstream: dict[str, str]) -> str:
    if not upstream:
        return ""
    return next(iter(upstream.keys()))


def _endpoint_reachable(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _write_backend_note(request: RunRequest, message: str) -> None:
    agent_dir = str(request.extra.get("agent_dir") or "")
    run_root = str(request.extra.get("run_root") or "")
    if agent_dir:
        target_dir = Path(agent_dir)
    elif run_root:
        target_dir = Path(run_root) / "coding"
    else:
        return
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "coding_backend_fallback.md").write_text(
            "# Coding backend fallback\n\n"
            f"{message}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("failed to write coding backend fallback note: {}", exc)
