# DESIGN.md — MARS 架构与实现设计

> 这份文档讲"怎么搭"。读之前先读 `PRODUCT.md` 知道"做什么"。

## 1. 五层架构(Tier)概览

| Tier | 名称 | 职责 |
|---|---|---|
| 1 | Web Workbench | 前端工作台(6 入口、Pipeline、多实验 split view、HITL) |
| 2 | API + Bridge | FastAPI + WebSocket 接入 + Bridge 编排 |
| 3 | Five Agents | Idea / Experiment / Coding / Execution / Writing |
| 4 | Harness Services | Schema / Tools / Gates / Context / LLM / KB / Sedimentation / Runtime |
| 5 | Storage & Projects | KB 物理存储 + workspace + runs + projects |

Tier 之间的流向:
- Tier 3 内部:`Idea → Experiment → Coding → Execution → Writing`(线性,但任意入口可进入)
- Tier 间:上层依赖下层,**不反向**

## 2. 模块分层与依赖方向

```
frontend
   ↓ HTTP / WS
api ───────────────┐
   ↓               │
bridge ──────────► harness  (bridge 与 agents 都依赖 harness;harness 不依赖任何上层)
   ↓               │
agents ────────────┘
```

**强约束**:
- `harness/` 内部禁止 import `agents/` 或 `bridge/`
- `bridge/` 通过 `bridge/agent_registry.py` 反转依赖,**不直接** `from agents.idea import IdeaAgent`,而是 `registry.get("idea")`
- `harness/runtime/` 模块不知道任何具体 Agent,只接受 `RunGraph` 数据结构 + Agent interface

依赖方向是 CI 检查的硬指标(用 `import-linter` 或类似工具)。

## 3. Bridge 与 Harness 边界

这是 v0.4 结构最关键的一刀。

| | bridge/ | harness/ |
|---|---|---|
| **角色** | 产品编排层 | Agent-agnostic 可信机制 |
| **知道什么** | 当前有 5 个 Agent / 当前 project 是 moe-pimc / 用户从哪个入口进 | 只知道接口,不知道实现 |
| **典型职责** | 编排 RunGraph、路由用户请求到正确 Agent、project 隔离 | Schema 校验、Tool 调用、Gate 触发、Context 装载、LLM 调用、KB 检索、沉淀 |
| **可替换性** | 换个产品形态(如 CLI 而非 Web)就要改 | 换 Agent / 换项目 / 换部署形态都不动 |

`bridge/` 调用 `harness/runtime/` 来执行图;`agents/` 调用 `harness/` 的所有服务做 LLM 推理与 IO 校验。这条调用图保证了 harness 的复用性。

### bridge/ 内部结构

```
bridge/
├─ orchestrator.py        # 主入口,接收 api 层的 run 请求
├─ agent_registry.py      # Agent 启动时注册;反转依赖
├─ workflow_service.py    # 构造 RunGraph(决定从哪个 Agent 进入、跳过哪些)
└─ project_isolation.py   # 多 project 时的隔离(V0 单 project,但接口预留)
```

### harness/ 内部结构

```
harness/
├─ runtime/
│  ├─ run_graph.py        # 通用图数据结构 + 调度算法,不写死任何拓扑
│  ├─ state_machine.py    # pending / running / waiting_review / approved / failed / done
│  ├─ queue_manager.py    # asyncio + Redis 任务队列
│  └─ event_bus.py        # WebSocket / event stream pub/sub
├─ schema/
│  ├─ frontmatter_parser.py
│  ├─ validator.py
│  └─ schemas/            # 5 个 JSON Schema(详见 §4)
├─ tools/
│  ├─ registry.py         # ★ Gate 5 hook 在 dispatch 路径上
│  ├─ search/             # arxiv / kb_query / web_search / local_docs
│  ├─ code/               # repo_reader / patch_generator / code_review / test_runner
│  ├─ execution/          # run_python / run_simulation / run_training
│  ├─ knowledge/          # kb_write / kb_read / baseline_match
│  └─ collaboration/      # request_human_review / agent_debate
├─ gates/
│  ├─ gate_base.py
│  ├─ plan_finalized.py
│  ├─ large_refactor.py
│  ├─ experiment_launch.py
│  ├─ conclusion_output.py
│  └─ baseline_compatibility.py   # ★ 静态规则触发,不是流程 checkpoint
├─ context/
│  ├─ loader.py           # 3 层装载入口
│  ├─ system_layer.py
│  ├─ project_layer.py    # 内部分:agents_md / baseline_meta / domain / code / history
│  ├─ task_layer.py       # KB top-k + 上游 handoff + recent dialog
│  ├─ manifest.py         # Context Manifest 写入 run_log
│  └─ compressor.py       # 三种策略:hier_summary / reference / relevance_prune
├─ llm/
│  ├─ provider_base.py
│  ├─ anthropic_provider.py
│  ├─ openai_provider.py
│  ├─ qwen_provider.py
│  ├─ local_vllm_provider.py
│  ├─ custom_endpoint_provider.py
│  ├─ model_registry.py
│  └─ post_training_loader.py
├─ kb/
│  ├─ stores.py           # 4 区 ChromaDB client
│  ├─ ingester.py
│  ├─ retriever.py
│  ├─ baseline_matcher.py # 语义匹配 + Fingerprint 比对
│  └─ memory_writer.py
└─ sedimentation/
   ├─ hooks.py            # 每 Agent 完成后触发
   ├─ extractors/         # 按 Agent 类型分:idea_extractor / experiment_extractor / ...
   └─ asset_metadata.py
```

