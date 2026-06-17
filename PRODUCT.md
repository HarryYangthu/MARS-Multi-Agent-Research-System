# PRODUCT.md — MARS 产品定义

> 本文按当前代码重写。它描述的是现在仓库里的产品形态和稳定边界,不是早期 V0 规划稿。

## 1. 一句话定位

MARS 是一个研究型多 Agent 工作台。它把“研究问题 → 假设 → 实验方案 → 代码改动 → 仿真结果 → 报告”变成可审计、可回放、可评价、可沉淀的闭环。

首个落地项目是 `projects/moe-pimc/`: PIMC for FDD Massive MIMO under beam/layer switching。

当前产品有两个层次:

- **研究工作流层**: Commander + 5 个专业 Agent,支持聊天入口、Pipeline、Standalone、任意入口继续。
- **治理底座层**: Schema、Tools、Gate、HITL、Context、Memory、Evaluation、Observability、Mock fallback。

## 2. 当前用户与核心价值

V0/V1 当前仍以单研究员工作流为主,不是多租户 SaaS。目标用户是算法研究员,尤其是已有代码仓、已有 baseline、需要反复设计和验证实验的人。

核心价值不是“让 LLM 写一段代码”,而是:

1. 每个中间产物都有结构化 schema,人写和 Agent 写对下游等价。
2. 每一步都能人工 review/approve,并留下审计记录。
3. 每次 run 都完整写入 `runs/<run_id>/`,能恢复 UI、复盘失败、构造后训练数据。
4. 工具调用统一经过 ToolRegistry,写操作有权限、Gate、rollback 和审计。
5. 运行结果、评价结果、人工反馈会进入 Memory/KB,但需要治理后才影响未来上下文。

## 3. 产品入口

### 3.1 Commander 聊天入口

Commander 是当前代码里新增的主控入口,位于 `backend/app/bridge/commander.py`。

它做三件事:

- 识别用户意图:完整 pipeline、从某个 Agent 进入、查询 run 状态、触发 feedback loop。
- 通过 bridge-only 工具调用现有系统能力,例如 `run.create`、`run.start`、`artifact.review`、`metrics.evaluate`。
- 维护对话状态,真实 LLM 不可用时走确定性 mock path。

Commander 不替代 5 个专业 Agent,它是产品操作层的调度员。

### 3.2 Pipeline 模式

完整研究链路:

```text
Idea -> Experiment -> Coding -> Execution -> Writing
```

每个节点完成后进入 `WAITING_REVIEW`,用户 approve 后才向下游推进。Execution 节点在 artifact approve 后还会通过 `execution.batch_runner` 启动批量仿真。

### 3.3 Standalone / 任意入口

每个 Agent 都可以单独调用。用户也可以从中间节点进入,例如:

- 已有假设:从 Experiment 开始。
- 已有实验方案:从 Coding 开始。
- 只有运行结果:从 Writing 开始。

规则是:只要输入 artifact 的 YAML frontmatter 通过对应 JSON Schema,它就和 Agent 生成的 approved artifact 等价。

### 3.4 Run Detail / Workbench

前端围绕 run 展开,主要面向这些操作:

- 查看 Pipeline 节点状态和事件流。
- 审查 artifact 版本、diff、schema/evaluation 结果。
- 查看工具调用、pending approval、rollback 记录。
- 查看 execution logs、curves、metrics、diagnosis。
- 查看 Context Workbench: manifest、segment、token 预算、raw refs、污染诊断。
- 查看 Agent context / memory / self-evolution levers。

## 4. Agent 产品定义

### 4.1 Commander

**目的**:把自然语言操作转成受控的 run/artifact/feedback 工具调用。

**位置**:`bridge/commander.py`,配置在 `configs/agents.yaml::commander`。

**工具**:`run.create`、`run.start`、`run.status`、`run.feedback_loop`、`artifact.read`、`artifact.review`、`metrics.evaluate`、`diagnosis.failure_analysis`、`user.approval`。

**特点**:Bridge 层 Agent,不进入五节点研究拓扑,但能驱动 Orchestrator。

### 4.2 Idea Agent

**目的**:把研究问题转成可验证假设。

**输入**:用户问题、项目上下文、文献/方法 KB、baseline/代码信息。

**行为**:

- 加载 Idea 自上下文和研究配置。
- 检索 local docs / KB / baseline,可选 arXiv。
- 默认开启 debate。
- 输出 `proposal.v1`,并附带 research provenance 和质量 warning。

**输出**:`idea_proposal.vN.md`,schema 为 `proposal.v1`。

### 4.3 Experiment Agent

**目的**:把假设转成实验方案和消融矩阵。

**行为**:

- 定义变量、指标、ablation。
- 调用 `knowledge.baseline_match` 和 `knowledge.experiment_memory`。
- 产出 baseline reuse 决策字段。

**输出**:`experiment_plan.vN.md`,schema 为 `experiment_plan.v1`。

### 4.4 Coding Agent

