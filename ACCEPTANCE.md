# ACCEPTANCE.md — V0 验收标准

> 这份文档定义"做完了"的边界。Claude Code / Codex / Cowork 在 V0 工作完成时,必须能让所有验收用例通过。

## 1. V0 验收边界

### V0 必须有

- ✅ 5 个 Agent 都能 Standalone 调用(每个有独立 UI 工作区)
- ✅ Pipeline 模式从 Idea 到 Writing 端到端跑通
- ✅ 任意入口进入(人写 md + Schema 校验通过即可继续)
- ✅ 5 种 Schema 定义 + JSON Schema 校验器
- ✅ 5 个 HITL Gate 全部实现(含 Gate 5 静态规则触发)
- ✅ 每 Agent 输出 review / edit / approve 工作流
- ✅ 4 区 KB(ChromaDB)+ baseline 语义匹配
- ✅ 三层上下文装载 + Context Manifest
- ✅ 知识沉淀闭环(每 Agent 完成时自动写入 KB)
- ✅ Coding Agent 支持 `remote_api` 与 `local_vllm` 两种 backend
- ✅ Execution Agent 支持多实验并发(默认上限 6)
- ✅ 前端 P0 功能(见 PRODUCT.md §10)
- ✅ `runs/` 完整沉淀链路(9 个子目录)
- ✅ 首个项目 `projects/moe-pimc/` 跑通
- ✅ 单机 4 × L40S 部署(**仅作为 Hardware Demo 验收的目标环境**;Dev / CI 验收必须能在零 GPU + Mock 模式下跑通,见下文 §1.1)

### 1.1 验收分层(关键)

V0 验收必须分两层。Claude Code / Codex 实现时,**Dev E2E 是硬指标,Hardware E2E 是目标但不阻塞 V0 完成**。

| 层级 | 适用场景 | 硬件 | LLM | Demo 通过条件 |
|---|---|---|---|---|
| **Dev E2E**(必过) | 开发 / CI / 你睡觉时跑 | 零 GPU,CPU only | 全 mock_provider | 完整 11 步 demo + 7 Phase 全过 + acceptance.sh 全绿 |
| **Hardware E2E**(目标) | 你公司 4×L40S 上验证 | 4 × L40S | 真实 LLM API + 本地 vLLM | 6 路真实并发实验,真实 ATK-MoE 跑出 RES 指标 |

**意义**:开发 Agent 没有真实 GPU / 没有 LLM API key 也能交付完整 V0。你拿到代码后再上自己的 4×L40S 跑 Hardware E2E。

### V0 不做(留 V1)

- ❌ GRPO 训练流水线
- ❌ Preference pair 构造工具
- ❌ Composite reward 设计
- ❌ Live training checkpoint backend
- ❌ 上下文自动压缩(V0 只做 manifest + 手动触发)
- ❌ 多 project 同时运行
- ❌ 前端 P1 功能(GPU 资源面板、LangSmith 嵌入、高级配置抽屉)
- ❌ 多用户 / 权限隔离
- ❌ 云端部署

### 1.2 Context Engineering V1 验收补充

- ✅ 每次 LLM provider 调用前写入 `runs/<id>/context/context_manifest.v2.*.json`
- ✅ 兼容保留 V0 `*_context_pack.vN.json` / `*_context_snapshot.vN.md`
- ✅ V2 manifest 记录 ContextSegment、token budget、render_order、messages_preview、diagnostics、raw_refs
- ✅ 大块 tool output 写入 `runs/<id>/context/raw/`,prompt 只注入 compact observation + `raw_ref`
- ✅ Agent handoff 默认传蒸馏摘要,原始 approved artifact 仍保留在 `runs/`
- ✅ `/api/context/runs/{run_id}` / manifest / raw / preview API 可用
- ✅ 前端 `/context` 工作台可查看 manifest、segment、预算、raw ref 与污染诊断
- ✅ 前端 `/context` 工作台支持 manifest agent/purpose/risk/over-budget 过滤、segment 过滤排序、manifest diff、raw ref 快速打开与 JSON pretty preview
- ✅ `diagnostics.compression` / `diagnostics.packing` 记录压缩、截断、丢弃、over-budget 的审计决策
- ✅ 污染诊断在工作台展示可执行建议,而不是只展示风险标签
- ✅ draft / schema repair / debate / tool gather 都必须走 pre-call manifest 或 message-capture manifest
- ✅ Context V1 配置可通过 `MARS_CONTEXT_MAX_TOKENS` / `MARS_CONTEXT_TARGET_TOKENS` / `MARS_CONTEXT_AUTO_COMPRESS` / `MARS_CONTEXT_TOOL_RAW_EXTERNALIZE` / `MARS_CONTEXT_WORKBENCH_ENABLED` 控制
- ✅ Context Workbench 纯逻辑回归通过 `pnpm --dir frontend test:context` 覆盖 manifest filter、segment sort、manifest diff、raw formatting