## 4. Schema(系统脊柱)

### 4.1 总原则

每个 Agent 的输出 = `markdown body + YAML frontmatter`,文件命名 `<artifact>.<version>.md`,版本 ∈ `v1` / `v2` / ... / `approved`。

frontmatter 通过对应 JSON Schema 校验,人写 / Agent 写一视同仁。

### 4.2 五种 Schema 类型

详细 schema 文件存 `harness/schema/schemas/`。下面给关键字段。

#### `proposal.v1`(Idea Agent 输出)

```yaml
---
schema: proposal.v1
project: moe-pimc
agent: idea
created: 2026-05-04T10:32:00Z
research_question: "..."
hypothesis: "..."
novelty: "..."
theoretical_basis: "..."
constraints:
  - "baseline_compat: required"
  - "ASIC_resource: ≤40% reduction"
related_literature:
  - title: "..."
    url: "..."
debate_summary:        # 可选,debate 开启时填
  rounds: 2
  consensus: "..."
---
```

#### `experiment_plan.v1`(Experiment Agent 输出)

```yaml
---
schema: experiment_plan.v1
project: moe-pimc
agent: experiment
upstream_artifact: idea_proposal.approved.md
variables:
  independent: ["expert_count", "router_type"]
  controlled: ["batch_size", "epochs"]
  dependent: ["RES", "PIM", "APE"]
metrics:
  primary: "RES"
  secondary: ["PIM", "APE", "param_count"]
baseline_ref:           # Baseline 复用决策结果
  matched_run_id: null  # 或匹配到的 run_id
  reuse_decision: "rerun" # rerun / reuse / modify
ablations:
  - name: "expert_count_4"
    config: {expert_count: 4}
  - name: "expert_count_16"
    config: {expert_count: 16}
estimated_runs: 8
estimated_gpu_hours: 24
---
```

#### `code_spec.v1`(Coding Agent 输出)

```yaml
---
schema: code_spec.v1
project: moe-pimc
agent: coding
upstream_artifact: experiment_plan.approved.md
target_lang: python    # python / c
baseline_compat:
  preserved: true
  rationale: "..."
files_changed:
  - path: "libs/Model.py"
    type: modified
    risk: medium
new_dependencies: []
test_coverage:
  unit_tests_added: 3
  baseline_smoke_test: pass
---
```

#### `run_log.v1`(Execution Agent 输出)

```yaml
---
schema: run_log.v1
project: moe-pimc
agent: execution
upstream_artifact: code_spec.approved.md
run_id: "2026-05-04T2310_pimc_moe_ablation_run3"
batch_size: 512
gpu_used: ["L40S:1", "L40S:2"]
duration_seconds: 3420
status: completed     # completed / failed / interrupted
metrics:
  RES: -42.3
  PIM: -18.7
  APE: 23.6
fingerprint_hash: "sha256:abcd1234..."
---
```

#### `report.v1`(Writing Agent 输出)

```yaml
---
schema: report.v1
project: moe-pimc
agent: writing
deliverable_type: research_report  # research_report / paper_fragment / ppt_outline / tech_summary
target_audience: phd_advisor
chain_refs:
  proposal: idea_proposal.approved.md
  plan: experiment_plan.approved.md
  code: code_spec.approved.md
  runs: ["run_log_run1.md", "run_log_run2.md", "..."]
debate_summary:
  rounds: 1
  reviewer_critiques: ["...", "..."]
---
```

