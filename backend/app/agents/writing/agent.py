"""Writing Agent — full chain → research report."""
from __future__ import annotations

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.debate.debate_runner import run_debate


class WritingAgent(BaseAgent):
    name = "writing"
    output_schema = "report.v1"
    agent_brief = (
        "你负责把完整研究链路汇总成研究报告。用 knowledge.run_archive 回顾执行结果、"
        "knowledge.methodology 对齐写作规范,串联 proposal/plan/code/runs 的 chain_refs,"
        "面向 phd_advisor 给出可复现、有据可依的中文报告。"
    )

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
            artifact.metadata.setdefault("debate_summary", {})[
                "reviewer_critiques"
            ] = [t.text[:200] for t in result.turns if t.role != "judge"][:5]
            artifact.metadata["debate_mode"] = result.mode.value
            artifact.metadata["debate_transcript_full"] = result.transcript_md
            return artifact
        return await self._draft_via_llm(request, context)
