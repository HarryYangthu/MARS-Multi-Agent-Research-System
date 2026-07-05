---
schema: proposal.v1
project: pimc
agent: idea
created: 2026-05-04T10:32:00Z
research_question: "如何在 8L 配置下进一步降低 PIMC 的计算资源，同时保持 RES 性能？"
hypothesis: "简化的 hard top-2 路由可以在 MAC 数降低约 30% 的同时，将 RES 退化控制在 1.5 dB 以内。"
novelty: "将 hard top-2 路由与 stream-aware gating 结合，现有调研文献中尚未针对 PIM-C 场景系统验证。"
theoretical_basis: "稀疏专家激活可降低有效计算量；PIM cancellation 约束可保持主导专家路径的稳定性。"
constraints:
  - "baseline_compat: 必须保持"
  - "ASIC_resource: 目标降低 ≥40%"
related_literature:
  - title: "路由综述 2024"
    url: "https://arxiv.org/abs/2404.00000"
testable_predictions:
  - prediction: "hard top-2 router 在 8L 配置下的 RES 退化不超过 1.5 dB。"
    metric: "RES"
    expected_direction: "lte_degradation"
    success_threshold: "≤1.5 dB degradation"
experiment_hint:
  variables:
    - "router_type"
    - "expert_count"
  metrics:
    - "RES"
    - "PIM"
    - "APE"
    - "loss"
  minimal_ablations:
    - name: "soft_router_baseline"
      config:
        router_type: "soft"
    - name: "hard_top2_router"
      config:
        router_type: "hard-top2"
evidence_refs:
  - ref: "self_context_1"
    kind: "project_rule"
    summary: "Idea Agent 必须保持 baseline 兼容并服务后续实验验证。"
risk_register:
  - risk: "hard routing 可能在波束/层切换时降低鲁棒性。"
    severity: "medium"
    mitigation: "在 Experiment 阶段加入 router_type × expert_count 消融，并比较 RES/PIM/APE。"
downstream_requirements:
  - "Experiment Agent 需要把 router_type 和 expert_count 转成消融矩阵。"
  - "Coding Agent 必须保持 forward(x, stream_label) 接口兼容。"
debate_summary:
  rounds: 2
  consensus: "为了 ASIC 简化，hard top-2 router 优先于完全学习式 gating。"
  disagreements:
    - "批判者认为 hard routing 的切换鲁棒性仍需实验验证。"
  risks:
    - "证据不足时不能承诺硬件资源收益。"
  evidence_gaps:
    - "外部文献调研默认关闭，需人工或联网工具补足。"
---

# 研究假设草案

## 调研依据

这里概述 `runs/<run_id>/idea/research/research_summary.v1.md` 的关键结论。

## 可证伪预测

这里说明哪些指标会被后续实验验证，以及什么结果会推翻该假设。

## 下一步实验建议

这里给出 Experiment Agent 可直接使用的变量、指标和最小消融建议。