### 4.3 校验流程

```
Agent / 人 写出 md
   ↓
frontmatter_parser.py 解析 YAML
   ↓
validator.py 按 schema 字段名找对应 JSON Schema
   ↓
JSON Schema 校验
   ├─ 通过 → 写入 runs/<id>/<agent>/<artifact>.<version>.md
   └─ 不过 → 返回错误字段列表,前端高亮
```

**合规率指标**:Agent 首次输出通过率 ≥ 95%(失败的多由前端 / 人工补全后通过)。

## 5. Agents 详解

### 5.1 BaseAgent 接口

```python
class BaseAgent(ABC):
    name: str                              # "idea" / "experiment" / ...
    output_schema: str                     # "proposal.v1" 等

    @abstractmethod
    async def build_context(self, request: RunRequest) -> ContextPack: ...

    @abstractmethod
    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact: ...

    @abstractmethod
    async def revise(self, artifact: Artifact, feedback: HumanFeedback) -> Artifact: ...

    async def validate_output(self, artifact: Artifact) -> ValidationResult:
        # 统一走 harness/schema/validator.py
        ...

    async def submit_for_review(self, artifact: Artifact) -> ReviewSession:
        # 统一走 hitl/review_session.py
        ...
```

5 个 Agent 都继承,差异在 `build_context` / `draft` 内部的 LangGraph 子图。

### 5.2 Agent 内部子图(以 Idea Agent 为例)

```
ReceiveQuestion
    ↓
build_context (3 层装载)
    ↓
KB Retrieval (literature + history)
    ↓
arxiv search (如果 hypothesis 涉及未在 KB 的方向)
    ↓
DraftCandidate (LLM)
    ↓
[debate enabled?]
    ├─ yes → MultiModelDebate (3 LLM × 2 rounds) → CriticSynthesis
    └─ no  → 直接 SchemaValidate
    ↓
SchemaValidate
    ↓
SedimentationHook (写入 literature + methodology KB)
    ↓
Output proposal.md
```

具体 LangGraph 节点定义见 `agents/idea/graph.py`。

### 5.3 Multi-model Debate

实现在 `agents/debate/`:

```
debate/
├─ debate_runner.py       # 编排 N 轮辩论
├─ judge.py               # Critic 角色综合
└─ roles.py               # proposer / critic / judge 角色 prompt 模板
```

**默认配置**:3 model × 2 round + 1 Critic synthesis。每个 model 独立配置(`configs/agents.yaml -> idea.debate.participants`)。

## 6. HITL 两层机制

### 第一层:每 Agent 输出 review(`hitl/`,高频)

每个 Agent 完成 draft 后,默认进入 review session:

```
hitl/
├─ review_session.py      # 创建 session,前端订阅
├─ revision_loop.py       # 人提 feedback → Agent.revise() → 新版本
├─ diff_view.py           # v1 vs v2 vs approved diff
├─ approval.py            # 批准 / 驳回 / 触发 regenerate
└─ audit_log.py           # 所有 review 操作 jsonl 记录
```

操作:edit / comment / approve / reject / regenerate。

### 第二层:5 个系统 Gate(`harness/gates/`,稀疏)

| # | Gate | 触发时机 | 触发方 |
|---|---|---|---|
| 1 | `plan_finalized` | Idea Agent approve 后,Experiment 启动前 | RunGraph 节点转换 |
| 2 | `large_refactor` | Coding Agent 输出修改文件数超阈值 | Coding 完成时静态检查 |
| 3 | `experiment_launch` | Execution Agent 启动前(估算 GPU 时长 / cost 超阈值时) | RunGraph 节点转换 |
| 4 | `conclusion_output` | Writing Agent 完成后,落库前 | RunGraph 节点完成 |
| 5 | `baseline_compatibility` | **任何 Agent 调用 tool 时**,如该 tool 会破坏项目 baseline | Tool registry dispatch 拦截 |

**Gate 5 特殊性**:不在流程 checkpoint 上,而是 hook 进 `harness/tools/registry.py` 的 tool dispatch 路径:

```python
# harness/tools/registry.py
async def dispatch(tool_name: str, args: dict, ctx: RunContext) -> ToolResult:
    # ★ Gate 5 在这里
    gate_result = await baseline_compatibility.check(tool_name, args, ctx)
    if gate_result.requires_human:
        await hitl.request_approval(gate_result)  # 阻塞等待
    # 通过后才真正调 tool
    return await tools[tool_name].run(args, ctx)
```

