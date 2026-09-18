"""Compose concrete agents outside Bridge; CLI uses the same native tool loop."""
from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from loguru import logger

from app.agents.base import BaseAgent, RunRequest
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.agents.research_cli import (
    ResearchAnalysisAgent, ResearchCodingAgent, ResearchExperimentAgent, ResearchFinalReportAgent,
)


class CliAgents:
    def __init__(self, model: str, loop: dict[str, Any]) -> None:
        self.model, self.loop = model, loop

    async def invoke(self, stage: str, project: str, task: str, upstream: dict[str, str],
                     root: Path) -> str:
        agent: BaseAgent
        if stage == "research":
            agent = FocusedIdeaAgent()
            task += ("\n本次 CLI 执行契约：只新增 libs/research_candidate.py，提供 build_model(config) 工厂。"
                     "config 只有 channels=16、baseline 架构配置和 context；返回保持 complex64 [time,16] 的模型。"
                     "可以复用已有模型组件，但不能修改 tools/、configs/、libs/model.py 或评测器。"
                     "所有方法从固定种子重新初始化并使用相同更新预算；不得使用训练后的基线 checkpoint 或追加拟合预算。"
                     "如用基线权重分解初始化，仅可在工厂内构造未训练的基线并分解，明确接口包装、复数因子尺度与退化处理。")
        elif stage == "coding":
            agent = ResearchCodingAgent(self.model, self.loop)
        elif stage == "experiment":
            agent = ResearchExperimentAgent(self.model, self.loop)
        elif stage == "analysis":
            agent = ResearchAnalysisAgent(self.model, self.loop)
        elif stage == "final_report":
            agent = ResearchFinalReportAgent(self.model, self.loop)
        else:
            raise ValueError("Unknown CLI research stage")
        logger.info("{}: {} / {}", stage, agent.name, agent.config.model_name)
        requirements: dict[str, Any] = {"require_parameter_budget": True}
        if "goal" in upstream:
            requirements["max_parameter_ratio"] = 1 - float(json.loads(upstream["goal"])["reduction"])
        async def progress(event: dict[str, Any]) -> None:
            # Never echo generated source, prompts, reasoning or credentials to the terminal.
            logger.info("{}: {} {}", stage, event.get("kind", "progress"),
                        {key: event[key] for key in ("status", "valid", "accepted", "tool", "ok") if key in event})
        request = RunRequest(project=project, user_request=task, upstream_artifacts=upstream,
            progress_sink=progress,
            extra={"run_root": str(root), "scope": "project_proposal",
                   "required_upstream_refs": list(upstream),
                   "idea_requirements": requirements,
                   "context_sources": {"project_rules": True, "code_repositories": False,
                       # Source context already contains the frozen project reference files.
                       "project_references": "source_code" not in upstream}})
        context = await agent.build_context(request)
        artifact = await agent.run_loop(request, context)
        return artifact.text
