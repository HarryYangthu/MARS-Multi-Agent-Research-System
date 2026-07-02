# DESIGN.md — MARS 当前架构设计

> 本文按当前代码重写。目标是让新贡献者能从文档直接定位到实现,并理解哪些边界是产品约束,哪些是可替换实现。

## 1. 当前架构总览

MARS 现在是一个 mock-first、run-scoped、schema-governed 的研究工作台。

```text
frontend
  -> api
  -> bridge                 产品编排: Commander / Orchestrator / workflow / diagnosis
  -> agents                 5 个专业 Agent
  -> harness                Agent-agnostic 机制: schema/tools/context/kb/eval/gates/runtime/obs
  -> storage + projects     runs/ artifacts/ agent_contexts/ repo_link/ knowledge
```

核心设计原则:

1. **Run 是事实源**:`runs/<run_id>/` 存 artifact、context、events、trace、metrics、HITL、evaluation。
2. **Schema 是合约**:所有下游消费 markdown artifact 时先看 YAML frontmatter 和 JSON Schema。
3. **Bridge 是产品编排层**:知道 Commander、5 Agent、入口模式、feedback loop。
4. **Harness 是可信机制层**:不知道具体产品拓扑,只提供可复用的校验、工具、上下文、KB、评价、可观测性。
5. **ToolRegistry 是动作边界**:工具权限、schema、Gate 5、approval、rollback、audit 都在 dispatch 路径上。
6. **Mock-first**:没有 API key/GPU/网络时仍能跑完整 demo。

## 2. 代码分层

### 2.1 后端目录

```text
backend/app/
├─ main.py                  FastAPI app, router 注册,默认 Agent 注册
├─ api/                     REST + WebSocket
├─ bridge/                  Commander、Orchestrator、RunGraph 构造、诊断与评价接入
├─ agents/                  BaseAgent + idea/experiment/coding/execution/writing + debate
├─ harness/
│  ├─ runtime/              RunGraph、state machine、event bus、readiness、system status
│  ├─ schema/               frontmatter parser、validator、JSON Schema
│  ├─ tools/                registry、tool config、code/search/knowledge/execution tools、MCP adapters
│  ├─ gates/                5 个系统 Gate,Gate 5 用于 baseline compatibility
│  ├─ context/              Context V2 compiler、manifest、raw store、budget/compression
│  ├─ llm/                  provider abstraction、model registry、mock、post-training loader
│  ├─ kb/                   file/chroma-like KB,MemoryRecord v2,baseline matcher,fingerprint
│  ├─ evaluation/           evaluation_report、rubric evaluator、scorecard、post-training export
│  ├─ observability/        event envelope、trace recorder、optional LangSmith sink
│  └─ sedimentation/        Agent 完成后的资产抽取和 KB 写入
├─ execution/               mock/pim/local simulation,batch runner,log/metrics/curve helpers
├─ hitl/                    review session、approval、diff、audit log
└─ storage/                 run/artifact/state/context/self-evolution stores
```

### 2.2 配置目录

```text
configs/
├─ agents.yaml              commander + 5 Agent 的 model/tools/debate/loop/post-training
├─ tools.yaml               工具唯一权限和 schema 控制面
├─ models.yaml              provider 注册
├─ gates.yaml               Gate 阈值
├─ execution.yaml           batch/mock/local execution 配置
├─ knowledge.yaml           KB 配置
├─ context.yaml             feedback/context 预算与裁剪
├─ evaluation.yaml          evaluator 与 policy
├─ evaluation_rubrics/      各 artifact schema 的质量 rubric
├─ memory.yaml              governed memory 写入、mock 隔离、selector
├─ observability.yaml       trace/event/langsmith sink
└─ agent_contexts/          每个 Agent 的长期 context/memory/research sites 配置
```

## 3. 依赖边界

### 3.1 强约束

- `harness/` 不能 import `agents/`、`bridge/`、`api/`。
- `bridge/` 通过 `agent_registry.py` 获取 Agent,不直接硬编码具体类。
- `harness/runtime/run_graph.py` 不能写死五 Agent 线性拓扑。
- Agent 调动作类能力必须经过 ToolRegistry。
- 真实研究代码通过 `projects/<name>/repo_link.yaml` 接入,不复制到 MARS 仓。

### 3.2 边界表

