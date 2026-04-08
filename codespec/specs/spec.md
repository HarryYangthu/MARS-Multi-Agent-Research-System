# System Spec — PIMC Research Lab

## 1. 系统目标

构建一个面向 PIMC 单领域的 AI Native 研究系统，支持：
- 问题定义与建模假设管理；
- 文献/网页/PDF 检索与知识沉淀；
- 多 Agent 协作实验执行与结果分析；
- 历史结果复用与基线治理；
- 全流程可追溯审计。

## 2. 领域边界

- **In-Scope**：PIMC 研究、仿真、实验、分析、汇报。
- **Out-of-Scope**：非 PIMC 通用科研代理能力。

## 3. 核心输入（任务前必读）

- `docs/core/core_docs.md`（核心文档）
- `docs/core/core_formulas.md`（核心公式）
- `workspace/codebase/`（现有代码目录）

若三者不完整，系统必须显式标记“上下文不完整”。

## 4. 核心能力要求

### 4.1 编排与任务管理
- 所有任务按增量任务处理；
- 使用 `codespec/changes/<change-id>/` 承载 proposal / delta-spec / delta-design / tasks。

### 4.2 统一检索与知识存储
- 提供统一 Web 检索代理：文献、网页、PDF；
- 存储位置：`knowledge/web_sources/{urls,pdfs,notes,parsed}`。

### 4.3 多 Agent 协作与讨论
- 提供分层多 Agent 协作（Idea / Experiment / Coding / Execution / Writing）；
- 提供 Multi-Agent Debate 流程与结论沉淀。

### 4.4 HITL 与回滚
- 在关键 Gate 节点（方案定型前、大改前、大实验前、最终结论前）必须支持人工确认；
- Gate 不通过时支持回滚与失败原因记录。

### 4.5 执行闭环与复用
- 支持代码生成 + 本地读写；
- 支持实验执行、日志记录、指标汇总、失败归因、迭代优化；
- 支持 baseline registry 与结果复用决策。

### 4.6 可视化
- 提供实时可视化控制台（MVP 可先实现只读状态看板）。

## 5. 输入输出定义

### 5.1 输入
- 任务请求（目标、约束、资源预算）；
- 核心文档、公式、代码；
- 增量变更文档；
- 数据集与历史 baseline。

### 5.2 输出
- 研究计划、实验计划、运行日志、指标摘要、分析结论；
- 报告文档、决策记录、复用登记；
- 结构化审计痕迹（变更、验证、结论来源）。

## 6. 非功能要求

- 可审计：每一步均有输入、输出、责任边界；
- 可复现：实验配置、日志、指标可回放；
- 增量可演进：不破坏既有结构；
- 领域一致性：禁止漂移到非 PIMC。

## 7. MVP 验收标准

1. 文档控制面（README / AGENTS / spec / design）完整且一致；
2. 目录骨架覆盖研究闭环与知识沉淀路径；
3. 能跑通最小任务链路（读取上下文 → 生成计划 → 输出占位结果）。
