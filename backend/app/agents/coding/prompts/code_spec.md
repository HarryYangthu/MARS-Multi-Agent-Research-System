# Code Spec Prompt

将当前项目已批准的 experiment plan 落成可审核的真实代码改动，产出 `code_spec.v1`（YAML frontmatter + Markdown body）。先阅读项目合同、项目规则、基线接口及要修改的真实文件。

## 输出重点

- `files_changed` 逐文件列出实际落地的改动。允许范围、保护路径、接口、数据 shape/dtype 和依赖限制来自本项目；缺少必要信息时明确阻断写入，不假设其他项目的类名、函数签名或张量结构。
- `implementation_plan` 连接已批准实验参数与真正消费它们的代码。不要添加无人读取的配置，也不要修改受保护的基线、数据划分或评测器以制造达标。
- 修改使用受治理的代码工具；ToolRegistry 权限与 Gate 5 必须保留。仅有建议或 diff 不等于代码已落地，必须引用实际工具结果并检查最终 diff。
- `baseline_compat` 如实说明保护范围和接口兼容证据。适合时采用独立模块或显式开关保留对照，但不把某一项目的实现方式写成所有项目的要求。
- `tests` 采用本项目真实检查命令，验证接口、shape/dtype、有限输出和指标计算口径。不能把“模型一定改善”或“所有 seed 一定达标”当作代码正确性断言；尚未执行的测试标记 `skipped`，失败保留原始证据。
- `patch diff` 应最小、可审查、可回滚；涉及 tensor 操作时标注 shape。`new_dependencies` 说明理由，`rollback_notes` 给出恢复方法。

- `execution_jobs` 是机器可读的执行交付，每项 `name` 必须对应实验矩阵，`config` 只补充实际入口 `entrypoint`、`config_path` 或宿主登记的 `command_id` 等运行绑定。保留批准参数；明确随机种子、`max_iters` 和 `budget_unit`（steps/epochs），不得把步数换成轮数。每个配置必须存在且被真实代码消费。执行侧直接消费此清单，不再调用模型猜测命令。

## 反馈边界

只针对证据支持的代码问题修复。指标不达标可能源于假设、数据、协议或实现；不能默认归因为代码，也不能为了通过验收改写指标方向、阈值或测试集。迭代与目标服从任务合同和审批。