| 层 | 知道什么 | 不知道什么 |
|---|---|---|
| API | HTTP/WS 请求、DTO、依赖注入 | Agent 内部策略 |
| Bridge | Commander、entrypoint、5 Agent 产品拓扑、run 生命周期 | Tool 内部执行细节 |
| Agents | 自己的 schema、prompt、工具名、draft/revise 逻辑 | 前端、API、RunGraph 调度 |
| Harness | schema/tool/context/kb/eval/gate/runtime 通用机制 | 产品拓扑和具体 Agent 类 |
| Storage | 文件布局和持久化 | 业务决策 |

## 4. Run 生命周期

### 4.1 创建

`POST /api/runs` 或 Commander 的 `run.create` 调用:

1. `RunStore.create()` 创建 `runs/<timestamp>_<task>/`。
2. 写 `run_meta.json` 和 `input/user_request.md`。
3. 创建 9 个核心子目录: `input/ context/ idea/ experiment/ coding/ execution/ writing/ hitl/ events/`。
4. `workflow_service` 根据 entrypoint 构造 RunGraph。
5. `TraceRecorder.ensure_manifest()` 初始化 trace。

### 4.2 执行

`Orchestrator.run()` 循环读取 `RunGraph.ready_nodes()`:

```text
PENDING
  -> RUNNING
  -> WAITING_REVIEW
  -> APPROVED
  -> DONE
```

失败路径进入 `FAILED`,下游不再自动推进。Run 状态持久化到 `RunStateStore`,事件写入 `events/*.jsonl` 并通过 EventBus 推送。

### 4.3 Agent 节点

每个 Agent 节点由 `bridge/agent_runner.py` 负责桥接:

1. 读取 `input/user_request.md` 和上游 `*.approved.md`。
2. 调 Agent `build_context()`。
3. 调 Agent `run_loop()`。
4. 写 artifact 版本。
5. 运行 schema/evaluation。
6. 触发 sedimentation。
7. 打开 HITL review。

### 4.4 Execution 特殊路径

Execution Agent 先产出 `run_log.v1` 风格的计划/记录 artifact。该节点 approve 后,Orchestrator 会再次进入 RUNNING,通过 ToolRegistry 调 `execution.batch_runner`。

这样批量仿真也经过工具治理、trace 和 audit,不会绕过 Gate/事件系统。

### 4.5 完成

所有节点 DONE/SKIPPED 后:

1. `harness/evaluation/aggregation.py` 聚合 eval reports。
2. 写 `events/evaluation_scorecard.json`。
3. 发布 `run.completed`。
4. `runs/<run_id>/` 成为 replay/audit/post-training 的事实源。

## 5. RunGraph 与 Workflow

`harness/runtime/run_graph.py` 是通用 DAG:

- 节点只有 key、kind、state、metadata。
- 支持 add_node/add_edge/set_entrypoint/skip。
- 用拓扑排序判断 ready nodes。
- 不知道 Idea/Experiment/Coding/Execution/Writing。

产品拓扑在 `bridge/workflow_service.py`:

```text
idea -> experiment -> coding -> execution -> writing
```

Standalone 是单节点 RunGraph。任意入口通过把入口前的节点标为 SKIPPED 实现。

## 6. Agent Loop

`agents/base.py` 定义统一 Agent 接口:

```text
build_context(request) -> ContextPack
draft(request, context) -> Artifact
revise(artifact, feedback) -> Artifact
validate_output(artifact) -> ValidationResult
submit_for_review(artifact) -> Artifact
run_loop(request, context) -> Artifact
```

当前 `run_loop()` 做三件重要的治理:

1. 调 `draft()` 生成 artifact。
2. 用 JSON Schema 校验。
3. 如果失败,按 `loop.max_validation_repairs` 做 schema-aware repair。

如果 repair 仍失败,artifact 保留并交给 HITL,避免失败输出直接丢失。

### 6.1 Tool gather

真实 provider 且 Agent 配置了工具时,BaseAgent 可先进入工具收集循环:

```text
LLM emits {"tool_calls": [...]}
  -> ToolRegistry.dispatch()
  -> compact observation + raw_ref
  -> fold observations into upstream context
  -> final draft
```

Mock provider 下跳过该循环,保持零依赖 demo 稳定。

### 6.2 Debate

`agents/debate/` 支持多角色 debate。Idea 和 Writing 默认开启。当前配置使用 DeepSeek 不同模型别名模拟 proposer/critic/judge/positive reviewer;缺 key 时降级到 mock debate。

## 7. Schema 与 Artifact

### 7.1 Schema

JSON Schema 位于 `backend/app/harness/schema/schemas/`。当前 artifact schema:

- `proposal.v1`
- `experiment_plan.v1`
- `code_spec.v1`
- `run_log.v1`
- `report.v1`
- `diagnosis.v1`
- `feedback_packet.v1`
- `evaluation_report.v1`

`validator.py` 负责:

- 解析 frontmatter。
- 根据 `schema` 字段加载对应 JSON Schema。
- 支持 expected schema。
- 返回结构化 `ValidationResult`。

### 7.2 ArtifactStore

Artifact 写入遵循版本化:

```text
runs/<id>/<agent>/<artifact>.v1.md
runs/<id>/<agent>/<artifact>.v2.md
runs/<id>/<agent>/<artifact>.approved.md
```

人工编辑产生新版本,approve 复制/标记 approved 版本。下游默认只读 approved artifact。

## 8. ToolRegistry

`harness/tools/registry.py` 是所有工具调用的安全边界。

Dispatch 顺序:

1. 记录 `tool.started`。
2. unknown tool 检查。
3. `configs/tools.yaml` enabled 检查。
4. allowed_agents 检查。
5. input_schema 校验。
6. 写工具 approval 检查。
7. Gate hook,当前默认安装 Gate 5。
8. timeout 包裹真实 tool function。
9. 写 `tool_events.jsonl`、`tool_calls.jsonl`、tool application record。
10. 结束 trace span。

`ToolResult` 标准状态:

```text
success | error | blocked | requires_approval | disabled | not_allowed | unknown_tool
```

### 8.1 工具族

- `search.*`: local docs、arXiv、web search。
- `knowledge.*`: KB 查询、baseline match、文档摄入。
- `code.*`: repo read/write/patch/delete/rollback/lint/test。
- `execution.*`: simulation、batch、logs、metrics。
- `run.*` / `artifact.*` / `metrics.*` / `diagnosis.*`: Commander bridge-only 工具。

### 8.2 MCP Adapter

MCP 是可选适配层。Agent 仍调用 MARS 工具名,MCP 只作为某些工具后端的 adapter。adapter 不可绕过 ToolRegistry。

## 9. Gate 与 HITL

### 9.1 HITL Review

`hitl/` 提供:

- review session
- approval/reject
- diff view
- audit log
- revision loop

Orchestrator 在 `WAITING_REVIEW` 等待 approve 或 auto-approve。生产环境禁止创建 auto-approved run。

### 9.2 系统 Gate

系统 Gate 位于 `harness/gates/`:

- `plan_finalized`
- `large_refactor`
- `experiment_launch`
- `conclusion_output`
- `baseline_compatibility`

Gate 5 特别重要:它读取 `projects/<name>/AGENTS.md` 和 repo/link 规则,在 tool dispatch 前检查 patch 或文件写入是否破坏 baseline。block 结果不可被普通人工审批绕过。

## 10. Context Engineering

当前上下文有 legacy loader 和 V2 compiler。实际 LLM 调用优先走 `harness/context/engine.py`。

### 10.1 Compile 输入

`CompileContextInput` 包含:

- agent / node_key / project / output_schema
- system guidance
- project context
- task
- upstream blocks
- metadata
- run_id / run_root
- purpose
- tool_names

### 10.2 Segment

Compiler 把输入归一成 `ContextSegment`:

- system
- schema
- project
- artifact / upstream
- memory
- tool
- task

每个 segment 有 priority、source_ref、content_hash、token estimate、selection reason、risk flags、raw_ref。

### 10.3 Pack / Compress / Manifest

流程:

```text
collect_segments
  -> select_segments
  -> compress_segments
  -> pack under target/max token budget
  -> diagnose risks
  -> render provider messages
  -> write context_manifest.v2.*.json
```

大块工具输出通过 `raw_store.py` 写入 `runs/<id>/context/raw/`,prompt 只放摘要与 raw ref。

### 10.4 Context Workbench

前端 `/context` 读取 `/api/context/*`,用于查看:

- manifests
- segments
- render order
- token budget
- raw refs
- manifest diff
- risk diagnostics

## 11. KB 与 Memory

### 11.1 KB Backend

`harness/kb/` 提供:

- stores/backends: file store 和可替换后端。
- ingester/memory_writer: 写入 KB。
- retriever/selector: 检索和重排。
- baseline_matcher/fingerprint: baseline 复用。
- models: MemoryRecord v2。
- resolver/consolidate/profiles: 冲突、合并、profile 化。

四区逻辑:

```text
literature
methodology
code_assets
run_archive
```

### 11.2 MemoryRecord v2

