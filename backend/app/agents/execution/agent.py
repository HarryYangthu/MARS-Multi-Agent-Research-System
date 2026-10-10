"""Deterministic intake of approved experiments; no model plans or launches jobs."""
from __future__ import annotations
from typing import Any
from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import dumps, parse
from app.harness.schema.experiment_contract import delivery_experiments, document_hash

class ExecutionAgent(BaseAgent):
    name = "execution"
    output_schema = "run_log.v1"
    requires_model = False
    native_structured_delivery = True
    agent_brief = "接收批准方案和编码交付，保持实验配置；宿主核对配置后管理真实作业。"

    async def build_context(self, request: RunRequest) -> ContextPack:
        return ContextPack(system=self.agent_brief, project=request.project,
                           task=request.user_request, upstream=dict(request.upstream_artifacts))

    def submission_schema(self, request: RunRequest) -> dict[str, Any] | None:
        schema = super().submission_schema(request)
        assert schema is not None
        schema["required"] = list(dict.fromkeys(schema["required"] + ["planned_experiments", "execution_phase", "is_mock"]))
        schema["properties"].update({
            "status": {"const": "interrupted"}, "execution_phase": {"const": "planned"},
            "is_mock": {"const": False},
            "fingerprint_hash": {"const": "sha256:" + digest(request.upstream_artifacts)},
            "metrics": {"type": "object", "required": ["planned_experiments"], "additionalProperties": False,
                        "properties": {"planned_experiments": {"type": "integer", "minimum": 1}}},
            "planned_experiments": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["name", "config"], "additionalProperties": False,
                "properties": {"name": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"},
                               "config": {"type": "object"}}}},
        })
        return schema

    async def run_loop(self, request: RunRequest, context: ContextPack) -> Artifact:
        artifact = await self.draft(request, context)
        errors = await self.validate_candidate(request, artifact.text, [])
        if errors:
            raise ValueError("; ".join(errors))
        return artifact

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        documents: dict[str, dict[str, Any]] = {}
        plan_text = ''
        coding_text = ''
        for text in context.upstream.values():
            if text.startswith("[upstream artifact: "):
                text = text.split("\n", 1)[1]
            if not text.startswith("---\n"):
                continue
            metadata = parse(text).metadata
            schema = str(metadata.get("schema", ""))
            if schema in {"experiment_plan.v1", "code_spec.v1"}:
                if metadata.get("project") != request.project or schema in documents:
                    raise ValueError("批准交付的项目身份或唯一性无法校验")
                documents[schema] = metadata
                if schema == 'experiment_plan.v1':
                    plan_text = text
                else:
                    coding_text = text
        experiments = delivery_experiments(plan_text, documents.get('code_spec.v1', {}),
            project=request.project, plan_required=bool(request.extra.get('experiment_plan_required')))
        metadata = {"schema": self.output_schema, "project": request.project, "agent": self.name,
            "run_id": str(request.extra["run_id"]), "status": "interrupted", "execution_phase": "planned",
            "runtime_mode": "deterministic", "intake_invocation": request.extra.get("invocation_id", ""), "is_mock": False,
            "metrics": {"planned_experiments": len(experiments)}, "planned_experiments": experiments,
            "execution_source": 'experiment_plan' if plan_text else 'coding_delivery',
            "coding_spec_sha256": document_hash(coding_text),
            "fingerprint_hash": "sha256:" + digest(request.upstream_artifacts)}
        body = ("# 仿真执行清单\n\n接收实际编码运行交付；有实验方案时同时核验其约束。未调用模型、未启动实验。\n\n"
                "核对运行环境、数据、随机种子、预算单位与配置文件后启动。"
                "执行管理器不改方案、代码、指标或预算；结果来自真实作业收据。\n\n"
                + "\n".join(f"- {item['name']}" for item in experiments))
        if request.progress_sink is not None:
            await request.progress_sink({"kind": "action", "phase": "intake",
                "message": f"已接收 {len(experiments)} 组批准实验；无需模型生成执行计划。"})
        return Artifact(text=dumps(metadata, body), schema_id=self.output_schema, metadata=metadata, body=body)