### 1.3 Tools V1 验收补充

- ✅ `configs/tools.yaml` 是唯一工具开关与权限控制面,所有注册工具都有 `enabled` / `mutation_level` / `allowed_agents` / `timeout_seconds` / schema 配置
- ✅ `configs/agents.yaml` 中每个工具名必须已注册,或在 `configs/tools.yaml` 中显式标记 `bridge_only: true`
- ✅ `ToolRegistry.dispatch()` 对未知工具、禁用工具、越权 agent、schema invalid、large refactor、Gate 5 block 返回结构化 `ToolResult`
- ✅ 每次 dispatch 写 `events/tool_events.jsonl`、`events/tool_calls.jsonl` 和 trace span
- ✅ Mutating code tools 只允许写 `repo_link.yaml.allowed_paths`,并保存 rollback snapshot
- ✅ `code.delete_file` 和 large refactor 默认 `requires_approval`;Gate 5 baseline 保护始终 `blocked`,不能人工审批绕过
- ✅ `search.arxiv_search` 默认 config-enabled,但运行时必须由 `MARS_ENABLE_NETWORK_TOOLS=true` 才能联网
- ✅ `search.web_search` 默认 config-disabled,只允许 allowlisted domains 和已配置 provider
- ✅ `execution.batch_runner` 在 mock demo 中通过 registry dispatch 启动,并产生 `metrics.json` / `curves/` / `run_log_*.v1.md`
- ✅ Run Detail Commander 面板可按 tool/status/event/call_id/limit 查询工具审计,并支持 pending approval 与 rollback 操作
- ✅ `scripts/verify_tools_v1_acceptance.py` 能对完成的 demo run 验证 catalogue、API filter、tool audit、trace span、execution artifacts

## 2. Demo 主脚本(MOE-PIMC 全链路)

这个 demo 必须 e2e 跑通才算 V0 完成。

### 前置条件

```
1. configs/.env 配置好 LLM API keys
2. projects/moe-pimc/repo_link.yaml 指向真实 / 简化 PIMC 代码
3. projects/moe-pimc/AGENTS.md 已就位
4. data_gen.py 能生成合成 PIM 数据
5. 4 个 ChromaDB collection 已初始化(空也行)
6. Docker Compose 全部 up
```

### 主脚本

```
[Step 1] 用户在前端首页点 "Pipeline" 卡片
[Step 2] 选择 project: moe-pimc
[Step 3] 输入研究问题:
         "如何在 8L 配置下进一步降低 ATK-MoE 的计算资源,同时保持 RES 性能?"
[Step 4] 点击 "Start Run"
[Step 5] Idea Agent 启动
         - 在 frontend 看到 5 节点 pipeline,Idea 节点亮起
         - WebSocket 推送进度
         - 触发 multi-model debate (3 LLM × 2 round)
         - 输出 idea_proposal.v1.md
[Step 6] HITL review session 开启
         - 用户审查 v1,提评论:"再补充对 router 简化的考虑"
         - Idea Agent revise → v2
         - 用户 approve → idea_proposal.approved.md
         - Gate 1 (plan_finalized) 通过
[Step 7] Experiment Agent 启动
         - 设计消融矩阵:expert_count ∈ {4, 8, 16}, router ∈ {soft, hard-topk}
         - Baseline matcher 在 run_archive 找历史相似 plan
         - 命中 1 个相似度 0.91 的历史 run → HITL 弹窗"复用?"
         - 用户选 "Modify" → 把 router 改成新方案
         - 输出 experiment_plan.approved.md
[Step 8] Coding Agent 启动
         - 通过 projects/moe-pimc/repo_link.yaml 定位代码,挂载到 workspace/repos/pimc-current/(真实代码不在 mars 仓内)
         - 生成 patch 修改 libs/Model.py 的 router
         - Gate 5 静态检查 AGENTS.md → 发现 patch 不破坏 baseline,通过
         - lint / test 自动跑,test_coverage 报告
         - 输出 code_spec.approved.md + patch.diff
[Step 9] Execution Agent 启动
         - 读 batch_config (6 个并发 ablation runs)
         - Gate 3 (experiment_launch) 估算 GPU 小时 = 18h,通过阈值,直接启动
         - 4 张 L40S 中 3 张做实验池,前端 split view 显示 6 条 loss 曲线叠加
         - 每个 run 完成时写 Baseline Fingerprint 入 run_archive
         - 输出 6 份 run_log.approved.md + results.json
[Step 10] Writing Agent 启动
         - 读全链路产物
         - 多模型 debate (reviewer critique)
         - 输出 research_report.v1.md
         - HITL review,用户 approve
         - Gate 4 (conclusion_output) 通过
         - 落库 + 沉淀 methodology KB
[Step 11] runs/<timestamp>_pimc_moe_router_simplification/ 完整可见
         - 9 个子目录都有内容
         - 用户可在前端 RunDetail 页面回放整个链路
```