`baseline_compatibility.check()` 读取项目 `AGENTS.md` 的"禁止行为"清单做静态匹配。

## 7. 数据流闭环(横切机制)

### 7.1 Sedimentation 闭环

每 Agent 完成时触发 `harness/sedimentation/hooks.py`:

```
Agent 完成
   ↓
SedimentationHook
   ↓
extractor (按 Agent 类型分)
   ├─ idea_extractor       → literature / methodology KB
   ├─ experiment_extractor → methodology KB
   ├─ coding_extractor     → code_assets KB
   ├─ execution_extractor  → run_archive KB(含 Baseline Fingerprint)
   └─ writing_extractor    → methodology KB
   ↓
asset_metadata.py 添加元数据 (project, agent, timestamp, tags, embedding)
   ↓
kb/memory_writer.py 写入对应 ChromaDB collection
```

### 7.2 Context 装载链(3 层)

每次 LLM 调用前:

```
LLM call
   ↓ build_context()
context/loader.py
   ├─ system_layer    : agent role + hard constraints + output schema
   ├─ project_layer   : projects/<name>/AGENTS.md + baseline meta + history summary
   │                    内部包含 domain / code / history 三个子分类
   └─ task_layer      : KB top-k retrieval + 上游 handoff md + recent dialog
   ↓
ContextPack (统一数据结构)
   ↓
context/manifest.py 写入 runs/<id>/context/context_pack.<version>.json
   ↓ + 一个人类可读的 context_snapshot.md
   ↓
LLM provider call
```

**Context Manifest** 是审计抓手:每次 LLM 调用前,记录"这次调用装载了什么、来自哪里、token 预算多少、为何这样选"。

V1 在不破坏 V0 `context_pack.vN.json` 的前提下,新增 `context_manifest.v2.*.json`:
- pre-call 写入真实 `messages_preview`,而不是 Agent 完成后重建。
- 上下文先归一成 `ContextSegment`,记录 kind / priority / source_ref / content_hash / token 估算 / selection_reason / compression / risk_flags / raw_ref。
- `context_manifest.v2.json` 作为 run 内索引,供 `/api/context/*` 与 `/context` 工作台读取。
- tool 原始输出与大块中间结果写到 `runs/<id>/context/raw/`,prompt 只保留摘要与 `raw_ref`。
- 污染诊断覆盖 poisoning / distraction / confusion / clash / lost-in-middle,作为 manifest diagnostics 而不是阻塞 gate。

`context_manifest.v2` 的封版字段:
- run 维度:`run_id` / `agent` / `node_key` / `project` / `output_schema` / `purpose` / `created_at`。
- budget:`max` / `target` / `used` / `over_budget`。
- segments:每个 `ContextSegment` 只暴露 `text_preview`,不把完整 prompt 原文二次复制进 manifest。
- render_order:最终 prompt 的 segment 顺序,用于检查 critical 指令是否落在开头或结尾。
- messages_preview:provider 调用前真实 messages 的截断预览。
- diagnostics:
  - `risk_counts` / `warnings` / `query_terms` / `segment_count`
  - `compression.counts` 与 `compression.decisions`,记录 summary/reference/relevance_prune/trimmed 的前后 token 与 raw_ref
  - `packing`,记录 dropped/trimmed/over_target/over_max 与每个预算决策
- raw_refs:所有外置 raw context 的引用,通过 `/api/context/runs/{run_id}/raw/{raw_ref}` 读取。

Context Workbench(`/context`) 是独立操作页,不嵌入 run 页面主流程:
- Run/Agent/Manifest 选择器支持 agent、purpose、risk-only、over-budget 过滤。
- Segment 表格支持 kind/risk 过滤与 render/token/priority/risk 排序。
- Manifest diff 比较当前 manifest 与任一同 run manifest,显示 added/removed/changed segment、token delta、compression/risk/hash 变化。
- Raw reference 查看器从 `manifest.raw_refs` 快速打开外置内容,能解析 JSON 时以 pretty preview 展示。
- Pollution diagnostics 展示风险说明与可执行处理建议。
- 前端纯逻辑集中在 `frontend/src/lib/contextWorkbench.ts`,由 `pnpm --dir frontend test:context` 做无新依赖 smoke 回归。

