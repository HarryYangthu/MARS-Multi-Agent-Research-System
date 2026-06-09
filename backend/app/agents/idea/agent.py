"""Idea Agent — research-question → hypothesis."""
from __future__ import annotations

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.debate.debate_runner import run_debate


_IDEA_DIRECTIVES = """\
作为 Idea Agent,你的 proposal 必须达到以下质量标准(对应 proposal.v1 的字段):
1. 文献接地:结合"knowledge base"中检索到的既有研究问题/方法,填好 related_literature
   (每条含 title,有来源就加 url);正文里点明你的想法与这些既有工作的关系。
2. 假设可证伪(关键):hypothesis 必须给出**可测量的预测**和**会证伪它的条件** ——
   写清指标与阈值(例如:"在 8L 下 FLOPs 降低 ≥30% 且 RES 下降 ≤1.5 dB;若 RES 下降 >1.5 dB
   则假设被证伪")。禁止只写笼统愿景。
3. 新颖性:novelty 要显式对比先验/检索到的工作,说清差异点与为何更优。
4. 补全 theoretical_basis(理论依据)与 constraints(约束/前提)。
"""


class IdeaAgent(BaseAgent):
    name = "idea"
    output_schema = "proposal.v1"
    kb_zones = ("literature", "methodology")
    quality_directives = _IDEA_DIRECTIVES

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
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
            artifact.metadata["debate_mode"] = result.mode.value
            artifact.metadata["debate_transcript_excerpt"] = result.transcript_md[:1000]
            artifact.metadata["debate_transcript_full"] = result.transcript_md
            return artifact
        return await self._draft_via_llm(request, context)
