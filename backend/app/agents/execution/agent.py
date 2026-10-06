"""Deterministic intake of approved experiments; no model plans or launches jobs."""
from __future__ import annotations
from typing import Any
from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import dumps, parse
from app.harness.schema.experiment_contract import handoff_errors

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
        errors = handoff_errors(plan_text, documents.get('code_spec.v1', {})) if plan_text else ['缺少批准实验方案']
        if errors:
            raise ValueError('；'.join(errors))
        rows = documents.get("experiment_plan.v1", {}).get("ablations", [])
        if not isinstance(rows, list) or not rows:
            raise ValueError("缺少已批准实验矩阵；执行管理器不会自行设计实验")
        jobs = documents.get("code_spec.v1", {}).get("execution_jobs", [])
        if not isinstance(jobs, list):
            raise ValueError("编码交付的 execution_jobs 必须是清单")
        by_name: dict[str, dict[str, Any]] = {}
        for job in jobs:
            if not isinstance(job, dict) or not isinstance(job.get("config"), dict):
                raise ValueError("编码交付缺少明确的作业配置")
            name = str(job.get("name", ""))
            if not name or name in by_name:
                raise ValueError("编码交付的作业名称为空或重复")
            by_name[name] = job["config"]
        experiments: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("config"), dict):
                raise ValueError("批准实验缺少配置")
            name = str(row.get("name", ""))
            config = dict(row["config"])
            binding = by_name.pop(name, {})
            for key, value in binding.items():
                if key in config and config[key] != value:
                    raise ValueError(f"{name} 的编码交付改写了批准参数 {key}；请回到实验设计核对")
                config[key] = value
            experiments.append({"name": name, "config": config})
        if by_name or len({item["name"] for item in experiments}) != len(experiments):
            raise ValueError("编码作业与批准实验矩阵不一致")
        metadata = {"schema": self.output_schema, "project": request.project, "agent": self.name,
            "run_id": str(request.extra["run_id"]), "status": "interrupted", "execution_phase": "planned",
            "runtime_mode": "deterministic", "intake_invocation": request.extra.get("invocation_id", ""), "is_mock": False,
            "metrics": {"planned_experiments": len(experiments)}, "planned_experiments": experiments,
            "fingerprint_hash": "sha256:" + digest(request.upstream_artifacts)}
        body = ("# 仿真执行清单\n\n直接接收已批准实验矩阵与编码交付，未调用模型、未启动实验。\n\n"
                "核对运行环境、数据、随机种子、预算单位与配置文件后启动。"
                "执行管理器不改方案、代码、指标或预算；结果来自真实作业收据。\n\n"
                + "\n".join(f"- {item['name']}" for item in experiments))
        if request.progress_sink is not None:
            await request.progress_sink({"kind": "action", "phase": "intake",
                "message": f"已接收 {len(experiments)} 组批准实验；无需模型生成执行计划。"})
        return Artifact(text=dumps(metadata, body), schema_id=self.output_schema, metadata=metadata, body=body)