**目的**:基于实验方案和项目代码生成 code spec、patch、测试计划。

**行为**:

- 读取受控 project repo。
- 生成或规范化 patch。
- 通过 ToolRegistry 做 Gate 5 baseline 兼容性检查。
- 可运行 lint/test 工具。
- 支持 research Python 和 C production 两条语言路径。
- 配置支持 post-training endpoint/local vLLM 加载,但不在本仓训练模型。

**输出**:`code_spec.vN.md`、`patch.diff`、测试/工具记录。

### 4.5 Execution Agent

**目的**:运行实验,产出 logs、curves、metrics 和 run log。

**行为**:

- 支持 `mock` / `pim_cpu` / local callback 风格执行。
- 批量执行由 `execution.batch_runner` 统一经过 ToolRegistry。
- 写 `metrics.json`、`curves/`、per-experiment `run_log_*.v1.md`、fingerprint。
- 无 GPU 时 mock simulation 仍保持 artifact/schema 等价。

**输出**:`run_log.v1`、`metrics.json`、`batch_summary.json`、曲线和日志。

### 4.6 Writing Agent

**目的**:把 proposal、plan、code、metrics、run logs 综合成报告。

**行为**:

- 查询 methodology 和 run_archive。
- 默认开启 reviewer-style debate。
- 强调 chain refs、metric support、limitation honesty。

**输出**:`report.v1`。

## 5. Schema 与 Artifact

当前稳定 artifact schema:

- `proposal.v1`
- `experiment_plan.v1`
- `code_spec.v1`
- `run_log.v1`
- `report.v1`
- `diagnosis.v1`
- `feedback_packet.v1`
- `evaluation_report.v1`

所有 Agent 产物都采用:

```text
YAML frontmatter + markdown body
```

通过 `harness/schema/validator.py` 校验后写入 `runs/<run_id>/<agent>/`。版本规则:

- `*.v1.md`, `*.v2.md`: Agent 或人工产生的版本。
- `*.approved.md`: 下游唯一默认消费版本。
- `*.eval.md`: 对应版本的评价报告。

## 6. HITL 与 Gate

系统有两层人工参与:

### 6.1 高频 HITL Review

每个 Agent 输出后进入 review session。用户可以 comment、edit、approve、reject、regenerate。审计写入 `runs/<run_id>/hitl/` 和事件流。

### 6.2 系统 Gate

Gate 用于阻止高风险行为:

- Gate 1 `plan_finalized`
- Gate 2 `large_refactor`
- Gate 3 `experiment_launch`
- Gate 4 `conclusion_output`
- Gate 5 `baseline_compatibility`

其中 Gate 5 是产品安全核心:它不在 RunGraph 节点边界上,而在 `ToolRegistry.dispatch()` 中拦截写工具。只要 patch 或文件写入违反项目 `AGENTS.md` 的 baseline 保护规则,工具直接返回 blocked。

## 7. Tools V1

Agent 调工具必须经过 MARS 稳定工具名,不直接调 MCP 或外部命令。

主要工具族:

- `search.*`: local docs、arXiv、web search。
- `knowledge.*`: KB query、baseline match、code assets、methodology、run archive、document ingest。
- `code.*`: repo read、patch、write、delete、rollback、lint、test。
- `execution.*`: simulation runner、batch runner、log streamer、metrics collector。
- `run.*` / `artifact.*` / `metrics.*` / `diagnosis.*`: Commander bridge-only 工具。

每次 dispatch 产生:

- `events/tool_events.jsonl`
- `events/tool_calls.jsonl`
- `coding/tool_applications/<call_id>.json`
- trace span

写工具会保存 rollback snapshot。`code.delete_file` 和 large refactor 需要审批;Gate 5 block 不允许人工绕过。

## 8. Context 与 Memory

### 8.1 Context Engineering

每次 LLM 调用前都会把上下文编译成受审计的 segment:

- system: Agent role、硬约束、schema 模板。
- project: 项目规则、domain、baseline、历史摘要。
- task: 当前用户任务、上游 approved artifact、KB 命中、recent dialog。
- tool: 当前 Agent 可用工具。

Context V1 会写 `context_manifest.v2.*.json`,记录 token budget、render order、messages preview、diagnostics、raw refs。大块 tool output 写入 `runs/<run_id>/context/raw/`,prompt 里只注入 compact observation 和 raw ref。

### 8.2 KB 四区

逻辑分区仍是:

- `literature`: 文献和上传资料。
- `methodology`: 方法、prompt、rubric、报告经验。
- `code_assets`: 可复用代码资产和代码规则。
- `run_archive`: 历史运行、fingerprint、baseline 结果。

当前 backend 可使用 file store,并保留 Chroma/向量后端替换点。

### 8.3 Governed Memory

MemoryRecord v2 支持:

- `semantic` / `episodic` / `procedural`
- `confidence` / `salience` / `ttl_days`
- `eval_status`
- `approved`
- `supersedes` / `superseded_by`
- mock 隔离策略