通过标准:**Step 1-11 全部完成,无人工 hack 修复**。

## 3. 各 Agent Standalone 验收

每个 Agent 必须独立可用。验收用例:

### Idea Agent standalone

```
输入:
  research_question: "PIMC 的 stream switching 如何用更轻量的 router 处理"
  uploaded:
    - papers/MoE_Routing_Survey_2024.pdf
预期输出:
  idea_proposal.v1.md (schema 校验通过)
  含至少 1 条新颖性论证
  debate_transcript.md(debate 默认开启)
```

### Experiment Agent standalone

```
输入(人手写):
  experiment_plan.v1.md(只填 research_question + hypothesis,其它字段空)
预期输出:
  experiment_plan.v1.md(自动补全 variables / metrics / ablations)
  baseline_decision.json(进行了 RunArchive 查询)
```

### Coding Agent standalone

```
输入:
  纯文本需求:"在 libs/Model.py 加一个新的 Paper_Router_v2 类,
              使用 Top-2 hard routing,保持 forward 接口不变"
  target_lang: python
预期输出:
  code_spec.v1.md
  patch.diff(可应用)
  Gate 5 检查 AGENTS.md,因 forward 接口不变,通过
  lint pass / smoke test pass
```

### Execution Agent standalone

```
输入:
  batch_config.json(指定 4 个 ablation 配置)
  workspace/repos/<project> 有可执行代码
预期输出:
  4 个并发 run 启动
  前端 4 条 loss 曲线实时叠加
  每个 run 结束后产生 run_log.md + Baseline Fingerprint
```

### Writing Agent standalone

```
输入:
  上传 1 份 idea_proposal.md + 2 份 run_log.md
  deliverable_type: paper_fragment
  target_audience: NeurIPS reviewer
预期输出:
  paper_fragment.md(含 introduction / method / experiments / discussion 段)
  reviewer critique 痕迹(debate 默认开)
```

## 4. Schema 合规率测试

`backend/tests/schema/`:

- 对每种 schema 类型,准备 ≥ 20 条测试数据(混合 Agent 生成 + 合规人写 + 故意错的人写)
- 跑 `validator.py` 校验
- **指标**:Agent 生成的首次 schema 合规率 ≥ 95%

## 5. HITL Gate 测试

`backend/tests/gate/`:

| Gate | 测试用例 |
|---|---|
| 1 plan_finalized | Idea approved 后,RunGraph 自动暂停等 Experiment 启动确认 |
| 2 large_refactor | Coding 生成的 patch 修改 ≥ 5 文件 → 触发 Gate |
| 3 experiment_launch | batch_config 估算 GPU 小时 > 阈值 → 触发 Gate |
| 4 conclusion_output | Writing 完成后,落库前必须 approve |
| 5 baseline_compatibility | tool dispatch 时,patch 修改 `forward(x, stream_label)` 接口 → 触发 Gate(因 AGENTS.md 第 4 条禁止) |

每条用例:**Gate 必须正确触发,不触发或误触发都算失败**。

## 6. Baseline 复用测试

`backend/tests/baseline/`:

- 准备 10 个历史 Baseline Fingerprint 入 run_archive
- 喂入 1 个与某 Fingerprint 高度相似的新 plan
- 校验 baseline_matcher 返回的 match_score ≥ 0.85
- 校验自动触发 HITL Gate 询问"复用?"

