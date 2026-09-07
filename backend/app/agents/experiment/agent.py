"""Experiment Agent — proposal → experiment_plan."""
from __future__ import annotations

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest


class ExperimentAgent(BaseAgent):
    name = "experiment"
    output_schema = "experiment_plan.v1"
    agent_brief = (
        "你负责把假设转化为可执行的实验方案。先用 knowledge.baseline_match 检查是否有"
        "可复用的历史 run,用 knowledge.experiment_memory 借鉴既有实验设计,再定义自变量/"
        "控制变量/因变量、主次指标、消融矩阵与 GPU 预算估计。能复用 baseline 时给出 "
        "reuse_decision=reuse。"
        "proposal 的 human_summary 仅用于快速概览，实际设计以 handoff、method_spec、"
        "signal_contract、参数预算和证据为规范；尊重已标记的上下文缺口与前置条件，"
        "不得猜测未提供的基线、数据或仿真结果。"
    )

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        return await self._draft_via_llm(request, context)
