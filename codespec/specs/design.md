# System Design — PIMC Research Lab

## 1. 设计原则

- **PIMC 单领域**：所有模块以 PIMC 任务对象建模。
- **AI Native**：显式版本化意图、规则、记忆、验证与审计。
- **增量优先**：新增能力优先通过 change-id 演进，而非重写。
- **文档驱动实现**：先 spec/design，再代码。

## 2. 总体架构

```text
[Docs/Core] + [Codebase Snapshot] + [Change Spec]
                │
                ▼
      Context Loader & Problem Framing
                │
                ▼
    Multi-Agent Orchestrator (5 Layers)
                │
      ┌─────────┼─────────┐
      ▼         ▼         ▼
 Web Retrieval  Debate   Execution Loop
      │         │         │
      └──────► Registry & Audit ◄──────┘
                │
                ▼
         Realtime Console
```

## 3. 阶段化 Pipeline

1. **Stage-0 Context Load**
   - 输入：core docs / formulas / codebase。
   - 输出：问题定义、假设、指标、风险摘要。
2. **Stage-1 Increment Plan**
   - 输入：change proposal + delta spec/design + tasks。
   - 输出：增量方案与实验计划。
3. **Stage-2 Implement & Run**
   - 输入：方案与实验配置。
   - 输出：代码改动、运行日志、指标结果。
4. **Stage-3 Analyze & Decide**
   - 输入：日志与指标。
   - 输出：分析结论、失败归因、是否迭代。
5. **Stage-4 Report & Reuse**
   - 输入：最终结果。
   - 输出：报告、决策记录、baseline 复用登记。

## 4. 模块职责 / 输入 / 输出 / 接口

### 4.1 Context Loader
- 职责：读取核心文档、核心公式、代码目录并生成上下文摘要。
- 输入：`docs/core/*`, `workspace/codebase/`。
- 输出：`problem_brief`, `assumptions`, `metrics`, `risks`。
- 接口：提供给 Orchestrator 的标准化 context object。

### 4.2 Change Manager
- 职责：管理增量任务与变更版本。
- 输入：`codespec/changes/<change-id>/*`。
- 输出：`delta_plan`, `task_graph`。
- 接口：向 Planning/Execution 下发变更约束。

### 4.3 Unified Web Retrieval Agent
- 职责：统一检索 paper/web/pdf 并落库。
- 输入：查询意图与关键词。
- 输出：`web_search_report`, `reference_registry`, `paper_notes`。
- 接口：写入 `knowledge/web_sources/{urls,pdfs,notes,parsed}`。

### 4.4 Multi-Agent Orchestrator
- 职责：调度 Idea / Experiment / Coding / Execution / Writing 五层 Agent。
- 输入：context + delta_plan。
- 输出：阶段产物与下一步动作。
- 接口：调用 Debate Runner、Execution Runner、Result Registry。

### 4.5 Debate Runner
- 职责：在路线冲突、结果反常等场景触发讨论。
- 输入：候选方案与证据。
- 输出：`discussion_transcript`, `consensus_synthesis`。
- 接口：回写 Orchestrator 决策流。

### 4.6 HITL Gate Controller
- 职责：关键节点人工确认与回滚。
- 输入：阶段结论与风险评估。
- 输出：`approved/rejected`, `rollback_to_stage`, `reason`。
- 接口：控制 Pipeline 状态机前进/回退。

### 4.7 Experiment Execution Loop
- 职责：执行实验、采集日志、汇总指标、失败归因、迭代优化。
- 输入：实验配置、代码版本、数据集。
- 输出：run logs、metrics summary、analysis、decision。
- 接口：写入 `workspace/experiments/` 与 `workspace/outputs/`。

### 4.8 Baseline & Result Registry
- 职责：登记历史结果、支持复用匹配与差异解释。
- 输入：实验结果与元数据。
- 输出：`baseline_match_report`, `reuse_decision`。
- 接口：写入 `knowledge/baseline_registry/` 与 `knowledge/decisions/`。

### 4.9 Realtime Console
- 职责：展示阶段状态、资源占用、实验进度、关键指标。
- 输入：Orchestrator 状态流与执行日志。
- 输出：可视化面板（MVP 可只读）。
- 接口：读取 registry 与 runtime events。

## 5. 数据流与存储

- 文档输入层：`docs/core/`, `codespec/specs/`, `codespec/changes/`
- 过程数据层：`workspace/experiments/`, `workspace/outputs/`
- 知识沉淀层：`knowledge/web_sources/`, `knowledge/baseline_registry/`, `knowledge/decisions/`
- 审计层：变更记录 + 验证记录 + 决策记录（按 change-id 关联）

## 6. MVP 最小可运行框架

1. CLI 读取 Context + Change 文档并输出结构化计划；
2. 执行一个占位实验任务并生成 run log 与 metrics 占位文件；
3. 将结果登记到 baseline registry；
4. 在控制台显示当前阶段与状态。
