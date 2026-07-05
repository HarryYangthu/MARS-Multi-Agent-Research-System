# MARS Evaluation Harness

> 当前文档描述新的 MARS 评测实现:先内置在 MARS 仓内,可单独启动评测程序,以 `runs/<run_id>/` 为事实源,输出评测报告,并沉淀 self-evolution 候选项。

![MARS Evaluation Harness Overview](./mars_evaluation_harness_overview.svg)

## 1. 定位

MARS Evaluation Harness 不是一个 Agent,也不是前端分数面板。它是 `backend/app/harness/evaluation/` 下的 run-centered 评测系统:

- **只读事实源**:`runs/<run_id>/` 中的 artifact、events、context、tool audit、metrics、HITL、memory。
- **不依赖具体 Agent**:不 import `agents/`、`bridge/`、`api/`。
- **可单独启动**:CLI 可以评已有 run,也可以把多个 run 当 trial 聚合成 suite report。
- **服务自进化**:findings 会转成 pending review 的 self-evolution candidates,后续可进入 memory、prompt mutation、rubric 更新或 regression task。

核心判断:

```text
Transcript helps debugging.
Outcome decides whether the system worked.
```

## 2. 三阶段能力

| 阶段 | 能力 | 入口 | 输出 |
|---|---|---|---|
| V0 | Replay 一个已有 run | `scripts/evaluate_run.py` | run report、scorecard、self-evolution candidates |
| V1 | Replay 多 run 或 live 多 trial | `scripts/run_evaluation_suite.py` | suite report、pass@k、pass^k、suite scorecard |
| V2 | 自进化与校准资产化 | `self_evolution.py`、`calibration.py` | evolution export、human calibration samples、drift report |

V0/V1/V2 都已在仓内实现。Live suite 通过 MARS API 创建 fresh run,自动推进 trial,完成后复用同一套 replay evaluator。

## 3. 文件布局

```text
backend/app/harness/evaluation/
├─ run_evaluators.py      # run-level code / LLM / human grader stack
├─ run_report.py          # replay 一个 run 并写报告
├─ suite_report.py        # 多 run/trial 聚合
├─ self_evolution.py      # findings -> self-evolution export
├─ calibration.py          # human calibration sample/export and drift report
├─ suites.py              # suite YAML loader
├─ artifacts.py           # .eval.md 读写,scorecard 读写
├─ aggregation.py         # scorecard aggregation
└─ evaluators/            # artifact-level evaluators

configs/evaluation_suites/
└─ mars_run_replay_v0.yaml

scripts/
├─ evaluate_run.py
├─ run_evaluation_suite.py
└─ export_evaluation_calibration.py
```

## 4. 评测对象

MARS 不把 Agent 当单轮函数测。一次评测包含:

| 对象 | MARS 来源 | 评什么 |
|---|---|---|
| Task | suite YAML + user request | 输入、期望结果、grader、指标 |
| Trial | 一个 `runs/<run_id>/` | 一次完整执行 |
| Trajectory | `events/` + `context/` + tool audit | 路径是否可审计 |
| Outcome | approved artifacts + metrics + final state | 任务是否真的完成 |
| Report | `events/evaluation_report.md` | 给人看的评测结论 |
| Scorecard | `events/evaluation_scorecard.json` | 给程序消费的结构化结果 |

## 5. Grader 栈

当前 run-level V0/V1/V2 已内置三层 Grader。三层共享同一个 `EvaluationReport` schema,但决策语义不同:

| Layer | 当前实现 | 决策权 | 主要输出 |
|---|---|---|---|
| Code / deterministic | Python evaluator、schema 校验、run 文件检查、tool audit 检查 | 可以 `warn/revise/block` | 硬约束、CI gate、回归信号 |
| LLM rubric | `llm_rubric.advisory` | 默认 advisory,不单独阻塞 | 主观质量分、弱证据发现、校准样本 |
| Human review | `human_review.queue` + `evaluation_human_review_queue.jsonl` + `human_review_labels.jsonl` | Gold label,通过 HITL/Gate 策略推广后才阻塞 | 人工结论、校准标签、高风险审查 |

确定性 grader 稳定、便宜、适合 CI:

| Evaluator | 作用 |
|---|---|
| `run_integrity.required_outcome` | 检查 run 目录、`run_meta.json`、required artifacts、required event files |
| `trajectory.audit_coverage` | 检查 context manifest、tool audit、agent state trace、terminal state |
| `outcome.execution_and_report` | 检查 execution metrics、batch summary、report chain refs |
| `gate_behavior.expected` | 检查 suite 期望 Gate 是否在 tool audit 中出现 |
| `multi_agent.collaboration_quality` | 检查 entrypoint/stage routing、handoff metadata、重复工具调用、failure/limitation acknowledgement |
| artifact evaluators | 复用已有 `contract.schema_validity`、`contract.provenance`、`artifact_quality.rubric` |

LLM rubric 负责确定性检查不擅长的主观维度:

| Rubric dimension | 评什么 |
|---|---|
| `task_completion_clarity` | 结论是否明确回答任务 |
| `evidence_grounding` | 结论是否绑定 artifact、metrics、chain refs |
| `limitation_awareness` | 是否交代风险、限制、失败模式 |
| `escape_hatch` | 证据不足时输出 `INSUFFICIENT_INFO`,避免模型硬猜 |

人工审查负责两类事情:

| Human lane | 触发条件 | 作用 |
|---|---|---|
| Calibration | LLM rubric 报告默认进入队列 | 计算 evaluator-human agreement,低于 0.85 时复查 rubric/prompt |
| High-risk review | deterministic `revise/block/fail` 或 high/blocker finding | 判断是真系统问题、suite fixture 问题,还是应转 regression |

