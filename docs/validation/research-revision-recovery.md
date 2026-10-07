# 研究方案修订与停止恢复检查

完整用户试用在项目主页新建独立对话和任务，通过实际总控、原生模型/工具与人工审核操作执行。研究首稿发现指标定义与现有代码入口错误，人工提出修改后又出现重复调研。修复覆盖实际断点：

- 默认研究提示、示例及评价表移除旧演示的固定模型、指标单位、噪声门槛和自动迭代假设；所有任务依据接入项目的当前代码。
- 人工修订以真实已发布候选开始，携带同任务完成 trace 中实际文献阅读的来源回执。代码读取不继承；独立审查、调用计数和通过状态重新开始，任务总账不清空。
- 检查原始工具回执和对应可见事件、摘要、项目、任务及方案绑定，拒绝跨任务、无已完成 trace、未确认工具或错误账本。
- 已明确停止的研究、实验设计和报告阶段在清理完成后提供新尝试；只允许用户明确重试，不自动重放中断调用。仿真与编码继续使用各自作业/分支清理检查。

验证：修订及现有原生参数/父版本检查 42 项通过，6 项依赖未配置的历史错误档案而跳过；恢复检查 20 项通过。真实本地首稿的 13 条来源记录保持阅读门槛通过，新修订 trace 实际记录 revision_seeded、重新读取当前仓库、初始审查未通过且新调用计数为零，累计用量保留。

检查点恢复保存原修改意见：从绑定任务的受保护材料中校验并读取，不丢失修订上下文；不匹配的调用或项目仍阻断。

完整端到端试用仍在进行，以上不代表实验结果、报告或全项目已验收。每次实际验收继续记录在本地 runs/product-user-journey-20261007-154655，私人论文与项目资料不入仓。

运行检查（真实档案路径由调用者提供）：

```sh
MARS_TEST_REVISION_ARCHIVE_ROOT=/absolute/path/to/completed/run PYTHONPATH=backend python -m pytest backend/tests/unit/test_revision_seed.py backend/tests/unit/test_focused_idea.py backend/tests/unit/test_native_document_arguments.py backend/tests/unit/test_idea_revision_parent.py -q -ra
PYTHONPATH=backend:. python -m pytest backend/tests/unit/test_stopped_draft_retry.py backend/tests/unit/test_run_recovery.py -q -ra
```
