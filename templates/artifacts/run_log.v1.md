---
schema: run_log.v1
project: pimc
agent: execution
upstream_artifact: code_spec.approved.md
run_id: "2026-05-04T2310_pimc_ablation_run3"
batch_size: 512
gpu_used: ["L40S:1", "L40S:2"]
duration_seconds: 3420
status: completed
metrics:
  RES: -42.3
  PIM: -18.7
  APE: 23.6
fingerprint_hash: "sha256:abcd1234deadbeef0000aaaa"
is_mock: false
---

# 执行日志

正文使用中文总结执行批次、关键指标、错误数量、异常实验、资源占用和是否达到目标。run_id、metric、fingerprint_hash、文件路径等技术字段保持原样。
