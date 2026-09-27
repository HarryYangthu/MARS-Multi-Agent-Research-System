---
schema: run_log.v1
project: "PROJECT_ID_FROM_TASK"
agent: execution
run_id: "RUN_ID_FROM_HOST"
execution_phase: planned
status: interrupted
metrics:
  planned_experiments: 1
planned_experiments:
  - name: "REPLACE_WITH_APPROVED_EXPERIMENT"
    config: {}
# 仅为 schema 格式占位；实际计划必须使用宿主提供的输入摘要，绝不是作业收据。
fingerprint_hash: "sha256:0000000000000000000000000000000000000000000000000000000000000000"
is_mock: false
---

# 待执行计划格式参考

以上名称、数量、空配置和零摘要只是字段结构，不能用于执行或冒充收据。实际输出须使用当前 run、已批准的实验配置/seed、确切数量和宿主输入摘要。缺少必要命令、数据、环境或预算时明确阻断，不填占位成功。

本 Agent 不开作业。计划阶段 metrics 只记录计划数量，不填写实验测量、耗时或设备使用量。实际执行状态、有限测量值和真实证据哈希由批准后的执行器另行写入；计划批准不能证明实验完成。正文说明约束、来源及待验证内容。