最终 `overall_decision` 和主 `overall_score` 只聚合非 advisory 报告。LLM / human 的分数写入 `advisory_score`,并进入人工队列和校准报告。真正 blocker 必须来自 deterministic evaluator 或 Gate。

## 6. CLI 用法

评一个已有 run:

```bash
PYTHONPATH=backend uv run python scripts/evaluate_run.py \
  --run-id 2026-07-04T1617_staticpimc
```

输出写回 run:

```text
runs/<run_id>/events/evals/run.v1.*.eval.md
runs/<run_id>/events/evaluation_report.md
runs/<run_id>/events/evaluation_scorecard.json
runs/<run_id>/events/evaluation_self_evolution_candidates.json
runs/<run_id>/events/evaluation_human_review_queue.jsonl
```

聚合多个 trial:

```bash
PYTHONPATH=backend uv run python scripts/run_evaluation_suite.py \
  --run-id run_a \
  --run-id run_b \
  --suite configs/evaluation_suites/mars_run_replay_v0.yaml
```

启动 live trial:

```bash
PYTHONPATH=backend uv run python scripts/run_evaluation_suite.py \
  --live \
  --suite configs/evaluation_suites/mars_live_smoke_v0.yaml
```

完整 pipeline 也可以 live 运行,但应使用项目专属 suite 和更长 timeout;快速 CI smoke 推荐 `mars_live_smoke_v0.yaml`。

输出:

```text
evaluation_runs/<timestamp>_<suite>/
├─ report.md
├─ report.json
├─ scorecard.json
└─ self_evolution_export.jsonl
```

导出人工校准样本:

```bash
PYTHONPATH=backend uv run python scripts/export_evaluation_calibration.py \
  --run-id <run_id> \
  --output /tmp/eval_calibration_samples.jsonl
```

标注 `human_decision` 后生成校准报告:

```bash
PYTHONPATH=backend uv run python scripts/export_evaluation_calibration.py \
  --score-labels /tmp/eval_calibration_samples.jsonl \
  --output /tmp/eval_calibration_report.json
```

## 7. Suite 配置

`configs/evaluation_suites/mars_run_replay_v0.yaml` 定义默认 replay suite:

```yaml
id: mars_run_replay_v0
mode: replay

expected_outcome:
  required_dirs: [input, context, idea, experiment, coding, execution, diagnosis, writing, hitl, events, memory]
  required_artifacts: []
  required_event_files:
    - events/evaluation_events.jsonl
  require_context_manifest: true
  require_tool_audit: true
  require_execution_metrics: false
  require_report_chain_refs: false
  expected_gates: []
  expected_entrypoint: null
  expected_stages: []

graders:
  - run_integrity.required_outcome
  - trajectory.audit_coverage
  - outcome.execution_and_report
  - gate_behavior.expected
  - multi_agent.collaboration_quality
  - llm_rubric.advisory
  - human_review.queue
```

项目级 suite 可以收紧要求,例如强制完整 pipeline 必须有五个 approved artifacts、`execution/metrics.json` 和 writing chain refs。

## 8. Score 语义

MARS 使用 decision + score,不是只看平均分:

| Decision | 含义 |
|---|---|
| `pass` | 可继续 |
| `warn` | 可继续,但有低/中风险发现 |
| `revise` | 需要人工或 Agent 修订 |
| `block` | 硬约束失败,不能被平均分掩盖 |
| `fail` | suite / benchmark 级失败 |

Scorecard 里同时保留两套分数:

| 字段 | 来源 | 用途 |
|---|---|---|
| `overall_score` | 非 advisory grader | CI、回归、suite pass/fail |
| `advisory_score` | LLM / human advisory grader | 主观质量观察、人工校准 |
| `grader_counts` | 全部报告 metadata | 判断 code / llm / human 覆盖是否完整 |

Suite 层额外输出:

```text
pass_rate = pass_or_warn_trials / total_trials
pass@k = k 次里至少一次成功的概率估计
pass^k = k 次全部成功的稳定性估计
```

研究辅助场景看 `pass@k`;无人值守链路更看 `pass^k`。

## 9. Self-Evolution 闭环

![MARS Evaluation Self-Evolution Loop](./mars_evaluation_self_evolution_loop.svg)

评测发现不会直接污染未来上下文。它们先进入 pending review:

```text
finding
  -> evaluation_self_evolution_candidates.json
  -> evaluation_human_review_queue.jsonl
  -> suite self_evolution_export.jsonl
  -> human review
  -> memory / prompt mutation / rubric update / regression task
  -> deterministic eval gate
  -> approved change
```

Human calibration 是另一条输入线:

```text
eval reports
  -> calibration samples
  -> human labels
  -> calibration report
  -> judge prompt/rubric review when agreement_rate < 0.85
```

默认 lever:

| Finding category | Suggested lever |
|---|---|
| `context`, `trajectory`, `tool_audit` | harness / observability regression |
| `claim_support`, `report` | writing prompt or rubric mutation |
| `outcome` | task fixture or agent feedback |
| `run_integrity` | run-store contract regression |

## 10. 设计边界

- Evaluation Harness 放在 MARS 仓内,因为它强依赖 `runs/`、schema、ToolRegistry、Gate、Context Manifest。
- 未来可以拆 `mars-evals` 仓,但只放大型 benchmark data、golden runs、跨模型报告;核心 evaluator 仍由 MARS 暴露稳定 CLI/API。
- V0/V1/V2 评测不替代 HITL 和 Gate。Gate 是阻塞机制;Evaluation 是复盘、回归、质量度量和自进化输入。
