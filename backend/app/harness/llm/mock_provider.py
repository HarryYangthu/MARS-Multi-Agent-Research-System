"""Mock LLM provider (★ V0 critical).

When no real API key is configured (or ``MARS_MOCK_MODE=always``) every Agent
falls through to this provider. Outputs are crafted so that, when written
into an Agent's expected ``output_schema``, validation succeeds 100% of the
time. This is what lets the Dev E2E demo (ACCEPTANCE §1.1) run with zero
external dependencies.

The fake metadata + bodies are *domain-real* for project ``pimc``
(dual-carrier PIM cancellation with a routing memory-polynomial canceller) and
mutually consistent across the artifact chain:

* RES = residual power ratio in dB, **lower is better**; project gate is
  ``RES <= -26 dB`` (mean) and ``loss <= 0.04`` (max). PIM suppression dB
  = ``-RES``. APE = residual phase error (deg). ``loss = 10**(RES/10)``.
* The "passing" execution run reports ``loss=0.001349`` / ``RES=-28.7`` /
  ``PIM=28.7`` / ``APE=23.2`` / ``n_basis=64`` — the same numbers reused by
  the writing report.
* The diagnosis / feedback packet describe the *failing first attempt*
  (RES observed ~-20.8 vs target -26.0, gap ~5.2, mean aggregation) and
  route the loop back to the Experiment Agent to deepen the canceller sweep.

Bodies are NOT schema-validated, so they are pure narrative. We reuse the
``templates/artifacts/<schema>.md`` bodies where available, but always fall
back to an inline body so the zero-external-dependency guarantee holds even
if the templates directory is missing.

Per DESIGN §16.1 + ACCEPTANCE §1.1.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMProvider,
    Message,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.frontmatter_parser import parse as fm_parse

# --------------------------------------------------------------- domain anchors
#
# Canonical numbers shared across the whole mock artifact chain. Keeping them in
# one place is what guarantees mutual consistency (run_log <-> report, and
# diagnosis <-> feedback_packet).

# "DEEP canceller" passing run (the rerun after the loop deepens memory).
_PASS_LOSS = 0.001349            # linear residual power ratio
_PASS_RES_DB = -28.7             # = 10*log10(_PASS_LOSS) (lower is better)
_PASS_PIM_DB = 28.7              # PIM suppression = -RES
_PASS_APE_DEG = 23.2             # residual phase error, degrees
_PASS_N_BASIS = 64               # memory-polynomial basis columns

# Failing first attempt (shallow memory): RES misses the -26 dB gate.
_FAIL_RES_OBSERVED = -20.8
_RES_TARGET = -26.0              # projects/pimc/diagnostics.yaml
_RES_GAP = round(_RES_TARGET - _FAIL_RES_OBSERVED, 3)   # 5.2 dB short

# Project metric gates (projects/pimc/diagnostics.yaml).
_LOSS_TARGET = 0.04

# True PIM memory depth in execution/pim_cancellation.py (canceller must match).
_TRUE_MEMORY = 12

_TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "templates" / "artifacts"


# ----------------------------------------------------------- per-schema fakes


def _fake_proposal(seed: str, debate_role: str | None) -> dict[str, Any]:
    # Determinism is carried by stable dict ordering + the fixed copy below.
    # No visible seed hash / debate-role tag leaks into the prose.
    return {
        "schema": "proposal.v1",
        "project": "pimc",
        "agent": "idea",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "research_question": (
            "在双载波 PIM 对消中，能否用 hard top-2 路由替代 soft gating，"
            "在保持 RES ≤ -26 dB 的同时显著降低 routing canceller 的有效计算量？"
        ),
        "hypothesis": (
            "若专家记忆深度匹配真实 PIM 记忆效应（~12 taps），hard top-2 路由相比 "
            "soft gating 的 RES 退化可控制在 1.5 dB 以内，同时 MAC 数下降约 30%。"
        ),
        "novelty": (
            "把 hard top-2 路由与 stream-aware gating 结合到记忆多项式对消器上，"
            "现有调研文献中尚未在 dual-carrier PIM-C 场景下系统验证记忆深度对路由鲁棒性的影响。"
        ),
        "theoretical_basis": (
            "稀疏专家激活降低有效计算量；记忆多项式理论指出 canceller 记忆深度需 ≥ "
            "真实 PIM 记忆深度才能压制记忆效应残差，否则 RES 抬升。"
        ),
        "constraints": [
            "baseline_compat: 必须保持 Paper_Total_0327 与 forward(x, stream_label)",
            "ASIC_resource: 目标 MAC 降低 ≥30%",
            "RES: mean ≤ -26 dB；loss: max ≤ 0.04",
        ],
        "related_literature": [
            # Real, on-topic reference — title only (schema-valid without url).
            {
                "title": (
                    "Morgan et al., 'A Generalized Memory Polynomial Model for "
                    "Digital Predistortion of RF Power Amplifiers', IEEE TSP 2006"
                )
            },
            {
                "title": (
                    "Shazeer et al., 'Outrageously Large Neural Networks: "
                    "The Sparsely-Gated Mixture-of-Experts Layer', ICLR 2017"
                )
            },
        ],
        "testable_predictions": [
            {
                "prediction": (
                    "记忆深度 ≥ 12 的 hard top-2 路由配置 RES ≤ -26 dB（mean）。"
                ),
                "metric": "RES",
                "expected_direction": "lte",
                "success_threshold": "≤ -26 dB (mean)",
            },
            {
                "prediction": "相比 soft gating，RES 退化 ≤ 1.5 dB。",
                "metric": "RES",
                "expected_direction": "lte_degradation",
                "success_threshold": "≤ 1.5 dB degradation",
            },
        ],
        "experiment_hint": {
            "variables": ["router_type", "expert_count", "order"],
            "metrics": ["RES", "PIM", "APE", "loss"],
            "minimal_ablations": [
                {"name": "soft_router_mem8", "config": {"router_type": "soft", "expert_count": 8}},
                {"name": "hard_top2_mem12", "config": {"router_type": "hard-top2", "expert_count": 12}},
            ],
        },
        "evidence_refs": [
            {
                "ref": "self_context_1",
                "kind": "self_context",
                "summary": "Idea Agent V2 要求先调研再提出可证伪假设，并保持 baseline 兼容。",
            },
            {
                "ref": "projects/pimc/diagnostics.yaml",
                "kind": "project_rule",
                "summary": "RES 门限 -26 dB(mean)、loss 门限 0.04(max) 为主判据。",
            },
        ],
        "risk_register": [
            {
                "risk": "hard routing 在波束/层切换时可能降低鲁棒性。",
                "severity": "medium",
                "mitigation": "在 Experiment 阶段加入 router_type × memory-depth 消融，并比较 RES/PIM/APE。",
            },
            {
                "risk": "记忆深度不足时记忆效应残差抬升，RES 退化。",
                "severity": "high",
                "mitigation": "扫描 expert_count∈{4,8,12,16} 找到匹配真实记忆深度的拐点。",
            },
        ],
        "downstream_requirements": [
            "Experiment Agent 需把 router_type × expert_count 展开成消融矩阵，控制 snr_db / order。",
            "Coding Agent 必须保持 forward(x, stream_label) 接口兼容（仅允许 keyword-only 新增参数）。",
        ],
        "quality_warnings": [
            "本提案的 related_literature 为 mock_provider 生成的占位调研（标题真实但未联网核验），"
            "正式材料前需人工或联网工具补足并核对引用。",
        ],
        # Real two-round debate substance (no role/seed leakage).
        "debate_summary": {
            "rounds": 2,
            "consensus": (
                "为压低 ASIC 面积优先采用 hard top-2 路由，但前提是 canceller 记忆深度匹配真实 PIM 记忆效应。"
            ),
            "disagreements": [
                "批判者认为 hard 路由在波束/层切换瞬态下的 RES 鲁棒性仍需实验验证，soft gating 更平滑。",
                "对记忆深度下限存在分歧：提案者主张 ~12 taps 足够，批判者要求扫到 16 排除欠拟合。",
            ],
            "risks": [
                "证据不足时不能承诺硬件资源收益。",
                "RES 是 mean 聚合，少数切换时刻的高残差可能被均值掩盖。",
            ],
            "evidence_gaps": [
                "缺少真实硬件 RES 测量，目前仅有 CPU 仿真残差。",
                "外部文献调研为 mock，需联网核验记忆多项式与 路由的最新结果。",
            ],
        },
    }


def _fake_experiment_plan(seed: str) -> dict[str, Any]:
    # router_type × memory-depth ablation grid. memory depth is carried by
    # expert_count (execution/pim_cancellation.py maps experts -> memory taps).
    routers = ["soft", "hard-top2"]
    memories = [4, 8, 12, 16]
    ablations: list[dict[str, Any]] = []
    for router in routers:
        for mem in memories:
            ablations.append(
                {
                    "name": f"{router.replace('-', '_')}_mem{mem}",
                    "config": {
                        "router_type": router,
                        "expert_count": mem,   # -> canceller memory taps
                        "order": 7,
                        "snr_db": 30,
                    },
                }
            )

    # Seed-selected case exercises the baseline-reuse branch deterministically.
    # The soft/mem8 cell matches an archived shallow-memory baseline run.
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    reuse_idx = int(digest[:6], 16) % len(ablations)
    reuse_case = ablations[reuse_idx]["name"]
    matched_digest = digest[:12]

    return {
        "schema": "experiment_plan.v1",
        "project": "pimc",
        "agent": "experiment",
        "upstream_artifact": "idea_proposal.approved.md",
        "variables": {
            "independent": ["router_type", "expert_count"],
            "controlled": ["snr_db", "order", "batch_size", "learning_rate"],
            "dependent": ["RES", "PIM", "APE", "loss"],
        },
        "metrics": {"primary": "RES", "secondary": ["PIM", "APE", "loss", "param_count"]},
        "baseline_ref": {
            "matched_run_id": f"archive_{matched_digest}",
            "match_score": 0.9,
            "reuse_decision": "reuse",
        },
        "ablations": ablations,
        "estimated_runs": len(ablations),
        "estimated_gpu_hours": round(len(ablations) * 0.6, 1),
        # surfaced for the body / downstream; ignored by the schema.
        "reuse_case": reuse_case,
    }


def _fake_code_spec(seed: str) -> dict[str, Any]:
    return {
        "schema": "code_spec.v1",
        "project": "pimc",
        "agent": "coding",
        "upstream_artifact": "experiment_plan.approved.md",
        "target_lang": "python",
        "baseline_compat": {
            "preserved": True,
            "rationale": (
                "Paper_Total_0327 的方法体与构造签名保持冻结；改动全部为新增。"
                "forward(x, stream_label) 的位置参数顺序不变，路由切换通过 keyword-only "
                "参数 router='hard-top2' 注入，默认值保证旧调用行为不变。"
            ),
        },
        # Additive-first: new module + config + tests added; the frozen
        # Model.py is only 'modified' (low risk) because the change is a
        # keyword-only arg that preserves the frozen forward signature.
        "files_changed": [
            {"path": "libs/router_v2.py", "type": "added", "risk": "low"},
            {"path": "configs/router.yaml", "type": "added", "risk": "low"},
            {"path": "tests/test_router_v2.py", "type": "added", "risk": "low"},
            {"path": "tests/test_baseline_smoke.py", "type": "added", "risk": "low"},
            {"path": "libs/Model.py", "type": "modified", "risk": "low"},
        ],
        "new_dependencies": [],
        "test_coverage": {"unit_tests_added": 5, "baseline_smoke_test": "pass"},
    }


def _fake_run_log(seed: str) -> dict[str, Any]:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    # Seed-derived but bounded duration (~1800-3600s) and batch in {256,512}.
    duration = 1800 + (int(digest[:4], 16) % 1801)
    batch_size = 512 if (int(digest[4:6], 16) & 1) else 256
    return {
        "schema": "run_log.v1",
        "project": "pimc",
        "agent": "execution",
        "upstream_artifact": "code_spec.approved.md",
        "run_id": f"mock_deep_{digest}",
        "batch_size": batch_size,
        # Mock-labelled GPUs (no real hardware was used).
        "gpu_used": ["L40S:0", "L40S:1"],
        "duration_seconds": float(duration),
        "status": "completed",
        "metrics": {
            "loss": _PASS_LOSS,        # = 10**(RES/10), kept consistent
            "RES": _PASS_RES_DB,       # dB, lower is better
            "PIM": _PASS_PIM_DB,       # suppression = -RES
            "APE": _PASS_APE_DEG,      # residual phase error, deg
            "n_basis": _PASS_N_BASIS,
        },
        "fingerprint_hash": f"sha256:{digest}",
        "is_mock": True,
    }


def _fake_report(seed: str, debate_role: str | None) -> dict[str, Any]:
    return {
        "schema": "report.v1",
        "project": "pimc",
        "agent": "writing",
        "deliverable_type": "research_report",
        "target_audience": "phd_advisor",
        "chain_refs": {
            "proposal": "idea_proposal.approved.md",
            "plan": "experiment_plan.approved.md",
            "code": "code_spec.approved.md",
            "runs": ["execution/run_log.approved.md"],
        },
        # Optional but realistic fields — same numbers as the passing run_log.
        "results_summary": {
            "RES": {"observed": _PASS_RES_DB, "target": _RES_TARGET, "unit": "dB", "pass": True},
            "PIM": {"observed": _PASS_PIM_DB, "unit": "dB"},
            "APE": {"observed": _PASS_APE_DEG, "unit": "deg"},
            "loss": {"observed": _PASS_LOSS, "target": _LOSS_TARGET, "pass": True},
            "verdict": "pass",
        },
        "key_findings": [
            f"hard top-2 路由配 expert_count=12（≈ 真实 PIM 记忆深度）后 RES={_PASS_RES_DB} dB，"
            f"优于 -26 dB 门限 {round(_RES_TARGET - _PASS_RES_DB, 1)} dB。",
            "记忆深度是 RES 的主导因素:深度 < 真实记忆深度时记忆效应残差抬升,RES 退化。",
            f"PIM 抑制达 {_PASS_PIM_DB} dB,残差相位误差 APE≈{_PASS_APE_DEG}°,接近噪声底。",
        ],
        "limitations": [
            "RES/PIM 来自 CPU 记忆多项式仿真,非真实硬件回采;数值仅供方法验证。",
            "RES 采用 mean 聚合,波束/层切换瞬态的高残差可能被均值掩盖。",
        ],
        "next_steps": [
            "在真实 L40S 上复跑 hard_top2_mem12 配置,核验 RES 与 MAC 收益。",
            "补充切换瞬态下的 per-step RES 分布,而非仅看 mean。",
        ],
        "debate_summary": {
            "rounds": 1,
            "reviewer_critiques": [
                "需要更具体地讨论 ASIC 面积与功耗影响。",
                "建议补充与 soft router baseline 在相同记忆深度下的消融对比。",
            ],
        },
    }


def _fake_report_bundle(seed: str, debate_role: str | None) -> dict[str, Any]:
    return {
        "schema": "report_bundle.v1",
        "project": "pimc",
        "agent": "writing",
        "run_id": f"mock_{seed[:8] or 'report'}",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "data_pack": "writing/report_data_pack.v1.json",
        "deliverables": [
            {
                "kind": "markdown",
                "path": "writing/research_report.approved.md",
                "status": "completed",
                "bytes": 1024,
            },
            {
                "kind": "excel",
                "path": "writing/deliverables/results_workbook.xlsx",
                "status": "completed",
                "bytes": 2048,
            },
            {
                "kind": "word",
                "path": "writing/deliverables/research_report.docx",
                "status": "completed",
                "bytes": 4096,
            },
            {
                "kind": "powerpoint",
                "path": "writing/deliverables/research_deck.pptx",
                "status": "completed",
                "bytes": 4096,
            },
        ],
        "source_refs": [
            "execution/metrics.json",
            "events/evaluation_scorecard.json",
            "writing/research_report.approved.md",
        ],
        "qa_status": {
            "status": "passed",
            "checks": [
                {
                    "name": "excel.zip_structure",
                    "status": "passed",
                    "detail": "results_workbook.xlsx",
                },
                {
                    "name": "word.zip_structure",
                    "status": "passed",
                    "detail": "research_report.docx",
                },
                {
                    "name": "powerpoint.zip_structure",
                    "status": "passed",
                    "detail": "research_deck.pptx",
                },
            ],
        },
        "generation_errors": [],
    }


def _fake_diagnosis(seed: str, debate_role: str | None) -> dict[str, Any]:
    # Failing FIRST attempt: RES misses the -26 dB(mean) gate. The default
    # repair target is the Experiment Agent (under-provisioned memory depth).
    return {
        "schema": "diagnosis.v1",
        "project": "pimc",
        "agent": "commander",
        "run_id": f"mock_{seed[:8]}",
        "attempt": 1,
        "passed": False,
        "failed_metrics": [
            {
                "metric": "RES",
                "observed": _FAIL_RES_OBSERVED,   # -20.8 dB
                "target": _RES_TARGET,            # -26.0 dB
                "direction": "lte",
                "gap": _RES_GAP,                  # 5.2 dB short
                "aggregation": "mean",
            }
        ],
        "suspected_causes": [
            {
                "kind": "metrics_gap",
                "summary": (
                    "RES 比 -26 dB 门限高约 5.2 dB,首跑采用的记忆深度浅于真实 PIM "
                    "记忆效应(~12 taps),记忆效应残差未被压制。"
                ),
                "severity": "high",
                "evidence": ["execution/metrics.json", "execution/run_log.v1.md"],
            },
            {
                "kind": "config_sanity",
                "summary": "消融网格的 expert_count 上限偏低,未覆盖匹配真实记忆深度的配置。",
                "severity": "medium",
                "evidence": ["experiment/experiment_plan.v1.md"],
            },
        ],
        "recommended_target": "experiment",
        "recommended_action": (
            "加深 canceller 记忆深度扫描(expert_count 扫到 ≥12),"
            "在 hard-top2 路由下重跑消融以压低 RES 至门限。"
        ),
        "evidence_refs": ["execution/metrics.json", "execution/run_log.v1.md"],
        "budget_status": "within_budget",
        "confidence": 0.82,
    }


def _fake_feedback_packet(seed: str, debate_role: str | None) -> dict[str, Any]:
    # Must AGREE with _fake_diagnosis: same failed metric, same target agent.
    return {
        "schema": "feedback_packet.v1",
        "project": "pimc",
        "agent": "commander",
        "run_id": f"mock_{seed[:8]}",
        "target_agent": "experiment",
        "attempt": 2,
        "source_attempt": 1,
        "confidence": 0.82,
        "why_this_agent": (
            "RES 缺口的根因是消融网格记忆深度欠配(experiment-design 问题),"
            "应由 Experiment Agent 加深 canceller 记忆深度扫描而非改代码。"
        ),
        "evidence_refs": ["execution/metrics.json", "diagnosis/diagnosis.v1.md"],
        "failed_metrics": [
            {
                "metric": "RES",
                "observed": _FAIL_RES_OBSERVED,
                "target": _RES_TARGET,
                "direction": "lte",
                "gap": _RES_GAP,
                "aggregation": "mean",
            }
        ],
        "do_next": [
            "把 expert_count 扫描扩到 {8,12,16},覆盖匹配真实 PIM 记忆深度(~12)的配置。",
            "在 hard-top2 路由下重跑消融,以 RES(mean) ≤ -26 dB 为通过判据。",
        ],
        "avoid_repeating": [
            "不要再用 expert_count ≤ 8 的浅记忆配置作为主候选。",
            "不要修改 protected baseline 接口 forward(x, stream_label)。",
        ],
        "context_refs": ["experiment/experiment_plan.v1.md", "diagnosis/diagnosis.v1.md"],
        "memory_candidates": [
            {
                "kind": "lesson",
                "summary": "RES 缺口优先怀疑记忆深度欠配,回传 Experiment Agent 加深扫描。",
            }
        ],
    }


def _fake_evaluation_report(seed: str, debate_role: str | None) -> dict[str, Any]:
    return {
        "schema": "evaluation_report.v1",
        "project": "pimc",
        "scope": "artifact",
        "target_ref": "writing/research_report.v1.md",
        "target_schema": "report.v1",
        "evaluator": "contract.artifact_reviewer",
        "evaluator_version": 1,
        # allOf invariant: decision 'block' => blocking true. We emit 'warn'
        # (non-blocking), which trivially satisfies the conditional.
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
                "id": "eval-evidence-1",
                "severity": "medium",
                "category": "evidence",
                "message": (
                    "RES/PIM 数值来自 CPU 记忆多项式仿真,报告需明确标注非真实硬件回采。"
                ),
                "evidence_refs": ["execution/run_log.approved.md"],
            }
        ],
        "recommended_actions": [
            "在真实 L40S 上复跑 hard_top2_mem12 配置后再作为最终论文材料。",
        ],
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
    "report_bundle.v1": _fake_report_bundle,
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


def _template_body(schema_id: str) -> str | None:
    """Best-effort load of the markdown body from templates/artifacts/<id>.md.

    Returns the body with its frontmatter stripped, or ``None`` if the template
    is unavailable / unparseable. Never raises and never touches the network, so
    callers can fall back to an inline body and keep the zero-dep guarantee.
    """
    path = _TEMPLATES_DIR / f"{schema_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        body = fm_parse(text).body.strip()
    except Exception:
        return None
    return body or None


# ----------------------------------------------------- inline fallback bodies
#
# Used when templates/artifacts is missing. These are interpolated with the
# canonical anchor numbers so the body matches the frontmatter.


def _inline_body(schema_id: str, metadata: dict[str, Any]) -> str:
    if schema_id == "proposal.v1":
        return (
            "# 研究假设草案\n\n"
            "## 调研依据\n\n"
            "双载波 PIM 的三阶交调落在 2f1-f2 / 2f2-f1,且具有记忆效应(真实记忆深度 "
            f"≈ {_TRUE_MEMORY} taps)。记忆多项式 canceller 若记忆深度不足,记忆效应残差无法压制。\n\n"
            "## 可证伪预测\n\n"
            "当 canceller 记忆深度匹配真实 PIM 记忆深度时,hard top-2 路由 RES ≤ -26 dB(mean);"
            "否则记忆效应残差抬升、RES 退化,假设被推翻。\n\n"
            "## 下一步实验建议\n\n"
            "Experiment Agent 展开 router_type × expert_count(记忆深度)消融,控制 snr_db / order。\n"
        )
    if schema_id == "experiment_plan.v1":
        runs = metadata.get("estimated_runs", "?")
        reuse = metadata.get("reuse_case", "")
        return (
            "# 实验计划\n\n"
            "## 假设验证策略\n\n"
            "在 soft 与 hard-top2 两种路由下交叉扫描 canceller 记忆深度"
            "(expert_count∈{4,8,12,16}),定位 RES 随记忆深度的拐点。\n\n"
            "## 变量与控制\n\n"
            "- 自变量:router_type、expert_count(→ 记忆 taps)\n"
            "- 控制变量:snr_db=30、order=7、batch_size、learning_rate\n"
            "- 因变量:RES(主)、PIM、APE、loss\n\n"
            "## 基线复用决策\n\n"
            f"网格中的 `{reuse}` 与归档浅记忆基线匹配(match_score≈0.9),decision=reuse,直接复用其结果。\n\n"
            "## 资源估算\n\n"
            f"估算 {runs} 次 run。判据:RES(mean) ≤ -26 dB 且 loss(max) ≤ 0.04。\n"
        )
    if schema_id == "code_spec.v1":
        return (
            "# 代码规格\n\n"
            "## 补丁目标\n\n"
            "为记忆多项式对消器新增 hard top-2 路由,在保持 baseline 冻结面的前提下加深记忆 taps。\n\n"
            "## 兼容性保护\n\n"
            "`Paper_Total_0327` 与 `forward(x, stream_label)` 冻结面不动;路由切换以 keyword-only "
            "参数注入,默认值保证旧调用不变。\n\n"
            "## 关键 diff\n\n"
            + _CODE_DIFF_SNIPPET
            + "\n## 测试覆盖\n\n"
            "新增 5 个单测;baseline smoke test 通过。\n"
        )
    if schema_id == "run_log.v1":
        m = metadata.get("metrics", {})
        return (
            "# 执行日志\n\n"
            f"DEEP canceller(记忆深度匹配真实 PIM,n_basis={m.get('n_basis')})收敛。\n\n"
            f"- RES = {m.get('RES')} dB(lower better,门限 ≤ -26 dB)→ 达标\n"
            f"- PIM 抑制 = {m.get('PIM')} dB\n"
            f"- APE = {m.get('APE')}°\n"
            f"- loss = {m.get('loss')}(= 10^(RES/10),门限 ≤ 0.04)→ 达标\n\n"
            "运行于 mock 标注的 L40S:0 / L40S:1(无真实硬件)。\n"
        )
    if schema_id == "report.v1":
        return (
            "# 研究报告\n\n"
            "## 研究问题与方法\n\n"
            "在双载波 PIM 对消中用 hard top-2 路由替代 soft gating,并匹配 canceller 记忆深度。\n\n"
            "## 实验结果\n\n"
            f"hard_top2_mem12 配置:RES = {_PASS_RES_DB} dB、PIM = {_PASS_PIM_DB} dB、"
            f"APE = {_PASS_APE_DEG}°、loss = {_PASS_LOSS},RES/loss 均达标。\n\n"
            "## 失败分析\n\n"
            f"首跑浅记忆配置 RES ≈ {_FAIL_RES_OBSERVED} dB,缺口 {_RES_GAP} dB,"
            "经 Commander 回传 Experiment Agent 加深记忆深度后达标。\n\n"
            "## 风险与下一步\n\n"
            "数值为 CPU 仿真,需真实硬件复核;RES 为 mean 聚合,应补充切换瞬态分布。\n"
        )
    if schema_id == "report_bundle.v1":
        return (
            "# Report Bundle\n\n"
            "该 manifest 汇总 Writing Agent 的 Markdown、Excel、Word、PPT 产物,"
            "并记录 data pack、来源引用和结构化 QA 状态。\n"
        )
    if schema_id == "diagnosis.v1":
        fm = (metadata.get("failed_metrics") or [{}])[0]
        return (
            "# 诊断\n\n"
            f"首跑(attempt 1)未通过:RES 观测 {fm.get('observed')} dB,目标 {fm.get('target')} dB"
            f"(direction=lte,aggregation=mean),缺口 {fm.get('gap')} dB。\n\n"
            "根因:canceller 记忆深度浅于真实 PIM 记忆效应,记忆效应残差未压制。\n\n"
            "建议把状态机拉回 Experiment Agent,加深记忆深度扫描后重跑。\n"
        )
    if schema_id == "feedback_packet.v1":
        return (
            "# Commander Feedback Packet\n\n"
            "回传目标:Experiment Agent。\n\n"
            f"失败指标 RES 观测 {_FAIL_RES_OBSERVED} dB vs 目标 {_RES_TARGET} dB(缺口 {_RES_GAP} dB)。\n\n"
            "下一步:把 expert_count 扫到 {8,12,16},在 hard-top2 路由下重跑,以 RES(mean) ≤ -26 dB 为判据;"
            "不要修改 baseline 冻结接口。\n"
        )
    if schema_id == "evaluation_report.v1":
        return (
            "# Evaluation Report\n\n"
            "对 writing/research_report.v1.md 的合规与证据质量评估:schema 合规、baseline 兼容,"
            "但 RES/PIM 数值来自 CPU 仿真,需标注非真实硬件回采(decision=warn,非阻塞)。\n"
        )
    return (
        f"# 模拟产物 {schema_id}\n\n"
        "该产物由 mock_provider 生成,内容满足对应 schema,可用于端到端流程验证。\n"
    )


# A real unified-diff snippet: adds memory taps + a keyword-only router arg to
# forward, preserving the frozen positional signature (Gate-5 compatible).
_CODE_DIFF_SNIPPET = """```diff
--- a/libs/Model.py
+++ b/libs/Model.py
@@ class Paper_Router_v2(nn.Module):
-    def __init__(self, n_experts: int = 8, memory: int = 8):
+    def __init__(self, n_experts: int = 8, memory: int = 12):   # match true PIM memory (~12 taps)
         super().__init__()
         self.n_experts = n_experts
-        self.memory = memory
+        self.memory = memory                                    # deeper taps -> suppress memory-effect residual
         self.gate = nn.Linear(memory, n_experts)

-    def forward(self, x, stream_label):
+    def forward(self, x, stream_label, *, router: str = "hard-top2"):
         # x: (B, T, D); stream_label: (B,)  — frozen positional signature preserved
-        logits = self.gate(x)                                   # (B, T, E)
-        weights = logits.softmax(dim=-1)                        # soft gating
+        logits = self.gate(x)                                   # (B, T, E)
+        if router == "hard-top2":
+            top = logits.topk(2, dim=-1)                        # (B, T, 2)
+            weights = self._scatter_top2(top, logits.shape)     # (B, T, E) sparse
+        else:
+            weights = logits.softmax(dim=-1)                    # soft gating fallback
         return self._mix_experts(x, weights, stream_label)     # (B, T, D)
```"""


def _render_body(
    schema_id: str, debate_role: str | None, metadata: dict[str, Any] | None = None
) -> str:
    """Render a domain-real markdown body for the given schema.

    Prefers the on-disk ``templates/artifacts/<id>.md`` body, falling back to an
    inline body interpolated with the canonical anchor numbers so the demo runs
    with zero external dependencies even without the templates directory.
    """
    meta = metadata or {}
    template = _template_body(schema_id)
    if template:
        return template
    return _inline_body(schema_id, meta)


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
        body = _render_body(schema_id, debate_role, metadata)
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