Context V1 配置:
- `MARS_CONTEXT_MAX_TOKENS`:硬上限预算,默认 `32000`。
- `MARS_CONTEXT_TARGET_TOKENS`:packer 目标预算,默认 `24000`。
- `MARS_CONTEXT_AUTO_COMPRESS`:是否自动触发压缩,默认开启。
- `MARS_CONTEXT_TOOL_RAW_EXTERNALIZE`:工具原始输出是否写入 `context/raw/`,默认开启。
- `MARS_CONTEXT_WORKBENCH_ENABLED`:是否开放 `/api/context/*` 与 `/context` 工作台 API,默认开启。

### 7.3 Baseline 复用闭环

```
Experiment Agent 设计 plan
   ↓
extract_plan_features() - LLM 抽取关键特征
   ↓ embed
baseline_matcher.py 在 run_archive 做相似度搜索
   ↓
match_score
   ├─ > threshold → 触发 hitl 询问 "Reuse this baseline?"
   │                  ├─ Reuse → plan.baseline_ref.reuse_decision = "reuse",跳过实际 run
   │                  ├─ Rerun → 继续完整 run
   │                  └─ Modify → 回到 plan 编辑
   └─ ≤ threshold → 自动 rerun
```

阈值在 `configs/gates.yaml` 配置(默认 cosine similarity 0.85)。

### 7.4 Context 压缩

触发条件(`harness/context/compressor.py` / `harness/context/engine.py`):
- token 用量 ≥ budget × 70%
- 任务切换边界(Agent 之间 handoff)
- 用户显式触发

V0 不自动触发;V1 允许自动触发,但必须可配置、可审计、可关闭。

3 种策略,按上下文类型选:
- `hier_summary`:对话历史压成 abstract → key decisions → detail pointers,只保留前两层
- `reference`:大块产物(代码 / 长 md)挪入 KB,context 留指针 + 一句话
- `relevance_prune`:历史 chunk 按当前任务做 relevance scoring,丢低分

不可压缩的不变量:hard constraints / 当前任务 spec / 最近一次用户决策 / 当前层 Schema。

## 8. 运行时:`runs/` 目录细颗粒

每次任务启动 → 创建 `runs/<timestamp>_<task>/`:
- timestamp 格式 `2026-05-04T2310`(ISO 8601 短)
- task 是 slug,如 `pimc_moe_ablation`
- 完整示例:`runs/2026-05-04T2310_pimc_moe_ablation/`

### 子目录强制结构

```
runs/<timestamp>_<task>/
├─ run_meta.json              # 任务元数据(用户、入口 Agent、project、配置 hash)
├─ input/
│  ├─ user_request.md         # 原始用户输入
│  ├─ uploaded_files/         # 上传的论文 / 文档
│  └─ selected_context.json   # 用户在前端勾选的上下文项
├─ context/
│  ├─ context_pack.v1.json    # 每次 LLM 调用的 ContextPack
│  └─ context_snapshot.md     # 人类可读的 context manifest
├─ idea/
│  ├─ idea_proposal.v1.md
│  ├─ idea_proposal.v2.md
│  ├─ idea_proposal.approved.md
│  ├─ debate_transcript.md    # debate 开启时
│  └─ idea_schema.json        # 校验记录
├─ experiment/
│  ├─ experiment_plan.v1.md
│  ├─ experiment_plan.approved.md
│  ├─ ablation_matrix.json
│  └─ baseline_decision.json
├─ coding/
│  ├─ code_spec.v1.md
│  ├─ code_spec.approved.md
│  ├─ patch.diff
│  ├─ changed_files.json
│  └─ tests_plan.md
├─ execution/
│  ├─ execution_plan.md
│  ├─ batch_config.json
│  ├─ logs/<run>.log
│  ├─ curves/<metric>.json
│  ├─ metrics.json
│  ├─ failed_cases.md
│  └─ run_log.approved.md
├─ writing/
│  ├─ research_report.v1.md
│  ├─ research_report.approved.md
│  ├─ ppt_outline.md
│  └─ paper_fragment.md
├─ hitl/
│  ├─ review_log.jsonl        # 每条 review 操作
│  ├─ approvals.json
│  └─ human_edits/            # 人工编辑的 patch
└─ events/
   ├─ agent_events.jsonl      # Agent 状态变迁
   ├─ websocket_events.jsonl  # WS 推送的所有事件
   └─ heartbeat.jsonl         # Agent 心跳
```

这个结构保证了**完整可审计 / 可回放 / 任意点继续 / 后训练数据可构造**。

