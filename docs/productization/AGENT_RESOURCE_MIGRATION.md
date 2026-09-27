# 下游 Agent 默认资源通用化

2026-09-28，基于 `1ddbbf2`。这是 B→E 的资源隔离切片，不是通用研究执行闭环或完整 E 验收。

## 问题与修改

Experiment、Coding、Execution、Writing 的默认 prompt 与 working_principles 原先写死 PIMC 模型、接口、RES 门限、仿真参数及失败原因。其中 Execution 文档要求缺数据时返回模拟结果，与真实执行约束冲突；其默认正文还将批准前计划混写为已经执行的结果。

四类 artifact 模板也由 `context/engine.py` 与 `compiler.py` 注入模型上下文，原先带有 `project: pimc`、已通过测试、GPU 使用量、耗时和成功指标示例。只改 prompt 不能消除这些默认约束。

本次修改：

- 八个阶段资源改为读取当前项目合同、项目规则和真实上游证据；参数、指标方向、目标、容差、汇总方式及允许范围必须注明来源。缺少必要信息时明确阻断相关动作或结论。
- 四个 artifact 模板改为明确的格式占位与未执行状态，仍通过已有 schema。模板数字仅为字段结构；不得直接用于作业或验收。Execution 使用当前实现的 `planned/interrupted` 计划语义，只填计划数量，不预填实验测量。
- 四个 Agent 启用现有 `project_knowledge_enabled`。已有 `BaseAgent.build_context`、`load_project_knowledge` 和 folder context 负责加载、按 run 冻结及校验，无新上下文系统，无额外目录扫描。未声明 `knowledge_file` 的项目不会加载别的项目知识；显式关闭 `project_references` 仍有效。
- PIMC 的领域知识、接口保护、指标口径继续放在 `projects/pimc/context/public_context.md`；新增适用边界说明。旧诊断阈值已在项目 `diagnostics.yaml` 中，无需复制到全局资源。未修改 `projects/pimc/AGENTS.md`。没有保留旧文档中“必然改善”“固定失败归因”或缺依赖伪造成功的指令。
- Writing 的执行指标摘要改为汇总所有有限数值并保留原始行号/身份。不按 RES 排名，不猜指标方向或目标达成；非数值、布尔值、无穷与 NaN 被明确计数排除。前五行按源顺序展示，其余给出完整文件指针。

项目知识和默认模板变化会改变新 invocation 的实际输入。原 checkpoint 不应跳过现有指纹校验强行恢复。旧资源可从 `1ddbbf2` 查看，已有 run 中的知识快照不重写。

## 验证

使用已有 Python 环境：

```sh
PYTHONPATH=backend python -m pytest backend/tests/unit/test_generic_pipeline_resources.py backend/tests/unit/test_skills_registry.py backend/tests/unit/test_focused_idea.py backend/tests/unit/test_public_research_context.py backend/tests/schema/test_template_files_pass.py backend/tests/unit/test_commander_agent_feedback.py -q
PYTHONPATH=backend python -m mypy --strict backend/app/bridge/agent_runner.py backend/app/agents/experiment/agent.py backend/app/agents/coding/agent.py backend/app/agents/execution/agent.py backend/app/agents/writing/agent.py backend/tests/unit/test_generic_pipeline_resources.py
```

测试仅构建真实临时项目、读取真实默认资源、编译实际请求消息、校验 schema 和解析明确标为人工编写的指标输入。没有 provider、工具、实验或服务成功替身，也没有模型请求或研究作业。

结果：上述六组测试最初联跑 64 项通过；随后补充四项“无 knowledge_file 项目”检查，新资源测试文件 20 项全部通过，累计覆盖 68 个独立测试。6 个 Python 文件 strict mypy 通过，`git diff --check` 通过。另执行 `test_release_manifest.py`，24 项通过，既有运行资源仍被精确清单覆盖。

## 未完成边界

资源中的阻断说明是对 Agent 的约束，不等同于任务合同已经驱动所有宿主检查。命令执行、写范围强制检查、预算、指标验收及 UI/CLI 统一生命周期仍需后续接入。此切片不证明缺信息时模型总会正确停止，也不证明任何第二领域研究已经完成；正式验收必须使用真实模型、工具、隔离候选及可审计实验。

默认 Commander/Idea、其他项目配置或历史评估资料不属于本切片。公开发行仍受现有内容和历史门禁约束；未放宽门禁，也未将 PIMC 私有项目资源加入发行或 runtime seed 清单。
