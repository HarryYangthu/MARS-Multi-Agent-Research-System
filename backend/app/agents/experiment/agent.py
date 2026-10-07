"""Experiment Agent — proposal → experiment_plan."""
from __future__ import annotations

from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.schema.experiment_contract import document_metadata, experiment_errors


class ExperimentAgent(BaseAgent):
    name = "experiment"
    project_knowledge_enabled = True
    output_schema = "experiment_plan.v1"
    agent_brief = (
        "你负责把假设转化为可执行的实验方案。先用 knowledge.baseline_match 检查是否有"
        "可复用的历史 run,用 knowledge.experiment_memory 借鉴既有实验设计,再定义自变量/"
        "控制变量/因变量、主次指标、消融矩阵与 GPU 预算估计。能复用 baseline 时给出 "
        "reuse_decision=reuse。"
        "proposal 的 human_summary 仅用于快速概览，实际设计以 handoff、method_spec、"
        "signal_contract、参数预算和证据为规范；尊重已标记的上下文缺口与前置条件，"
        "不得猜测未提供的基线、数据或仿真结果。"
        "每组 config 必须给出从证据解析的非负整数 seed；steps 预算使用正整数 budget_steps，"
        "epochs 预算使用 budget_unit=epochs 与正整数 max_iters。不得写同基线种子等占位描述，"
        "不得混用步数和轮数；estimated_runs 必须与矩阵数量一致。"
        "cfg/config_path 只写实际文件路径，不拼接解释；解释放 role。产物文件名逐字核对源码，"
        "不得删减 json/jsonl 等扩展名或猜测运行目录。"
    )

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if not errors:
            errors.extend(experiment_errors(document_metadata(text)))
        return errors

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        return await self._draft_via_llm(request, context)