## 9. 前端架构

详细组件清单见 `frontend/src/`。本节只讲架构层。

```
frontend/src/
├─ pages/
│  ├─ Dashboard.tsx             # 首页 6 卡片
│  ├─ NewRun.tsx                # 新建任务,选入口
│  ├─ AgentWorkbench.tsx        # 单 Agent 工作区
│  ├─ PipelineWorkbench.tsx     # Pipeline 模式工作区
│  ├─ RunDetail.tsx             # 一次任务的完整链路
│  ├─ MultiExperimentView.tsx   # 多实验 split view(P0)
│  ├─ KnowledgeBase.tsx
│  ├─ ModelConfig.tsx
│  └─ Settings.tsx
├─ features/
│  ├─ agent-entry/              # 入口选择 + Standalone / Workflow 切换
│  ├─ hitl-review/              # 审查 / 编辑 / version timeline / diff / approve
│  ├─ simulation-monitor/       # log / loss / metrics / multi-run compare
│  ├─ model-config/             # 5 Agent 各自的 LLM / debate / Coding 后训练配置
│  └─ knowledge/                # 上传 / repo connector / 检索 / Baseline 复用面板
├─ components/                  # Layout / Sidebar / StatusBadge / MarkdownRenderer
├─ api/                         # 后端 REST / WS 调用封装
├─ stores/                      # Zustand stores
└─ types/                       # TS 类型(对应后端 schemas/)
```

### WebSocket 事件类型

后端通过 `harness/runtime/event_bus.py` 推送,前端按 channel 订阅:

- `run.<run_id>.agent_state` — Agent 状态变迁
- `run.<run_id>.queue` — 任务队列变化
- `run.<run_id>.hitl_waiting` — Gate 触发,等待审批
- `run.<run_id>.log` — 仿真日志流
- `run.<run_id>.metrics` — loss / metrics tick
- `run.<run_id>.failure` — 失败告警
- `run.<run_id>.heartbeat`

前端按 experiment 订阅 / 取消订阅,降低带宽。多实验 split view 时,每个面板独立 WS channel。

## 10. 配置系统

### 10.1 全局配置(`configs/`)

```
configs/
├─ agents.yaml         # 5 Agent 各自的 model / debate / tools 配置
├─ models.yaml         # LLM provider 注册(API endpoint / key 引用)
├─ tools.yaml          # 工具开关与权限
├─ gates.yaml          # 5 Gate 阈值(如 large_refactor 文件数 / experiment_launch GPU 小时)
├─ knowledge.yaml      # 4 区 KB embedding model / chunk size / overlap
└─ execution.yaml      # GPU 调度(默认 1 vLLM + 3 池)/ 并发上限(6) / 超时
```

### 10.2 项目配置(`projects/<name>/`)

```
projects/moe-pimc/
├─ project.yaml        # 项目元数据(name / domain / tags)
├─ repo_link.yaml      # 真实代码仓接入(见 §10.3)
├─ AGENTS.md           # 项目级硬约束(baseline 保护、领域规则)
├─ data_gen.py         # 合成数据(Volterra / 多项式 PIM 信号,可入仓)
└─ docs/               # 项目特定文档
```

### 10.3 `repo_link.yaml` schema

```yaml
type: local_path        # local_path | git_submodule | mirror
local_path: /usr1/project/y50051262/My_Dynamic
readonly: true
sync_strategy: live     # live | snapshot
agents_md_inherit: true # 项目根的 AGENTS.md 是否合并到 mars 这边
ignore_patterns:        # 不索引进 KB 的路径
  - "data/"
  - "*.npy"
  - "*.npz"
```

## 11. LLM Backend 抽象(关键设计)

`harness/llm/` 提供统一接口:

```python
class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, messages: list[Message], config: LLMConfig) -> Completion: ...

    @abstractmethod
    async def stream(self, messages: list[Message], config: LLMConfig) -> AsyncIterator[Delta]: ...
```

实现:
- `anthropic_provider.py` / `openai_provider.py` / `qwen_provider.py` / `gemini_provider.py` — 远程 API
- `local_vllm_provider.py` — 本地 vLLM serve(支持挂载 LoRA adapter / 全参微调权重)
- `custom_endpoint_provider.py` — 自定义 OpenAI-compatible endpoint
- `post_training_loader.py` — 加载 GRPO 后训练产物(V0 只 load,V1 才 train)

