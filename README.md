# PIMC Research Lab

PIMC Research Lab 是一个 **仅面向 PIMC（Passive Intermodulation Cancellation）** 的 AI Native Agent 系统。
项目参考 Claw AI Lab 的“分层协作 + 阶段化 Pipeline”思想，但**不做通用科研平台**，只服务 PIMC 研究、建模、仿真、实验执行与结果沉淀。

---

## 1. 项目定位

- **单领域优先**：所有模块、提示词、数据结构与评估口径均围绕 PIMC。
- **文档先行**：先维护 AGENTS / spec / design / README，再做实现。
- **增量优先**：所有任务都通过 `codespec/changes/*` 作为增量任务推进。
- **AI Native**：版本化管理意图、规则、记忆、验证与审计链路。

---

## 2. 核心能力（必须保留）

1. 任务前必须读取本地三类基础上下文：
   - `docs/core/core_docs.md`
   - `docs/core/core_formulas.md`
   - `workspace/codebase/`
2. 新场景背景分析与增量任务处理。
3. 统一 Web 检索代理（文献/网页/PDF）与本地存储。
4. 多层多 Agent 协作调度。
5. Multi-Agent Debate 讨论模式。
6. HITL Gate 与回滚机制。
7. 代码生成与本地代码仓读写。
8. 实验执行、迭代优化、结果分析闭环。
9. 历史结果复用与 baseline registry。
10. 实时可视化控制台。

---

## 3. 目录结构

```text
.
├── AGENTS.md
├── README.md
├── codespec/
│   ├── specs/
│   │   ├── spec.md
│   │   └── design.md
│   └── changes/
├── docs/
│   └── core/
│       ├── core_docs.md
│       └── core_formulas.md
├── backend/
│   ├── agent/pimclab/
│   └── services/
├── knowledge/
│   ├── web_sources/{urls,pdfs,notes,parsed}/
│   ├── baseline_registry/
│   └── decisions/
└── workspace/
    ├── codebase/
    ├── datasets/
    ├── experiments/
    └── outputs/
```

---

## 4. 启动方式（MVP 骨架）

> 当前阶段以文档与骨架为主，CLI/服务逻辑将按增量任务逐步补全。

### 4.1 初始化检查

- 检查核心输入文档与目录是否存在；
- 检查 `codespec/changes/<change-id>/` 是否包含 proposal/spec/design/tasks。

### 4.2 最小开发循环

1. 先更新 `codespec/changes/<change-id>/` 任务定义；
2. 再更新 `spec.md` / `design.md` 的增量差异；
3. 最后实现最小代码与验证脚本。

---

## 5. 开发流程（阶段化 Pipeline）

1. **Context Load**：读取核心文档、公式、代码目录。
2. **Problem Framing**：形成问题定义、约束、指标。
3. **Plan & Debate**：实验设计与争议讨论。
4. **Implement**：最小增量代码改动。
5. **Execute**：运行实验并采集日志。
6. **Analyze & Reuse**：分析、登记、复用 baseline。
7. **Report**：生成可审计报告与决策记录。

---

## 6. 非目标（当前阶段）

- 不实现完整的高复杂度 PIMC 取消算法库；
- 不扩展到非 PIMC 领域研究；
- 不做大规模系统重写。