MemoryRecord 记录:

- zone
- memory_type: semantic / episodic / procedural
- source_path / run_id / agent / schema
- content_hash
- is_mock
- confidence / salience
- eval_status
- ttl_days
- approved
- supersedes / superseded_by

`configs/memory.yaml` 定义 mock 隔离、写入门槛、生命周期衰减和 selector 权重。

### 11.3 Agent Context Store

`storage/agent_context_store.py` 管理长期 Agent context:

- `docs/`
- `prompts/`
- `examples/`
- `evals/`
- `uploads/`
- approved memory items
- research sites

只有 approved/active memory 会进入未来 Agent context。stale/rejected/superseded 会保留审计但不注入。

## 12. Sedimentation

`harness/sedimentation/hooks.py` 在 Agent 完成后触发资产沉淀:

```text
idea        -> literature / methodology
experiment  -> methodology / run_archive
coding      -> code_assets
execution   -> run_archive + fingerprint
writing     -> methodology
```

沉淀写入 MemoryRecord,并带 source、project、agent、schema、mock/eval 等元数据。Mock 数据按 memory policy 隔离或降低召回权重。

## 13. Evaluation

`harness/evaluation/` 是 artifact-oriented 评价层,不 import 具体 Agent。

### 13.1 数据模型

`evaluation_report.v1` 包含:

- scope: artifact / run / benchmark / model_backend
- target_ref / target_schema
- evaluator / evaluator_version
- decision: pass / warn / revise / block / fail
- blocking
- overall_score / scores
- findings with evidence_refs
- recommended_actions

### 13.2 当前 Evaluator

默认 `EvaluationRunner` 注册:

- `SchemaValidityEvaluator`
- `ProvenanceEvaluator`
- `ArtifactQualityEvaluator`

Artifact quality 读取 `configs/evaluation_rubrics/<schema>.yaml`,并使用 deterministic scorer。

### 13.3 Scorecard

Run 完成时:

```text
read all *.eval.md
  -> worst decision
  -> average score
  -> top findings
  -> events/evaluation_scorecard.json
```

Bridge 通过 `evaluation_service` 把 scorecard 事件推给前端。

### 13.4 Post-training Export

`harness/evaluation/post_training_export.py` 只导出带证据的候选数据。它不构造 preference pair,不启动训练,不做 live checkpoint routing。

## 14. 自进化

自进化代码在 `storage/self_evolution_store.py`。设计原则是 manual-review-only。

### 14.1 Run-local learning

失败诊断、feedback loop、scorecard finding 可以写入:

- `memory/episode_memory.jsonl`
- `memory/memory_candidates.jsonl`

candidate 默认 `pending_review`,不会自动进入长期上下文。

### 14.2 Levers

`build_self_evolution_levers()` 暴露:

- prompt levers
- few-shot levers
- eval levers
- KB/evaluation finding levers

### 14.3 Mutation

Prompt/few-shot/eval 修改必须:

1. 创建 mutation proposal。
2. 通过 deterministic gate: lever_present、agent_supported、target_allowed、non_empty、changes_content、rationale_present。
3. 用户 approve。
4. 修改 Agent context 文件。
5. 同步到 governed memory。

## 15. Observability

### 15.1 信号

MARS 使用六类信号:

- Event
- Trace
- Metric
- Log
- Audit
- Readiness

### 15.2 Durable paths

```text
runs/<id>/events/*.jsonl
runs/<id>/context/trace_manifest.v2.json
runs/<id>/execution/metrics.json
runs/<id>/execution/logs/
runs/<id>/hitl/review_log.jsonl
runs/<id>/events/evaluation_scorecard.json
```

### 15.3 Trace

`harness/observability/tracing.py` 写 file-backed trace manifest。节点、工具、LLM/context 等可扩展为 span。LangSmith sink 是可选镜像,不影响本地 file trace。

### 15.4 EventBus / WebSocket

`harness/runtime/event_bus.py` 提供 in-process/Redis 风格事件总线。WebSocket endpoint 订阅 run-level 和 experiment-level channel。UI 刷新后必须能从 REST + durable files 恢复状态。

## 16. Execution

Execution 层在 `backend/app/execution/`:

- `mock_simulation.py`: 零 GPU fallback。
- `simulation_runner.py`: 单实验执行 facade。
- `batch_runner.py`: 并发 batch。
- `metrics_collector.py`: metrics/run_log 写入。
- `curve_parser.py`: 曲线格式化。
- `log_streamer.py`: 日志读取。
- `pim_cancellation.py`: PIM 相关指标/取消辅助。