待沉淀内容不会自动污染未来上下文。run-local candidate 必须被 approve 后才进入 Agent long-term memory;连续负反馈会把 memory 标记为 stale。

## 9. Evaluation 与自进化

评价层位于 `harness/evaluation/`,不是某个具体 Agent。

当前已经实现的 evaluator:

- `contract.schema_validity`
- `contract.provenance`
- `artifact_quality.rubric`

评价输出是 `evaluation_report.v1`,包含 decision、score、findings、recommended actions 和 evidence refs。run 完成时聚合成 `events/evaluation_scorecard.json`。

自进化是人工审核模式:

1. run 内写 episode memory 和 memory candidates。
2. Evaluation scorecard / findings 暴露成 self-evolution levers。
3. 用户可创建 prompt/few-shot/eval mutation proposal。
4. mutation 先过 deterministic eval gate。
5. 用户 approve 后才修改 Agent context 文件并同步到 governed memory。

V0/V0.5 允许导出后训练候选数据,不在仓内启动 GRPO 或 reward model 训练。

## 10. Observability

MARS 的可观测性围绕 run,不是只围绕服务进程。

必须回答:

- run 现在在哪个节点。
- 为什么暂停、失败或等待审核。
- 哪个 Agent/tool/gate/human action 导致了当前状态。
- 能不能从 `runs/<run_id>/` 恢复 UI 和审计链。

持久信号:

- events: `runs/<id>/events/*.jsonl`
- trace: `runs/<id>/context/trace_manifest.v1.json`
- metrics: `execution/metrics.json`
- logs: `execution/logs/`
- HITL audit: `hitl/review_log.jsonl`
- evaluation scorecard: `events/evaluation_scorecard.json`

实时信号通过 EventBus/WebSocket 推前端。LangSmith 是可选旁路,file trace 是默认事实来源。

## 11. Mock-first 与真实执行

当前产品坚持 mock-first:

- 无 LLM key: fallback 到 `mock_provider`。
- 无 GPU: fallback 到 `mock_simulation`。
- 无外部网络: local docs / file KB / mock debate 仍可跑通。

Mock artifact 必须与真实 artifact 在 schema 层等价,这样下游 Agent、Evaluation、KB、Writing 都不用区分 mock/real。

真实路径包括:

- DeepSeek/OpenAI/Anthropic/Gemini/Qwen/local vLLM/custom endpoint 等 provider 插拔。
- Coding post-training endpoint 或 local vLLM 加载。
- 真实项目代码通过 `projects/<name>/repo_link.yaml` 指针接入,不复制进 MARS 仓。
- Execution 可扩展到真实 GPU runner。

## 12. 当前非目标

- 不做多租户 SaaS。
- 不把真实研究代码 commit 进 MARS 仓。
- 不在 V0/V0.5 内实现 GRPO 训练流水线。
- 不让 Agent 绕过 Bridge/ToolRegistry 直接执行危险写操作。
- 不让未审核 memory candidate 自动进入未来 prompt。
- 不依赖外部 telemetry 才能回放 run。

## 13. 产品指标

当前指标按三类看:

### 13.1 合约与治理

- Schema 首次合规率。
- Gate 误触发/漏触发率。
- Tool dispatch 审计完整率。
- Context manifest 覆盖率。

### 13.2 研究效率

- Pipeline e2e 完成率。
- Baseline 复用召回率/精度。
- 多实验并发成功率。
- 从 failed metrics 到 diagnosis/feedback loop 的修复成功率。

### 13.3 资产沉淀

- 每次 run 的 9 个核心目录完整性。
- KB/Memory 新增可复用资产数。
- Evaluation report 和 scorecard 覆盖率。
- 可导出的 post-training candidate 数量和证据完整性。

## 14. 关键代码索引

```text
backend/app/main.py                         FastAPI app + router 注册
backend/app/bridge/commander.py             聊天主控入口
backend/app/bridge/orchestrator.py          Run lifecycle 编排
backend/app/bridge/workflow_service.py      Pipeline/Standalone RunGraph 构造
backend/app/agents/base.py                  Agent loop + schema repair + tool gather
backend/app/harness/tools/registry.py       Tool dispatch + Gate 5 + audit + trace
backend/app/harness/context/engine.py       Context V1 compiler + manifest writer
backend/app/harness/kb/models.py            MemoryRecord v2
backend/app/storage/self_evolution_store.py 自进化候选和 mutation 审核
backend/app/harness/evaluation/             Evaluation reports + scorecard
backend/app/harness/observability/          Event envelope + trace/langsmith sink
backend/app/execution/                      mock/local execution runners
configs/agents.yaml                         Agent/model/tool/loop 配置
configs/tools.yaml                          工具权限和 schema 控制面
configs/evaluation.yaml                     评价策略
configs/memory.yaml                         Memory 写入与选择策略
configs/observability.yaml                  trace/event sink 配置
```