**指标**:在准备好的对照集上,Baseline 复用召回率 ≥ 80%,精度 ≥ 90%。

## 7. 多实验并发测试

`backend/tests/integration/test_concurrent_execution.py`:

```
同时启动 6 个 SimulationJob
预期:
  - 全部成功调度(不超并发上限)
  - 前端 split view 6 个面板正确显示
  - WebSocket 6 个 channel 各自独立订阅,无串扰
  - GPU queue 正确分配(默认 3 池)
  - 第 7 个 job 提交时进入 queue,等前面有 slot 释放
```

## 8. 量化指标(V0 demo 期结束时统计)

| 指标 | 目标值 | 测量方式 |
|---|---|---|
| Schema 合规率 | ≥ 95% | `tests/schema/` 自动统计 |
| Baseline 复用召回率 | ≥ 80% | `tests/baseline/` 对照集 |
| 多实验并发上限 | 6 | `tests/integration/` |
| Pipeline e2e 跑通时长 | ≤ 4 小时(MOE-PIMC demo) | demo 时手动记录 |
| HITL Gate 误触发率 | ≤ 5% | `tests/gate/` |
| 沉淀资产数(运行 demo 期) | ≥ 50 | 统计 4 区 KB 写入 |
| `runs/` 完整性 | 100% | 9 子目录都非空 |

## 9. 边界用例(必测)

```
1. Agent 输出不通过 Schema → 前端正确高亮缺失字段,用户补全后通过
2. 人写的 md 缺 frontmatter → 提示模板 + 引导补全
3. Pipeline 跑到一半,用户改 v2 中间产物 → 后续 Agent 读 approved 版本
4. Coding Agent 触发 Gate 5(违反 baseline)→ 强制阻塞,显示违反的 AGENTS.md 条款
5. 同一 plan 在 run_archive 命中相似 baseline → 复用 / 重跑 / 修改三选一
6. 单 Agent standalone 模式,用户直接调 Coding Agent → 跳过 Idea/Experiment,接受人写需求
7. LLM API 失败 → 重试 3 次后 graceful degrade,不损坏 run state
8. 多实验并发,某 run 中途失败 → 不影响其它 run,失败 run 标记并允许独立重跑
9. KB 检索 0 命中 → Context 装载降级,task layer 只用上游 handoff
10. workspace/repos/ 真实代码仓离线 → repo_reader 报错,前端提示用户检查 repo_link
```

## 10. 文档与可交付物

V0 完成时还要交付:

- ✅ 这 5 份顶层 md(`README.md / CLAUDE.md / PRODUCT.md / DESIGN.md / ACCEPTANCE.md`)保持最新
- ✅ `docs/` 下补充:
  - `architecture.md`(图 + 文字,可参考之前 ChatGPT 画的图)
  - `agent_io_schema.md`(5 schema 详细字段说明 + 示例)
  - `run_lifecycle.md`(一次 run 从创建到归档的完整时序)
  - `frontend_ux.md`(P0 各页面交互规范)
- ✅ `projects/moe-pimc/AGENTS.md`(项目级硬约束)
- ✅ `templates/code_rules/pimc_python.md`(代码规范模板)
- ✅ `posttrain/README.md`(V1 占位说明)
- ✅ Demo 录屏(MOE-PIMC 全链路,≤ 10 分钟)

## 11. 实现顺序(7 个 Phase)

⚠️ **Claude Code / Codex / Cowork 必须按顺序实现**。每个 Phase 完成后,跑该 Phase 的 acceptance 子集再进下一个。一开始铺得太大是 V0 失败的最常见原因。

### Phase 0 — Repo Scaffold(0.5–1 天)

1. 创建 monorepo 结构(按 `CLAUDE.md` 目录树)
2. 配 `pyproject.toml / package.json / docker-compose.yml / .env.example`
3. backend / frontend 最小启动(hello world API + 空白 Next.js 页面)
4. CI 配 `mypy --strict` / `import-linter` / pytest
5. 5 份顶层 md(README/CLAUDE/PRODUCT/DESIGN/ACCEPTANCE)按本仓库的版本拷贝到位

**Phase 0 验收**:`docker compose up -d` 能起所有服务,前端能打开空页面,后端 health check 返回 200。

### Phase 1 — Schema + Artifact + Run Lifecycle(2–3 天)

