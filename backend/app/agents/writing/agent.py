"""Writing Agent — full chain → research report."""
from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.debate.debate_runner import run_debate
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter


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
            ] = [_turn_preview(t.text) for t in result.turns if t.role != "judge"][:5]
            artifact.metadata["debate_mode"] = result.mode.value
            artifact.metadata["debate_transcript_ref"] = "debate_transcript.vN.md"
            artifact.metadata.pop("debate_transcript_full", None)
            artifact.metadata.pop("debate_transcript_excerpt", None)
            _ensure_execution_refs(artifact.metadata, request.extra.get("run_root"))
            artifact.schema_id = str(artifact.metadata.get("schema", self.output_schema))
            artifact.text = fm_dumps(artifact.metadata, artifact.body)
            return artifact
        return await self._draft_via_llm(request, context)


def _turn_preview(text: str, *, limit: int = 240) -> str:
    """Keep frontmatter readable; full debate text is stored separately."""
    stripped = text.strip()
    try:
        stripped = parse_frontmatter(stripped).body.strip()
    except Exception:
        pass
    preview = " ".join(line.strip() for line in stripped.splitlines() if line.strip())
    return preview[:limit]


def _ensure_execution_refs(metadata: dict[str, Any], run_root_raw: object) -> None:
    """Make measured post-run evidence visible in report frontmatter."""
    chain_refs = metadata.setdefault("chain_refs", {})
    if not isinstance(chain_refs, MutableMapping):
        return
    raw_runs = chain_refs.get("runs")
    runs = [str(item) for item in raw_runs] if isinstance(raw_runs, list) else []
    if not run_root_raw:
        chain_refs["runs"] = runs
        return
    root = Path(str(run_root_raw))
    for ref in (
        "execution/run_log.approved.md",
        "execution/metrics.json",
        "execution/batch_summary.json",
        "execution/loss_curves_16.png",
        "diagnosis/diagnosis.v2.md",
    ):
        if not (root / ref).exists():
            continue
        if ref not in runs:
            runs.append(ref)
    chain_refs["runs"] = runs
