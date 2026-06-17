"""Mock LLM provider (★ V0 critical).

When no real API key is configured (or ``MARS_MOCK_MODE=always``) every Agent
falls through to this provider. Outputs are crafted so that, when written
into an Agent's expected ``output_schema``, validation succeeds 100% of the
time. This is what lets the Dev E2E demo (ACCEPTANCE §1.1) run with zero
external dependencies.

Per DESIGN §16.1 + ACCEPTANCE §1.1.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMProvider,
    Message,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps


# ----------------------------------------------------------- per-schema fakes


def _fake_proposal(seed: str, debate_role: str | None) -> dict[str, Any]:
    angles = {
        "proposer": "主张 hard top-2 路由可以以较低复杂度达成目标",
        "critic": "强调波束/层切换时路由鲁棒性风险",
        "judge": "综合提案者与批判者意见，给出混合方案建议",
        None: "平衡视角",
    }
    angle = angles.get(debate_role or "", "平衡视角")
    return {
        "schema": "proposal.v1",
        "project": "moe-pimc",
        "agent": "idea",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "research_question": f"如何在保持 RES 性能的同时简化 ATK-MoE 路由？（seed:{seed[:8]}）",
        "hypothesis": (
            f"采用 hard top-2 路由后，RES 退化可控制在 1.5 dB 以内。"
            f"【{angle}】"
        ),
        "novelty": (
            f"将 stream-aware gating 与硬路由结合，形成可解释、低计算量的专家选择策略；视角：{angle}。"
        ),
        "theoretical_basis": "稀疏专家激活理论与 PIM-aware 路由约束。",
        "constraints": ["baseline_compat: 必须保持", "ASIC_resource: 目标降低 ≥40%"],
        "related_literature": [
            {"title": "MoE 路由综述 2024", "url": "https://arxiv.org/abs/2404.00000"},
        ],
        "testable_predictions": [
            {
                "prediction": "hard top-2 路由在 8L 配置下的 RES 退化不超过 1.5 dB。",
                "metric": "RES",
                "expected_direction": "lte_degradation",
                "success_threshold": "≤1.5 dB degradation",
            }
        ],
        "experiment_hint": {
            "variables": ["router_type", "expert_count"],
            "metrics": ["RES", "PIM", "APE", "loss"],
            "minimal_ablations": [
                {"name": "soft_router_baseline", "config": {"router_type": "soft"}},
                {"name": "hard_top2_router", "config": {"router_type": "hard-top2"}},
            ],
        },
        "evidence_refs": [
            {
                "ref": "self_context_1",
                "kind": "self_context",
                "summary": "Idea Agent V1 要求先调研再提出可证伪假设。",
            }
        ],
        "risk_register": [
            {
                "risk": "hard routing 在波束/层切换时可能降低鲁棒性。",
                "severity": "medium",
                "mitigation": "在 Experiment 阶段加入 router_type × expert_count 消融。",
            }
        ],
        "downstream_requirements": [
            "Experiment Agent 需要把 router_type 和 expert_count 转成消融矩阵。",
            "Coding Agent 必须保持 forward(x, stream_label) 接口兼容。",
        ],
        "debate_summary": {
            "rounds": 0,
            "consensus": "",
            "disagreements": [],
            "risks": [],
            "evidence_gaps": ["外部网络调研默认关闭。"],
        },
    }


def _fake_experiment_plan(seed: str) -> dict[str, Any]:
    return {
        "schema": "experiment_plan.v1",
        "project": "moe-pimc",
        "agent": "experiment",
        "upstream_artifact": "idea_proposal.approved.md",
        "variables": {
            "independent": ["expert_count", "router_type"],
            "controlled": ["batch_size", "epochs"],
            "dependent": ["RES", "PIM", "APE"],
        },
        "metrics": {"primary": "RES", "secondary": ["PIM", "APE", "param_count"]},
        "baseline_ref": {
            "matched_run_id": None,
            "match_score": None,
            "reuse_decision": "rerun",
        },
        "ablations": [
            {"name": "expert_count_4", "config": {"expert_count": 4}},
            {"name": "expert_count_8", "config": {"expert_count": 8}},
            {"name": "expert_count_16", "config": {"expert_count": 16}},
        ],
        "estimated_runs": 6,
        "estimated_gpu_hours": 18,
    }


def _fake_code_spec(seed: str) -> dict[str, Any]:
    return {
        "schema": "code_spec.v1",
        "project": "moe-pimc",
        "agent": "coding",
        "upstream_artifact": "experiment_plan.approved.md",
        "target_lang": "python",
        "baseline_compat": {
            "preserved": True,
            "rationale": (
                "保持 forward(x, stream_label) 接口不变；新增 "
                "Paper_Router_v2，并与现有 Paper_Total_0327 并行保留。"
            ),
        },
        "files_changed": [
            {"path": "libs/Model.py", "type": "modified", "risk": "medium"},
            {"path": "tests/test_router_v2.py", "type": "added", "risk": "low"},
        ],
        "new_dependencies": [],
        "test_coverage": {"unit_tests_added": 3, "baseline_smoke_test": "pass"},
    }


def _fake_run_log(seed: str) -> dict[str, Any]:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return {
        "schema": "run_log.v1",
        "project": "moe-pimc",
        "agent": "execution",
        "upstream_artifact": "code_spec.approved.md",
        "run_id": f"mock_{digest}",
        "batch_size": 512,
        "gpu_used": [],
        "duration_seconds": 30.0,
        "status": "completed",
        "metrics": {"RES": -41.7, "PIM": -18.2, "APE": 23.1, "loss": 0.0142},
        "fingerprint_hash": f"sha256:{digest}",
        "is_mock": True,
    }


def _fake_report(seed: str, debate_role: str | None) -> dict[str, Any]:
    role = debate_role or "平衡视角"
    return {
        "schema": "report.v1",
        "project": "moe-pimc",
        "agent": "writing",
        "deliverable_type": "research_report",
        "target_audience": "phd_advisor",
        "chain_refs": {
            "proposal": "idea_proposal.approved.md",
            "plan": "experiment_plan.approved.md",
            "code": "code_spec.approved.md",
            "runs": ["execution/run_log.approved.md"],
        },
        "debate_summary": {
            "rounds": 1,
            "reviewer_critiques": [
                f"（{role}）需要更具体地讨论 ASIC 面积与功耗影响。",
                f"（{role}）建议补充与 soft router baseline 的消融对比。",
            ],
        },
    }


def _fake_diagnosis(seed: str, debate_role: str | None) -> dict[str, Any]:
    return {
        "schema": "diagnosis.v1",
        "project": "moe-pimc",
        "agent": "bridge",
        "run_id": f"mock_{seed[:8]}",
        "attempt": 1,
        "passed": False,
        "failed_metrics": [
            {
                "metric": "loss",
                "observed": 0.12,
                "target": 0.02,
                "direction": "lte",
                "gap": 0.1,
                "aggregation": "max",
            }
        ],
        "suspected_causes": [
            {
                "kind": "metrics_gap",
                "summary": "Mock 诊断发现指标与目标之间仍有差距。",
                "severity": "high",
                "evidence": ["execution/metrics.json"],
            }
        ],
        "recommended_target": "coding",
        "recommended_action": "生成补丁 diff，并提交人工审核。",
        "evidence_refs": ["execution/metrics.json"],
        "budget_status": "within_budget",
    }


def _fake_feedback_packet(seed: str, debate_role: str | None) -> dict[str, Any]:
    return {
        "schema": "feedback_packet.v1",
        "project": "moe-pimc",
        "agent": "commander",
        "run_id": f"mock_{seed[:8]}",
        "target_agent": "coding",
        "attempt": 2,
        "source_attempt": 1,
        "confidence": 0.82,
        "why_this_agent": "Mock 反馈包建议 Coding Agent 修复指标缺口相关实现。",
        "evidence_refs": ["execution/metrics.json", "diagnosis/diagnosis.v1.md"],
        "failed_metrics": [
            {
                "metric": "loss",
                "observed": 0.12,
                "target": 0.02,
                "direction": "lte",
            }
        ],
        "do_next": [
            "检查 router stability clamp。",
            "生成最小补丁并保持 baseline 兼容。",
        ],
        "avoid_repeating": ["不要修改 protected baseline 接口。"],
        "context_refs": ["coding/code_spec.v1.md"],
        "memory_candidates": [
            {
                "kind": "lesson",
                "summary": "失败诊断应优先回传给能直接修复的 Agent。",
            }
        ],
    }


def _fake_evaluation_report(seed: str, debate_role: str | None) -> dict[str, Any]:
    return {
        "schema": "evaluation_report.v1",
        "project": "moe-pimc",
        "scope": "artifact",
        "target_ref": "writing/research_report.v1.md",
        "target_schema": "report.v1",
        "evaluator": "mock_evaluator",
        "evaluator_version": 1,
        "decision": "warn",
        "overall_score": 0.78,
        "blocking": False,
        "scores": {
            "schema_compliance": 1.0,
            "evidence_quality": 0.72,
            "baseline_compatibility": 0.86,
        },
        "findings": [
            {
                "id": "mock-eval-1",
                "severity": "medium",
                "category": "evidence",
                "message": "报告需要补充更多真实实验引用。",
                "evidence_refs": ["execution/run_log.approved.md"],
            }
        ],
        "recommended_actions": ["补充真实硬件实验后再作为最终论文材料。"],
        "created": datetime.now(tz=timezone.utc).isoformat(),
    }


def _fake_experiment_plan_w(seed: str, role: str | None = None) -> dict[str, Any]:
    return _fake_experiment_plan(seed)


def _fake_code_spec_w(seed: str, role: str | None = None) -> dict[str, Any]:
    return _fake_code_spec(seed)


def _fake_run_log_w(seed: str, role: str | None = None) -> dict[str, Any]:
    return _fake_run_log(seed)


_FakeBuilder = Callable[[str, str | None], dict[str, Any]]

_FAKE_BUILDERS: dict[str, _FakeBuilder] = {
    "proposal.v1": _fake_proposal,
    "experiment_plan.v1": _fake_experiment_plan_w,
    "code_spec.v1": _fake_code_spec_w,
    "run_log.v1": _fake_run_log_w,
    "diagnosis.v1": _fake_diagnosis,
    "feedback_packet.v1": _fake_feedback_packet,
    "evaluation_report.v1": _fake_evaluation_report,
    "report.v1": _fake_report,
}


def build_fake_metadata(
    schema_id: str, *, seed: str = "", debate_role: str | None = None
) -> dict[str, Any]:
    """Public helper used by tests / Agent skeletons that need a fake doc.

    Returns a dict that is guaranteed to validate against the named schema.
    """
    builder = _FAKE_BUILDERS.get(schema_id)
    if builder is None:
        raise ValueError(f"no mock builder for schema '{schema_id}'")
    return builder(seed, debate_role)


def _seed_from_messages(messages: list[Message]) -> str:
    body = "".join(m.content for m in messages)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _render_body(schema_id: str, debate_role: str | None) -> str:
    role = debate_role or "默认"
    return (
        f"# 模拟产物 {schema_id}（角色={role}）\n\n"
        "该产物由 mock_provider 生成。\n"
        "内容为占位示例，但已经满足对应 schema，可用于端到端流程验证。\n"
    )


class MockProvider(LLMProvider):
    """Deterministic, schema-valid placeholder responses."""

    name = "mock"

    def __init__(self, *, default_schema: str | None = None) -> None:
        self.default_schema = default_schema

    def _resolve_schema(self, config: LLMConfig) -> str:
        return (
            config.response_schema
            or config.extra.get("response_schema")
            or self.default_schema
            or "proposal.v1"
        )

    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion:
        schema_id = self._resolve_schema(config)
        debate_role = config.extra.get("debate_role")
        seed = _seed_from_messages(messages) + (debate_role or "")
        metadata = build_fake_metadata(
            schema_id, seed=seed, debate_role=debate_role
        )
        body = _render_body(schema_id, debate_role)
        text = fm_dumps(metadata, body)
        return Completion(
            text=text,
            provider="mock",
            model=config.model or "mock-1",
            is_mock=True,
            debate_role=debate_role,
            raw={"schema": schema_id},
        )

    async def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        completion = await self.complete(messages, config)
        # yield in ~64-char chunks for realistic UI streaming
        chunk_size = 64
        for i in range(0, len(completion.text), chunk_size):
            yield Delta(text=completion.text[i : i + chunk_size])
            await asyncio.sleep(0)
        yield Delta(text="", finish_reason="stop")