1. 实现 5 个 JSON Schema(`harness/schema/schemas/*.json`)
2. `frontmatter_parser.py` + `validator.py`
3. Artifact versioning(`storage/artifact_store.py`)
4. `runs/<timestamp>_<task>/` 目录写入(`storage/run_store.py`)
5. `.approved.md` 规则
6. 单测:5 schema 各 20 条样本(混合 valid / invalid)

**Phase 1 验收**:能从 CLI 喂入 md → 校验 → 写入 runs/。`tests/schema/` 100% pass。

### Phase 2 — Bridge + RunGraph(2–3 天)

1. `harness/runtime/run_graph.py`(通用图数据结构 + 拓扑排序调度)
2. `bridge/agent_registry.py`(反转依赖)
3. `harness/runtime/state_machine.py`(pending/running/waiting_review/approved/failed/done)
4. `harness/runtime/event_bus.py`(Redis pub/sub)
5. `bridge/orchestrator.py` + `bridge/workflow_service.py`
6. WebSocket 事件推送骨架
7. 单测:RunGraph 任意拓扑、Bridge 不直接 import agents

**Phase 2 验收**:能创建一个空 RunGraph,模拟 Agent state 转换,前端 WS 收到事件。

### Phase 3 — LLM Backend + Agent Skeletons(3–4 天)

1. `harness/llm/provider_base.py` + 4 个真实 provider(anthropic/openai/qwen/gemini)
2. `harness/llm/mock_provider.py`(★ 关键,兜底)
3. `harness/llm/local_vllm_provider.py`
4. `harness/llm/post_training_loader.py`(load-only)
5. `harness/llm/model_registry.py`(从 `agents.yaml` 路由)
6. 5 个 Agent 的 `agent.py`(继承 BaseAgent,内部用 LangGraph subgraph)
7. `agents/debate/` 完整实现
8. 配置缺失 → fallback mock 的逻辑

**Phase 3 验收**:5 个 Agent 都能 standalone 调用,无 API key 时自动用 mock_provider 跑通。

### Phase 4 — HITL 前端 + Agent Workbench(3–4 天)

1. 前端 6 入口卡片(Dashboard)
2. `AgentWorkbench` 页面:Markdown editor + version timeline + diff viewer
3. `Approve / Reject / Regenerate` 按钮 + 后端联通
4. Gate 触发的 modal
5. WS 状态订阅(单 run channel)

**Phase 4 验收**:前端能完整完成一次"Idea Agent draft → 人工编辑 v2 → approve"流程。

### Phase 5 — Context + KB + Sedimentation(3–4 天)

1. `harness/context/` 三层 loader + Manifest writer
2. `harness/kb/` 4 区 ChromaDB client + ingester + retriever
3. `harness/kb/baseline_matcher.py` + `fingerprint.py`
4. `harness/sedimentation/` hooks + 5 个 extractor
5. `templates/code_rules/pimc_python.md`(项目 baseline 规则模板)
6. 5 个 HITL Gate 实现,Gate 5 hook 进 `tools/registry.py` dispatch

**Phase 5 验收**:跑一次 Idea→Experiment 链路,Context Manifest 写入正确,沉淀写到 methodology KB,Gate 5 能基于 AGENTS.md 触发。

### Phase 6 — Execution Monitor + Mock Simulation(2–3 天)

1. `execution/simulation_runner.py` + `batch_runner.py`
2. `execution/mock_simulation.py`(★ 关键,无 GPU 兜底)
3. `execution/log_streamer.py` + `metrics_collector.py` + `curve_parser.py`
4. 前端 `MultiExperimentView`:6 组并排 + log/loss 开关 + 曲线叠加
5. WS per-experiment channel
6. Baseline Fingerprint 写入 RunArchive

**Phase 6 验收**:启动 6 个 mock simulation,前端 split view 实时显示,run 结束后 Fingerprint 入库。

### Phase 7 — End-to-End Demo(1–2 天)

跑通 ACCEPTANCE.md §2 完整 11 步主脚本:

1. Idea standalone with debate → 人工编辑 → approve
2. Experiment Agent + Baseline matcher 触发 HITL 复用决策
3. Coding Agent 生成 patch + Gate 5 检查
4. Execution Agent 6 路并发 mock simulation
5. Writing Agent 综合输出 report.md

**Phase 7 验收**:完整 demo 在零外部依赖(无 LLM API key、无 GPU)下,docker compose up 后单条命令跑通,产出完整的 `runs/<timestamp>_pimc_demo/` 目录。

