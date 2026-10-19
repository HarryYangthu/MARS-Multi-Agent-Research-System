"""Writing Agent — full chain → research report."""
from __future__ import annotations

from collections.abc import MutableMapping
import json
from pathlib import Path
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.debate.debate_runner import run_debate
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter


class WritingAgent(BaseAgent):
    name = "writing"
    project_knowledge_enabled = True
    output_schema = "report.v1"
    agent_brief = (
        "你负责把完整研究链路汇总成研究报告。用 knowledge.run_archive 回顾执行结果、"
        "knowledge.methodology 对齐写作规范,串联 proposal/plan/code/runs 的 chain_refs,"
        "面向 phd_advisor 给出可复现、有据可依的中文报告。"
        "先核对 admitted_report_sources 提供的实际路径；跳过的研究或实验设计阶段不能编造 proposal/plan。"
        "上下文材料标签不是文件路径，不得猜测作业哈希；保留完整路径、目录与后缀。"
        "执行证据的 source 是当前 run 的引用路径；按其 reader/material_ref 用 context.read_material 读取，"
        "不能把 run 路径交给 code.repo_reader。代码仓读取失败不能据此声称执行证据不存在。"
        "解释指标公式、改善方向、优化器实际类型或学习率调度单位时，按需用 code.repo_reader 读取本任务实际代码仓中"
        "相关指标函数、优化器或训练入口，并与本次配置、执行收据及原生记录交叉核对。"
        "不得凭指标缩写猜测优劣方向，不得把优化器更新数与 epoch 混用，也不得从观测点数量推导调度单位；"
        "没有核对到实现或证据时，只报告实际测量值并明确语义尚未核实。差值与比例按原始精度计算后再格式化。"
        "配置字符串或缩写不能当作真实实现类名；未读实现时仅引用字段原值，不自行扩写。"
        "批大小对照需核对实际训练更新次数，以及同一设置是否同时改变评价或推理分块；"
        "若共同变化，披露归因局限，不能把结果差异只归因于训练批大小，也不能在没有代码依据时声称评价条件固定。"
    )

    async def build_context(self, request: RunRequest) -> ContextPack:
        context = await super().build_context(request)
        if request.extra.get('run_root'):
            from app.harness.reporting.source_refs import report_source_catalogue
            context.upstream['admitted_report_sources'] = json.dumps(
                report_source_catalogue(Path(str(request.extra['run_root']))), ensure_ascii=False)
            from app.harness.reporting.execution_evidence import execution_job_evidence
            context.upstream['execution.job_files'] = execution_job_evidence(
                Path(str(request.extra['run_root'])), str(request.extra.get('run_id') or Path(str(request.extra['run_root'])).name),
                request.project, admit_materials=True)
        return context

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if not errors:
            errors.extend(_review_metadata_errors(parse_frontmatter(text).metadata,
                                                  debate_enabled=self._config.debate_enabled))
        if not errors and request.extra.get("run_root"):
            from app.harness.reporting.source_refs import report_source_errors
            errors.extend(report_source_errors(Path(str(request.extra["run_root"])), text))
        return errors

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


def _review_metadata_errors(metadata: dict[str, Any], *, debate_enabled: bool) -> list[str]:
    """Model-written review labels cannot create a debate that was disabled."""
    if debate_enabled:
        return []
    summary = metadata.get('debate_summary', {})
    claimed = isinstance(summary, dict) and (summary.get('rounds', 0) != 0 or bool(summary.get('reviewer_critiques')))
    if claimed or metadata.get('debate_mode') or metadata.get('debate_transcript_ref'):
        return ['debate_summary: this invocation has debate disabled; retries and revision feedback '
                'cannot be presented as debate rounds or reviewer critiques']
    return []


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