Execution tool 位于 `harness/tools/execution/`,它通过 ToolContext 注入 bridge callback,保持 harness 不依赖 bridge。

输出落地:

```text
execution/logs/
execution/curves/
execution/metrics.json
execution/batch_summary.json
execution/run_log_<experiment>.v1.md
```

## 17. LLM Provider

统一接口在 `harness/llm/provider_base.py`:

```text
complete(messages, config) -> Completion
stream(messages, config) -> AsyncIterator[Delta]
```

当前模型路由由 `model_registry.py` 根据 `configs/agents.yaml` 选择 provider。支持真实 provider、OpenAI-compatible endpoint、本地 vLLM、post-training loader 和 mock provider。

当前默认配置以 DeepSeek 为主:

- commander: `deepseek-chat`
- idea: `deepseek-chat`,debate 中使用 `deepseek-reasoner`
- experiment: `deepseek-reasoner`
- coding: `deepseek-chat` + optional post-training endpoint
- execution: `deepseek-chat`
- writing: `deepseek-chat` + reviewer debate

缺 key 或 provider 失败时,非生产模式 fallback 到 `MockProvider`。

## 18. Frontend

前端是 Next.js 15。当前主要页面/组件包括:

- Lab 首页和 run 列表。
- Run detail / Pipeline overview。
- Context Workbench。
- Agent Context Panel。
- Coding Workspace Panel。
- Runtime Ops Panel。
- KB Panel。
- Chat Panel / Commander。
- Project switcher。

前端通过 `frontend/src/lib/api.ts` 调 REST,通过 socket 接收 run/execution 事件。Context Workbench 的纯逻辑在 `frontend/src/lib/contextWorkbench.ts`。

## 19. API Surface

`main.py` 当前注册的 API:

```text
/api/runs
/api/context
/api/diagnoses
/api/agents
/api/artifacts
/api/evaluation
/api/traces
/api/execution
/api/knowledge
/api/templates
/api/tools
/api/projects
/api/readiness
/api/runtime
/api/events
/api/stats
/api/chat
/ws/...
```

原则:

- API 做输入输出和依赖注入。
- 产品决策在 bridge。
- 通用治理在 harness。

## 20. 测试与验收

当前测试覆盖方向:

- schema compliance
- Agent standalone mock
- Agent loop schema repair
- debate runner
- Orchestrator dummy/full mock run
- HITL flow
- Gate 5 baseline compatibility
- Tools hardening / execution tools
- Context V2 runtime/compiler
- Evaluation runner/policy/scorecard
- Memory v2 governance
- Commander tools/feedback
- post-training loader/export
- observability events/langsmith
- frontend context smoke

核心命令仍是:

```bash
bash scripts/acceptance.sh
```

要求是 mock path 在零外部依赖下完整可跑,真实 GPU/真实 LLM 是增强路径。

## 21. 扩展规则

新增 Agent:

1. 实现 BaseAgent 子类。
2. 增加 output schema 或复用已有 schema。
3. 在 `configs/agents.yaml` 配 tools/model/loop。
4. 在 bridge registry 注册。
5. 如果进入产品拓扑,只改 `workflow_service.py`。

新增工具:

1. 在 `harness/tools/*` 实现 tool function。
2. 在 ToolRegistry 注册。
3. 在 `configs/tools.yaml` 写 enabled、allowed_agents、mutation_level、schema、timeout。
4. 写 unit test 覆盖 disabled/not_allowed/schema invalid/audit。
5. 写工具如果触碰项目 repo,必须走 allowed paths、rollback、Gate 5。

新增长期记忆:

1. 先写 run-local candidate。
2. 通过 evaluation/provenance 检查。
3. 人工 approve。
4. 写入 Agent memory 或 KB。
5. 设置 ttl/salience/confidence/mock policy。

新增评价:

1. 在 `harness/evaluation/evaluators/` 写 evaluator。
2. 输出 `EvaluationReport`。
3. findings 必须有 evidence refs。
4. 配置到 `configs/evaluation.yaml`。
5. 不 import bridge/agents/api。

## 22. 非目标

- 不把 harness 变成产品编排层。
- 不让前端直接驱动 Agent 内部。
- 不让任何工具绕过 ToolRegistry。
- 不在 V0/V0.5 做 GRPO 训练。
- 不让 external telemetry 成为 replay 必需依赖。
- 不让 pending memory 自动进入未来上下文。