`model_registry.py` 根据 `configs/agents.yaml` 的 `provider + model` 字段路由到对应 provider。

### Coding Agent 的 3 backend

```yaml
# configs/agents.yaml
coding:
  model:
    provider: local_vllm
    model: qwen2.5-coder-7b-grpo-pimc-v1   # V1 才有,V0 用占位 / 公开模型
  post_training:
    enabled: false                          # V0 默认 false
    mode: adapter                           # adapter | endpoint | fine_tuned_id
    adapter_path: ./posttrain/adapters/coding_v1/
    custom_endpoint: ""
    fine_tuned_model_id: ""
```

## 12. 部署

### 12.1 单机配置(默认)

- 4 × NVIDIA L40S
- GPU 0: vLLM serve(挂 Coding Agent 的本地模型,可关)
- GPU 1-3: 实验池(由 Execution Agent 调度)
- 并发上限 6 个 SimulationJob(`configs/execution.yaml`)

### 12.2 Docker Compose 服务清单

```
services:
  - frontend       (Next.js)
  - backend        (FastAPI + WS)
  - redis          (Bridge 队列 + WS pub/sub)
  - chromadb       (4 区 KB)
  - vllm-serve     (可选,Coding Agent 本地模型)
  - postgres       (V0 不用,V1 持久化 run metadata)
```

### 12.3 GPU 调度策略

`harness/runtime/queue_manager.py` 维护 GPU 资源池。Execution Agent 提交 SimulationJob 时声明所需 GPU 数,queue_manager 按 FIFO + 优先级分配。Gate 3(experiment_launch)在估算 GPU 小时超阈值时阻塞。

## 13. 测试策略

```
backend/tests/
├─ unit/                  # 各模块单测
├─ integration/           # 跨模块(bridge + harness + agents)
├─ schema/                # 5 个 schema 的合规率测试(目标 ≥95%)
├─ gate/                  # 5 个 Gate 触发逻辑
├─ baseline/              # Baseline 复用语义匹配准确率
└─ e2e/                   # 完整 pipeline e2e(走通 moe-pimc demo)
```

CI 强制:
- `mypy --strict` 必过
- `import-linter` 检查依赖方向
- 单测覆盖率 ≥ 70%
- e2e demo run 必跑通

## 14. 完整 REST API 清单

```
# Run 生命周期
POST   /api/runs                         # 创建 run(指定入口 Agent / Pipeline)
GET    /api/runs                         # list runs
GET    /api/runs/{run_id}                # 获取 run 详情
POST   /api/runs/{run_id}/start          # 启动
POST   /api/runs/{run_id}/stop           # 停止
DELETE /api/runs/{run_id}                # 删除(归档前)

# Agent 调用
POST   /api/agents/{agent_name}/draft    # 生成首版
POST   /api/agents/{agent_name}/revise   # 基于 human feedback 改写
POST   /api/agents/{agent_name}/approve  # 标记 approved
POST   /api/agents/{agent_name}/continue # 触发下游 Agent

# Artifact
GET    /api/artifacts/{artifact_id}
POST   /api/artifacts/{artifact_id}/edit         # 人工编辑产生新版本
POST   /api/artifacts/{artifact_id}/approve
GET    /api/artifacts/{artifact_id}/versions
GET    /api/artifacts/{artifact_id}/diff?from=v1&to=v2

# Execution
POST   /api/execution/start
POST   /api/execution/stop
POST   /api/execution/rerun
GET    /api/execution/{run_id}/metrics
GET    /api/execution/{run_id}/logs
GET    /api/execution/{run_id}/curves

# 配置
GET    /api/configs/agents               # 读 agents.yaml
POST   /api/configs/agents               # 改 agents.yaml(前端 ModelConfig 页)
GET    /api/configs/models
POST   /api/configs/models
GET    /api/configs/gates
POST   /api/configs/gates

# 知识库
POST   /api/knowledge/upload             # 上传文档
POST   /api/knowledge/index              # 触发索引
GET    /api/knowledge/search?zone=literature&q=...

# 项目
GET    /api/projects
POST   /api/projects/{name}/repo/connect # 接入 repo_link.yaml
GET    /api/projects/{name}/baseline_rules

# WebSocket
WS     /ws/runs/{run_id}                 # 主订阅频道
WS     /ws/runs/{run_id}/experiment/{exp_id}  # 实验级订阅(多实验并排时)
```

## 15. WebSocket 事件命名规范

所有事件用扁平 dot notation,前端按 prefix 路由:

