# ACCEPTANCE_V2.md — V2 Development Gate

> V0 已完成 Dev E2E 验收。本文定义 V2 从开发态进入稳定态前必须满足的边界。
> 在本文所有条目通过之前,README/backend/frontend 仍保持 `V0 / v0.1.0` 稳定版本标识。

## 1. V2 目标

V2 的目标是在不破坏 V0 mock-first 主链路的前提下,补齐研究系统进入闭环迭代所需的四块能力:

1. Commander:自然语言入口选择、run 调度、状态查询、失败反馈回路。
2. Diagnosis + Evaluation:失败归因、指标评估、结构化 `diagnosis.v1` / `evaluation_report.v1` 产物。
3. Agent memory/context:Agent 自上下文、可审计 ContextPack、可回放 run state。
4. Posttrain pipeline:GRPO / preference pair / reward 训练流水线的最小可运行版本。

## 2. V2 必须保留的 V0 回归线

以下命令必须持续通过:

```bash
PYTHONPATH=backend:posttrain/src uv run mypy --strict backend/
PYTHONPATH=backend:posttrain/src uv run lint-imports
PYTHONPATH=backend:posttrain/src uv run pytest backend/tests/unit -q
PYTHONPATH=backend:posttrain/src uv run pytest backend/tests/integration -q
bash scripts/acceptance.sh
```

V2 新增功能不得移除以下 V0 行为:

- 无 LLM API key 时仍自动降级到 `mock_provider`。
- 无 GPU 时 Execution 仍可走 `mock_simulation`。
- 5 Agent pipeline 仍能产出 schema-valid markdown artifact。
- `runs/<id>/` 仍包含 input / context / idea / experiment / coding / execution / writing / hitl / events。
- Gate 5 仍挂在 `harness/tools/registry.py` dispatch 路径上。

## 3. V2 新增验收

### Commander

- [ ] 用户只输入自然语言目标时,Commander 能选择 pipeline / standalone entrypoint。
- [ ] 用户已有 hypothesis 时,Commander 能跳过 Idea 并从 Experiment 启动。
- [ ] Commander 工具调用链必须可单测,不得绕过 bridge 直接操作具体 Agent。
- [ ] 前端主控对话能展示半自动/全自动状态,并能查询 run 状态。

### Diagnosis + Feedback Loop

- [ ] 失败 run 能生成 schema-valid `diagnosis.v1`。
- [ ] Diagnosis 能给出 recommended target: experiment / coding / execution / writing。
- [ ] Feedback loop 能从 diagnosis 回拉到目标节点,生成新的 attempt 节点或 artifact version。
- [ ] 原始失败产物和重跑产物都保留在 run 目录,事件写入 `events/`。

### Evaluation

- [ ] evaluator registry 能按 run / artifact scope 生成 `evaluation_report.v1`。
- [ ] evaluation result 能进入 Writing Agent 上下文,不是只留在日志里。
- [ ] failed metrics 能被 Diagnosis 消费。
- [ ] mock-mode 下 evaluation 有确定性结果,便于 CI 回归。

### Agent Context + Memory

- [ ] 每个 Agent 的自上下文配置来自 `configs/agent_contexts/*.yaml`。
- [ ] ContextPack 与实际传给 LLM 的 message 来源一致,可以审计。
- [ ] KB 写入仍经过 sedimentation hook,不得绕过 schema/approval 直接写长期记忆。
- [ ] V2 不要求实现 MemoryRecord v2;相关统一记忆模型留到 V2。

### Posttrain

- [ ] `posttrain/` 提供最小训练入口、配置、数据格式和 dry-run。
- [ ] preference pair 来源可追溯到 `runs/<id>/hitl/*`。
- [ ] reward 至少覆盖 schema validity、baseline preservation、downstream metric 三类信号。
- [ ] 训练产物写入 `posttrain/checkpoints/` 或 `posttrain/reports/`,不得污染源码目录。
- [ ] 没有 GPU 时 posttrain dry-run 必须能在 CPU/mock 数据上完成。

## 4. 禁止升级为 V2 的情况

- 任一 V0 acceptance 命令失败。
- V2 功能只能依赖真实 API key、真实 GPU 或外部服务才能跑通。
- README/backend/frontend 已标 V2,但本文清单仍未通过。
- Commander、Diagnosis、Evaluation 产物绕过 schema/frontmatter。
- Bridge 或 harness 的依赖方向被破坏。

## 5. Release Checklist

- [ ] 本文所有 V2 新增验收通过。
- [ ] `scripts/acceptance.sh` 仍通过。
- [ ] 新增 V2 测试纳入 CI 或本地 release script。
- [ ] `docs/V2_AGENT_TODO.md` 中 release-blocking 项已关闭或迁入 V2 文档。
- [ ] 才允许把稳定版本标识从 `V0 / v0.1.0` 改为 `V2 / v1.0.0` 或约定版本。
