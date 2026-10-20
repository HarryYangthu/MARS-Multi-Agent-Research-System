"""Experiment Agent — proposal → experiment_plan."""
from __future__ import annotations

import json
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
        "先核对 configured_execution_delivery 中当前项目可用的登记命令、entrypoints 与 config_paths；"
        "派生配置的目标路径也必须匹配该命令允许的范围，不能先设计任意新目录再让编码迁移。"
        "目录名不能代替实际 model_name 作为模型身份。没有兼容命令或目标路径时明确列出能力缺口，"
        "不得自行扩大白名单、换模型、改数据或把未登记方案称为可执行；计划和预算仍须按原流程审批。"
    )

    async def build_context(self, request: RunRequest) -> ContextPack:
        context = await super().build_context(request)
        from app.harness.tools.execution.handoff import execution_delivery_context
        context.upstream['configured_execution_delivery'] = json.dumps(
            execution_delivery_context(), ensure_ascii=False)
        if request.extra.get('evolution'):
            context.upstream['current_iteration_delivery_rules'] = (
                'Auto Research: design only the CURRENT executable batch. Read evolution_task_context.current_iteration. '
                'Use exactly its baseline_names for controls and candidate_pattern for candidates. '
                'Future adaptation and verification are controller policy, never placeholder rows in ablations. '
                'If YAML fields evolve, candidate config_path must be the actual allowed candidate YAML. '
                'Do not add unconsumed candidate_config/lr_init overrides or copy to a different directory. '
                'The same candidate YAML will be changed by Coding between batches, then read by the real registered entrypoint. '
                'Do not freeze future parameter values into this batch. Keep only real execution bindings and the fixed training protocol in config.')
        return context

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if not errors:
            errors.extend(experiment_errors(document_metadata(text)))
        if request.extra.get('evolution'):
            from app.harness.evolution import EvolutionContract, evolution_plan_errors
            errors.extend(evolution_plan_errors(EvolutionContract.model_validate(request.extra['evolution']), document_metadata(text)))
        return errors

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        return await self._draft_via_llm(request, context)
