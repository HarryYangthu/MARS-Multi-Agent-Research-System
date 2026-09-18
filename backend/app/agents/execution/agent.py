"""Execution planning uses the real Agent loop; the bridge executes approved jobs."""
from __future__ import annotations

from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import parse


class ExecutionAgent(BaseAgent):
    name = "execution"
    output_schema = "run_log.v1"
    native_structured_delivery = True
    agent_brief = (
        "根据已批准的 experiment_plan 与 code_spec 生成当前项目的真实执行计划。"
        "planned_experiments 每项含唯一 name 和 config，config 必须保留方案明确指定的 seed。"
        "只安排用户要求的实验，不套用其他项目的参数或默认扫描。"
        "实际执行由 bridge 在批准后启动；本阶段不得调用执行工具或声称已有实验结果。"
        "输出 execution_phase=planned、status=interrupted、is_mock=false，"
        "metrics 只包含 planned_experiments 的数量。fingerprint_hash 是宿主给出的输入摘要，"
        "不是实验测量收据。正文说明待执行计划与约束，不编造耗时、设备或指标。"
    )

    def submission_schema(self, request: RunRequest) -> dict[str, Any] | None:
        schema = super().submission_schema(request)
        assert schema is not None
        schema["required"] = list(dict.fromkeys(schema["required"] + ["planned_experiments", "execution_phase", "is_mock"]))
        schema["properties"].update({
            "status": {"const": "interrupted"},
            "execution_phase": {"const": "planned"},
            "is_mock": {"const": False},
            "fingerprint_hash": {"const": "sha256:" + digest(request.upstream_artifacts)},
            "metrics": {"type": "object", "required": ["planned_experiments"], "additionalProperties": False,
                        "properties": {"planned_experiments": {"type": "integer", "minimum": 1}}},
            "planned_experiments": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["name", "config"], "additionalProperties": False,
                "properties": {"name": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"},
                               "config": {"type": "object", "properties": {"seed": {"type": "integer", "minimum": 0}}}}}},
        })
        return schema

    async def validate_candidate(self, request: RunRequest, text: str, observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if errors:
            return errors
        metadata = parse(text).metadata
        plans = metadata["planned_experiments"]
        if len({item["name"] for item in plans}) != len(plans):
            errors.append("/planned_experiments: experiment names must be unique")
        if metadata["metrics"]["planned_experiments"] != len(plans):
            errors.append("/metrics/planned_experiments: count must equal the actual plan length")
        return errors

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        return await self._draft_via_llm(request, context)