---

**总工期估算:V0 全部 7 Phase 约 2.5–3 周**(单人全职,包含调试)。

## 12. 完成后输出报告(交付清单)

实现完成时,Claude Code / Codex 必须在 `docs/implementation_report.md` 输出以下报告(按此模板):

```markdown
# MARS V0 Implementation Report

## 1. Summary
做了什么。每个 Phase 的实际完成情况。

## 2. How to run
```
git clone <repo>
cd mars
cp .env.example .env  # 可不填 API key,自动 fallback mock
docker compose up -d
# 前端:http://localhost:3000
# 后端:http://localhost:8000
# 运行 demo:scripts/run_demo.sh
```

## 3. Repository structure
实际生成的目录树(对照 CLAUDE.md 目录约束)。

## 4. Requirement mapping
| 需求(来自 PRODUCT/DESIGN/ACCEPTANCE) | 实现位置 |
|---|---|
| 5 Agent 双形态 | backend/app/agents/, bridge/workflow_service.py |
| Schema 校验 | harness/schema/ |
| 5 HITL Gate | harness/gates/ + tools/registry.py(Gate 5 hook) |
| Multi-model debate | agents/debate/ |
| 4 区 KB | harness/kb/ + knowledge/ |
| Baseline 复用 | harness/kb/baseline_matcher.py |
| 三层上下文 | harness/context/ |
| Mock provider | harness/llm/mock_provider.py |
| Mock simulation | execution/mock_simulation.py |
| 前端 P0 | frontend/src/pages/, features/ |
| ... | ... |

## 5. Demo flow
完整记录 docker compose up 后到 demo 跑通的命令序列与预期产出。

## 6. Tests run
- 单测覆盖率:X%
- import-linter:pass / fail
- mypy --strict:pass / fail
- e2e demo:pass / fail
- 各 Phase 子验收:逐项 ✓/✗

## 7. Known limitations
- 哪些是 mock(明确列出)
- 哪些是 skeleton(未来要接真实系统)
- 哪些 schema 字段是占位(LLM 没生成完整内容)

## 8. Risks
- 架构 / 依赖 / 性能 / 安全风险

## 9. Suggested next X
- 建议 V1(Posttrain 训练流水线)X 文档应该聚焦什么
- 当前 V0 哪些抽象不够稳,V1 之前要重构
```

## 13. Acceptance 校验脚本

V0 完成时,以下命令必须全部通过(写在 `scripts/acceptance.sh`):

```bash
#!/bin/bash
set -e

# 1. 静态检查
mypy --strict backend/
import-linter --config .importlinter

# 2. 单测
pytest backend/tests/unit/
pytest backend/tests/schema/ --cov-fail-under=95   # schema 合规率
pytest backend/tests/gate/
pytest backend/tests/unit/test_tools_hardening.py \
  backend/tests/unit/test_search_tools_v1.py \
  backend/tests/unit/test_execution_tools_v1.py
pytest backend/tests/baseline/

# 3. 集成测试
pytest backend/tests/integration/

# 4. 前端 / Context Workbench
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend test:context

# 5. e2e demo(零外部依赖)
python scripts/run_demo_inprocess.py \
  --mock-mode \
  --task acceptance_demo \
  --run-id-file /tmp/mars-acceptance-run-id
RUN_ID="$(cat /tmp/mars-acceptance-run-id)"

# 6. 检查 runs/ 完整性 + tools/context 审计
ls runs/*/idea/ runs/*/experiment/ runs/*/coding/ \
   runs/*/execution/ runs/*/writing/ runs/*/hitl/ runs/*/events/
python scripts/verify_tools_v1_acceptance.py --run-id "$RUN_ID" --in-process
test -f "runs/$RUN_ID/context/context_manifest.v2.json"
find "runs/$RUN_ID/context" -name 'context_manifest.v2.*.json' | wc -l
python - "$RUN_ID" <<'PY'
import sys
from fastapi.testclient import TestClient
from app.main import create_app

response = TestClient(create_app()).get(f"/api/context/runs/{sys.argv[1]}")
assert response.status_code == 200
assert response.json()["budget_summary"]["manifest_count"] >= 5
PY

echo "✅ V0 + Tools V1 + Context Workbench acceptance passed"
```

CI 必须每次 PR 跑这个脚本,全绿才能 merge。
