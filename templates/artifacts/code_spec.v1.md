---
schema: code_spec.v1
project: moe-pimc
agent: coding
upstream_artifact: experiment_plan.approved.md
target_lang: python
baseline_compat:
  preserved: true
  rationale: "保持 forward(x, stream_label) 接口不变；新增 Paper_Router_v2，并与现有 Paper_Total_0327 并行保留。"
files_changed:
  - path: "libs/Model.py"
    type: modified
    risk: medium
  - path: "tests/test_router_v2.py"
    type: added
    risk: low
new_dependencies: []
test_coverage:
  unit_tests_added: 3
  baseline_smoke_test: pass
---

# 代码规格

正文使用中文描述补丁目标、涉及文件、兼容性保护、测试覆盖、风险等级与回滚方式。函数签名、类名、文件路径、命令和指标名保持原样。
