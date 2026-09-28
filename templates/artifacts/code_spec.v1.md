---
schema: code_spec.v1
project: "PROJECT_ID_FROM_TASK"
agent: coding
target_lang: python
baseline_compat:
  preserved: false
  rationale: "尚未检查；实际输出必须依据项目保护规则、接口和最终 diff 填写，不能从模板宣称兼容。"
files_changed: []
new_dependencies: []
test_coverage:
  unit_tests_added: 0
  baseline_smoke_test: skipped
---

# 代码规格格式参考

以上是未实施、未验证的字段结构，语言也须按实际项目填写。仅记录真实工具已落地的文件变更与实际检查；未执行的检查保持 skipped。缺少必要项目规则、源码或接口信息时明确阻断写入，不从模板补造文件、测试通过或兼容结论。

正文用中文描述改动目的、允许与保护范围、实际 diff、参数接口、测试证据、必要依赖、风险和回滚方式。函数名、路径、命令等技术标识保持原样。
