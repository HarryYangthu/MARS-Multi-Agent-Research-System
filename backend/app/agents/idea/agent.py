"""Idea Agent — research-question → hypothesis."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.debate.debate_runner import run_debate
from app.agents.idea.discovery import (
    CoScientistWorkflow,
    DeepDiscoveryConfig,
    DeterministicRoleBackend,
    DiscoveryRoleBackend,
    IdeaMode,
    LLMRoleBackend,
    RunLocalDiscoveryStore,
    build_discovery_context,
    discovery_root_for_request,
    resolve_idea_mode,
)
from app.agents.idea.research import (
    ResearchPack,
    augment_context_with_research,
    build_idea_context,
    gather_required_research_tools,
    load_idea_self_context,
    merge_quality_warnings,
    normalize_idea_metadata,
    prepare_research_pack,
    validate_idea_quality,
)
from app.harness.llm.provider_base import Message
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.storage.artifact_store import ArtifactRef
from app.storage.run_store import RunHandle


class IdeaAgent(BaseAgent):
    name = "idea"
    output_schema = "proposal.v1"
    agent_brief = (
        "你负责把研究问题转化为可验证的假设。先用 knowledge.kb_query / "
        "search.local_docs 检索相关文献与方法，用 code.repo_reader 了解 baseline "
        "代码现状,再提出有新意且可证伪的 hypothesis。产物需给出 research_question、"
        "hypothesis、novelty、theoretical_basis 与 constraints。"
    )

    async def build_context(self, request: RunRequest) -> ContextPack:
        base = await super().build_context(request)
        return build_idea_context(request, base)

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        self_context = load_idea_self_context()
        raw_research_config = self.config.raw.get("research")
        research_config: Mapping[str, Any] | None = (
            raw_research_config if isinstance(raw_research_config, Mapping) else None
        )
        provider, _ = self._select_provider()
        tool_observations = await gather_required_research_tools(
            request=request,
            research_config=research_config,
            real_provider=provider.name != "mock",
        )
        research_pack = prepare_research_pack(
            request=request,
            context=context,
            self_context=self_context,
            research_config=research_config,
            tool_observations=tool_observations,
        )
        context = augment_context_with_research(context, research_pack)

        idea_mode = resolve_idea_mode(request)
        if idea_mode is IdeaMode.DEEP:
            return await self._draft_deep_discovery(
                request=request,
                research_pack=research_pack,
                provider_name=provider.name,
            )

        if self._config.debate_enabled:
            result = await run_debate(
                agent_name=self.name,
                agent_config=self._config,
                request=request,
                context=context,
                output_schema=self.output_schema,
                progress_path=str(request.extra.get("debate_progress_path") or "")
                or None,
            )
            assert result.final_artifact is not None
            artifact = result.final_artifact
            artifact.metadata.setdefault("debate_summary", {})
            artifact.metadata["debate_summary"]["rounds"] = result.rounds
            artifact.metadata["debate_summary"]["consensus"] = result.consensus_summary()
            artifact.metadata["debate_summary"].setdefault(
                "disagreements",
                ["详见 debate_transcript；V2 将批判者分歧沉淀为结构化审查项。"],
            )
            artifact.metadata["debate_summary"].setdefault(
                "risks",
                ["证据不足或 baseline 兼容风险必须在 Experiment 阶段继续验证。"],
            )
            artifact.metadata["debate_summary"].setdefault(
                "evidence_gaps",
                ["外部网络调研默认关闭，缺口需在人工审核或后续联网调研中补足。"],
            )
            artifact.metadata["debate_mode"] = result.mode.value
            artifact.metadata["debate_transcript_excerpt"] = result.transcript_md[:1000]
            artifact.metadata["debate_transcript_path"] = "debate_transcript.v1.md"
            artifact.metadata["idea_mode"] = "fast"
            return self._finalize_artifact(artifact, research_pack)

        artifact = await self._draft_via_llm(request, context)
        artifact.metadata["idea_mode"] = "fast"
        return self._finalize_artifact(artifact, research_pack)

    async def _draft_deep_discovery(
        self,
        *,
        request: RunRequest,
        research_pack: ResearchPack,
        provider_name: str,
    ) -> Artifact:
        config = DeepDiscoveryConfig.from_request_extra(request.extra)
        evidence_refs = _research_evidence_refs(research_pack)
        constraints = _deep_constraints(request)
        discovery_context = build_discovery_context(
            request=request,
            evidence_refs=evidence_refs,
            constraints=constraints,
        )
        backend: DiscoveryRoleBackend
        if provider_name == "mock":
            backend = DeterministicRoleBackend()
        else:
            backend = LLMRoleBackend(self._complete_discovery_role)
        workflow = CoScientistWorkflow(
            backend=backend,
            config=config,
            store=RunLocalDiscoveryStore(discovery_root_for_request(request)),
        )
        state = await workflow.run(discovery_context)
        selected_id = str(
            request.extra.get("idea_selected_hypothesis_id")
            or request.extra.get("selected_hypothesis_id")
            or ""
        ).strip()
        if selected_id:
            actor = str(request.extra.get("idea_selection_actor") or "").strip()
            source: Literal["human", "auto"] = (
                "auto"
                if _as_bool(request.extra.get("idea_auto_select"))
                else "human"
            )
            _selected, artifact = workflow.select_hypothesis(
                state,
                hypothesis_id=selected_id,
                research_question=request.user_request,
                actor=actor,
                reason=str(request.extra.get("idea_selection_reason") or ""),
                source=source,
            )
        else:
            artifact = workflow.proposal_for_hitl(
                state,
                research_question=request.user_request,
            )
        return self._finalize_artifact(artifact, research_pack)

    async def _complete_discovery_role(self, role: str, prompt: str) -> str:
        completion = await self._call_llm(
            [
                Message(
                    role="system",
                    content=(
                        "You are the Idea Agent's internal Co-Scientist role. "
                        "Return only the requested structured JSON."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            debate_role=f"co_scientist_{role}",
        )
        return completion.text

    def _finalize_artifact(
        self,
        artifact: Artifact,
        research_pack: ResearchPack,
    ) -> Artifact:
        """Attach V2 research provenance and non-blocking quality warnings."""
        artifact.metadata = normalize_idea_metadata(artifact.metadata, research_pack)
        artifact.metadata.setdefault("research_artifacts", {})
        if isinstance(artifact.metadata["research_artifacts"], dict):
            artifact.metadata["research_artifacts"].update(
                {
                    "research_dir": (
                        str(research_pack.research_dir)
                        if research_pack.research_dir is not None
                        else ""
                    ),
                    "research_plan": "research/research_plan.v1.md",
                    "research_notes": "research/research_notes.v1.md",
                    "research_summary": "research/research_summary.v1.md",
                    "source_summaries": "research/source_summaries.v1.md",
                    "source_summaries_index": "research/source_summaries.v1.json",
                    "evidence_index": "research/evidence_index.v1.json",
                    "tool_results": "research/tool_results.v1.json",
                }
            )
        warnings = validate_idea_quality(
            artifact.metadata,
            evidence_index=research_pack.evidence_index,
        )
        if warnings:
            artifact.metadata["quality_warnings"] = merge_quality_warnings(
                artifact.metadata.get("quality_warnings"),
                warnings,
            )
        artifact.schema_id = str(artifact.metadata.get("schema", self.output_schema))
        artifact.text = fm_dumps(artifact.metadata, artifact.body)
        return artifact

    def write_acceptance_report(
        self,
        *,
        run: RunHandle,
        artifact_ref: ArtifactRef | None = None,
        node_key: str = "idea",
    ) -> Path:
        from app.agents.idea.acceptance import write_idea_acceptance_report

        return write_idea_acceptance_report(
            run=run,
            artifact_ref=artifact_ref,
            node_key=node_key,
        )


def _research_evidence_refs(research_pack: ResearchPack) -> tuple[str, ...]:
    raw_items = research_pack.evidence_index.get("items", [])
    if not isinstance(raw_items, list):
        return ("research/evidence_index.v1.json",)
    refs = tuple(
        ref
        for item in raw_items
        if isinstance(item, Mapping)
        if (ref := str(item.get("id") or item.get("ref") or "").strip())
    )
    return refs[:8] or ("research/evidence_index.v1.json",)


def _deep_constraints(request: RunRequest) -> tuple[str, ...]:
    raw = request.extra.get("idea_constraints")
    if isinstance(raw, (list, tuple)):
        values = tuple(value for item in raw if (value := str(item).strip()))
        if values:
            return values
    return (
        "遵守项目 AGENTS.md、冻结 baseline 与 evaluator",
        "保持现有公开接口和同预算比较协议",
        "候选排名不能替代真实实验结论",
    )


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