```
# Agent 状态
agent.state_changed              { agent, run_id, from_state, to_state }
agent.output_chunk               { agent, run_id, chunk, total_tokens }

# Artifact
artifact.version_created         { artifact_id, version, source: "agent"|"human" }
artifact.approved                { artifact_id, version }
artifact.schema_validation_failed { artifact_id, errors[] }

# HITL
hitl.review_required             { agent, artifact_id }
hitl.approved                    { agent, artifact_id }
hitl.rejected                    { agent, artifact_id, reason }

# Gate
gate.triggered                   { gate_id (1-5), context, blocking: true }
gate.resolved                    { gate_id, decision: "approve"|"deny"|"modify" }

# Execution
execution.log_line               { run_id, exp_id, line, level }
execution.metric_tick            { run_id, exp_id, step, metric, value }
execution.curve_point            { run_id, exp_id, step, curve_name, value }
execution.failed                 { run_id, exp_id, error }
execution.completed              { run_id, exp_id, fingerprint_hash }

# 系统
heartbeat                        { agent, run_id, timestamp }
queue.changed                    { queue_state }
```

前端可按 channel(per-run / per-experiment)订阅,降低带宽。多实验 split view 时,每个面板订阅独立 `experiment/{exp_id}` 频道。

## 16. Mock Provider 与 Mock Simulation(V0 必须实现)

### 16.1 Mock LLM Provider

`harness/llm/mock_provider.py` 是 V0 的兜底:

- 没配 LLM API key 时自动 fallback
- 返回结构化的占位响应(按当前 Agent 的 output schema 填合规字段)
- 支持模拟 streaming(逐字符 yield,模拟真实 provider 行为)
- 支持模拟 debate(返回多个不同角度的占位 response)

启用条件(自动判定):
```python
def select_provider(agent_config: AgentConfig) -> LLMProvider:
    if missing_api_key(agent_config.model.provider):
        log.warning(f"No API key for {agent_config.model.provider}, falling back to mock")
        return MockProvider(target_schema=agent_config.output_schema)
    return real_providers[agent_config.model.provider]
```

### 16.2 Mock Simulation

`execution/mock_simulation.py`:

- 没 GPU(`torch.cuda.is_available() == False`)时自动启用
- 基于 `data_gen.py` 生成合成数据
- 模拟 loss / metrics 曲线(可配 `exponential_decay` / `noisy_decay` / `plateau` 几种模板)
- 单 run 默认 30 秒(配置可调,见 `configs/execution.yaml`)
- 输出格式与真实 simulation 完全一致(`run_log.md` / `metrics.json` / `curves/`)

**关键约束**:Mock 模式产生的 artifact **必须**和真实 run 在 schema 层完全等价,下游 Agent / KB 沉淀 / Baseline matching 都不应能区分 mock vs real。

### 16.3 Multi-model Debate 三种模式

`agents/debate/debate_runner.py` 必须支持以下三种模式,由 `configs/agents.yaml` 的 `debate.mode` 字段切换:

| 模式 | 适用 | 实现 |
|---|---|---|
| `real_multi_model` | 生产 / Hardware E2E | 真实多 LLM provider 各扮演一角(默认) |
| `single_model_simulated` | 部分 API key 缺失 | 同一个 LLM 用不同 system prompt 扮演 proposer / critic / judge |
| `mock_debate` | Dev E2E / CI | 返回固定 transcript 占位,保证流程跑通 |

**自动降级逻辑**(`debate_runner.py` 启动时判定):

```python
def select_debate_mode(config: DebateConfig) -> DebateMode:
    available_providers = get_available_providers()  # 看 .env 哪些 key 配了
    required_providers = {p.provider for p in config.participants}

    if required_providers.issubset(available_providers):
        return RealMultiModel(config)
    elif len(available_providers) >= 1:
        log.warning(f"Missing providers {required_providers - available_providers}, "
                    f"degrading debate to single_model_simulated")
        return SingleModelSimulated(config, fallback_provider=next(iter(available_providers)))
    else:
        log.warning("No real providers available, using mock_debate")
        return MockDebate(config)
```

### 16.4 Demo 在零外部依赖下跑通

完整 ACCEPTANCE.md §2 Demo 主脚本(11 步)在没有任何真实 LLM API key、没有 GPU 的机器上必须能跑完。CI 在这个零依赖配置下跑 e2e。这对应 ACCEPTANCE §1.1 的 **Dev E2E** 层。
