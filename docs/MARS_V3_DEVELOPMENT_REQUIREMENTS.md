# MARS V3 开发需求文档

> 文档类型：Development Requirements Document（DRD）
> 文档状态：Draft，待产品、算法、平台与领域负责人评审
> 适用范围：MARS V3.0–V3.2
> 最后更新：2026-08-15
> 架构图：[`mars-v3-latest-architecture-editable.svg`](./mars-v3-latest-architecture-editable.svg)

## 0. 文档声明

本文定义 MARS V3 的开发范围、模块职责、功能需求、数据契约、验收标准和实施顺序。

本文是**开发需求和验收依据**，不是已实现证明。只有代码、配置、测试、运行产物和验收报告同时存在，相关功能才能标记为完成。

规范词含义：

- **必须（MUST）**：不满足则版本不能发布。
- **应该（SHOULD）**：原则上实现，若延期必须记录原因、风险和替代方案。
- **可以（MAY）**：可选增强，不阻塞当前版本发布。

文档优先级：

1. `AGENTS.md` 的硬约束最高。
2. `PRODUCT.md`、`DESIGN.md`、`ACCEPTANCE.md` 定义当前 V2 基线。
3. 本文定义 V3 相对 V2 的增量需求。
4. `MARS_V3_WIRELESS_LIGHTWEIGHT_DISCOVERY_PLAN.md` 和论文调研材料作为设计依据，不替代本文验收条款。

---

## 1. 产品定义

### 1.1 一句话定义

> MARS V3 是面向 PIMC、DPD 等无线算法研发的可信模型发现系统：在保留 V2 Commander、五个专业 Agent 和 Harness 的基础上，通过假设发现、候选进化、确定性评测、多目标归档和人工审批，持续产生可复现、可比较、可回滚的轻量模型候选。

### 1.2 核心产物

V3 的核心产物不是“自动生成的论文”或“最高分代码”，而是完整证据闭环：

```text
冻结研究目标与评测协议
  → 生成和筛选研究假设
  → 生成可执行模型候选
  → Preflight 执行前检查
  → 隔离训练/仿真/Profiling
  → 外部确定性评测
  → Pareto/MAP-Elites/失败档案
  → sealed holdout 与人工审批
  → 模型、报告和研究资产沉淀
```

### 1.3 目标用户

- 无线算法研究员：定义目标、约束、baseline 和数据协议，审查假设与模型候选。
- 模型工程师：维护候选表示、模型生成算子、训练与导出流程。
- Agent/Harness 工程师：维护运行时、上下文、工具、Gate、Trace、Replay 和 Eval。
- 领域负责人：确认指标语义、baseline 公平性、隐藏集和最终研究结论。

---

## 2. V3 核心设计决策

### 2.1 保留 V2 主体

V3 必须保留：

- Commander 主入口和系统级结果诊断循环；
- Idea、Experiment、Coding、Execution、Writing 五个专业 Agent；
- RunGraph、Schema、Artifact、Context Compiler、Memory、ToolRegistry；
- Gate 1–5、HITL、Trace、Replay、Evaluation 和完整 run 沉淀；
- mock 模式下的零外部依赖 E2E。

### 2.2 新增但不增加固定 Agent 数量

V3 新增：

- Idea Agent 内部的 Co-Scientist 式深度假设发现模式；
- Bridge 层 `DiscoveryService`；
- Harness 层通用 `Model Discovery Core`；
- Candidate、Lineage、Archive、Budget、Novelty 和 Promotion 数据面；
- PIMC、DPD Project Pack 与执行适配器；
- Discovery、Pareto、Lineage、Budget、Quarantine 和 Registry UI。

以下模块**不是第六个固定 Agent**：

- `DiscoveryService` 是 Bridge 产品控制服务；
- `Model Discovery Core` 是 Agent-agnostic 算法与状态能力；
- Generation、Reflection、Ranking、Proximity、Evolution、Meta-review 是 Idea Agent 内部角色。

### 2.3 三类循环的职责边界

| 循环 | 负责人 | 触发条件 | 主要输出 |
|---|---|---|---|
| 系统级结果反馈 | Commander | 一次 Execution 结果不达标或出现失败 | `diagnosis.v1`、feedback packet、重试目标 |
| 假设发现循环 | Idea Agent | 用户选择深度发现模式 | Top-K 假设、审查、Elo/相似图、最终 `proposal.v1` |
| 模型候选循环 | DiscoveryService + Discovery Core | Experiment Contract 已冻结 | Candidate、Evaluation、Archive、Promotion 记录 |

Commander 决定“做什么、让谁做、何时停止”；DiscoveryService 控制候选任务生命周期；专业 Agent 执行；外部 Evaluator 决定候选是否满足客观指标。

---

## 3. 范围与非目标

### 3.1 V3.0 必须范围

- V2 全链路和 Commander 反馈循环保持兼容。
- `DiscoveryService` 的创建、暂停、恢复、停止和重放。
- Candidate、Lineage、Evaluation、Archive、Budget 基础数据面。
- PIMC 配置级候选生成。
- Preflight、Gate 5、沙箱和外部确定性 Evaluator。
- F0/F1/F2 多保真晋级。
- Candidate、Budget、Pareto、Lineage、Quarantine 最小 UI。
- mock discovery E2E 和 PIMC CPU smoke。

### 3.2 V3.1 应该范围

- Idea Agent 的完整 Co-Scientist 式深度发现模式。
- Shinka 式 parent sampling、LLM bandit、novelty rejection 和 crossover。
- 受控结构组合与受限代码 patch。
- 多 seed、sealed holdout、Model/Dataset Registry。
- 正式 PIMC 研究协议与同预算随机/best-of-N 对照。

### 3.3 V3.2 应该范围

- DPD Project Pack、数据协议、指标和适配器。
- 量化、剪枝、蒸馏和部署候选。
- 编译器、Remote GPU、目标设备和 HIL Profiling。
- 质量—复杂度—时延—功耗多目标分析。

### 3.4 明确非目标

- 不实现 GRPO、偏好对训练或在线更新基础模型。
- 不允许 Runtime、Evaluator、Gate、baseline 或 hidden holdout 自我修改。
- 不让高分候选自动 merge、自动发布或自动进入长期 Memory。
- 不使用 LLM judge 替代无线性能指标和统计验证。
- 不从单 seed、best case、F1 快速评测生成正式研究结论。
- 不把真实研究代码或原始敏感数据复制进 MARS 仓库。

---

## 4. 总体架构与模块归属

### 4.1 依赖方向

```text
frontend → api → bridge → agents
bridge / agents → harness
bridge → execution adapter registry
project pack → external repo/data/environment
```

必须满足：

- `harness/` 禁止 import `agents/` 或 `bridge/`。
- Bridge 必须通过 `agent_registry.py` 调用 Agent，不直接 import 具体 Agent 类。
- 通用 Discovery Core 不得硬编码 `pimc`、`dpd` 或具体指标名。
- 领域规则必须位于 `projects/<name>/` 的配置、AGENTS 或 adapter 中。
- RunGraph 继续保持 DAG；循环通过带 iteration/attempt 的子图或 child run 展开，不在 Harness 中创建图环。

### 4.2 建议新增目录

```text
backend/app/
├─ bridge/
│  ├─ discovery_service.py          # Discovery Run 生命周期和循环控制
│  ├─ discovery_workflow.py         # iteration 子图构造
│  └─ discovery_tools.py            # Commander 可调用的 bridge-only tools
├─ agents/idea/discovery/
│  ├─ workflow.py                   # Idea Agent 内部深度假设循环
│  ├─ generation.py
│  ├─ reflection.py
│  ├─ ranking.py
│  ├─ proximity.py
│  ├─ evolution.py
│  └─ meta_review.py
├─ harness/discovery/
│  ├─ models.py                     # Candidate/Evaluation/Budget/Archive records
│  ├─ candidate_builder.py          # ModelGenome delta 应用与校验
│  ├─ sampling.py                   # parent sampling，纯算法
│  ├─ novelty.py                    # hash/AST/behavior/embedding
│  ├─ archive.py                    # Pareto/MAP-Elites/negative archive
│  ├─ budget.py                     # 原子预算账本
│  ├─ promotion.py                  # 多保真晋级
│  └─ stopping.py                   # 预算、patience 和安全停止
├─ execution/adapters/
│  ├─ base.py
│  ├─ pimc.py
│  ├─ dpd.py
│  ├─ remote_gpu.py
│  └─ device_profile.py
└─ storage/
   ├─ candidate_store.py
   ├─ dataset_registry.py
   └─ model_registry.py

projects/
├─ pimc/
│  ├─ discovery.yaml
│  ├─ metrics.yaml
│  └─ workflow.yaml
└─ dpd/
   ├─ AGENTS.md
   ├─ project.yaml
   ├─ repo_link.yaml
   ├─ discovery.yaml
   ├─ metrics.yaml
   └─ workflow.yaml
```

### 4.3 Schema 兼容规则

五个 Agent 的正式下游产物必须继续使用：

- Idea：`proposal.v1`
- Experiment：`experiment_plan.v1`
- Coding：`code_spec.v1`
- Execution：`run_log.v1`
- Writing：`report.v1`

Candidate、Evaluation、Budget、Archive 可以使用版本化 Pydantic/JSON 系统记录，例如 `candidate.v1`，但它们不是新增 Agent 输出类型，也不得绕过现有 Artifact 与审计链。

Commander 已有的 `diagnosis.v1`、`feedback_packet.v1` 以及系统评测记录继续保留；它们属于 Bridge/系统产物，不改变五个专业 Agent 的正式输出契约。

---

## 5. 端到端工作流需求

### 5.1 标准研究工作流

```text
用户 → Commander → Idea → Experiment → Coding → Execution → Writing
```

如果 Execution 不达标：

```text
Execution
  → Commander 读取 metrics/curves/logs
  → 生成 diagnosis 与 feedback packet
  → 定位 Experiment 或 Coding
  → 追加新 attempt
  → Execution 再评测
```

该 V2 功能必须保留。达到迭代上限、诊断置信度不足或目标反复切换时，必须暂停并请求人工处理。

### 5.2 Idea Agent 深度假设发现工作流

```text
Generate
  → Reflect
  → Proximity 聚类/去重
  → Debate + Elo Ranking
  → Evolve
  → Meta-review
  → 下一轮 Generate
  → 科学家选择
  → proposal.v1
```

该流程属于 Idea Agent 内部模式，不改变五 Agent 主拓扑。

### 5.3 模型候选发现工作流

```text
Experiment Contract 冻结
  → Shinka 策略选父代/LLM/算子
  → Coding 生成 ModelGenome delta/config/graph/diff
  → Preflight
  → Execution 隔离训练/仿真/Profiling
  → 外部 Deterministic Evaluator
  → Archive / Quarantine / Dominated
  → 优秀候选成为下一轮父代
  → F0–F4 晋级
  → sealed holdout
  → HITL approval
```

---

## 6. 功能需求

### 6.1 Commander 与系统级反馈

| ID | 优先级 | 需求 |
|---|---:|---|
| CMD-001 | P0 | Commander 必须识别标准 Pipeline、Standalone 和 Discovery Run 意图。 |
| CMD-002 | P0 | Commander 必须保留现有 Execution 后结果诊断能力。 |
| CMD-003 | P0 | 诊断必须读取 canonical metrics、曲线摘要、日志摘要、Gate、approved artifacts 和 attempt history。 |
| CMD-004 | P0 | 失败时必须生成版本化 `diagnosis.v1` 和有界 feedback packet。 |
| CMD-005 | P0 | Commander 只允许将自动修复目标路由到受支持 Agent；低置信度或目标反复变化时必须 HITL。 |
| CMD-006 | P0 | Commander 必须支持 Discovery Run 的 create/start/status/pause/resume/stop。 |
| CMD-007 | P0 | Commander 负责系统级预算与停止决策，不直接实现 candidate mutation、parent sampling 或 metric evaluator。 |
| CMD-008 | P1 | Commander 应汇总 Pareto、预算、停机原因、风险和待审批候选。 |

### 6.2 Idea Agent：Co-Scientist 式深度发现

| ID | 优先级 | 需求 |
|---|---:|---|
| IDEA-001 | P0 | Idea Agent 必须保留现有单提案快速模式。 |
| IDEA-002 | P1 | Idea Agent 应新增可配置 `deep_discovery` 模式。 |
| IDEA-003 | P1 | Generation 必须结合 ResearchPack、文献、历史 run、代码 baseline 和科学家输入生成多个不同机制假设。 |
| IDEA-004 | P1 | Reflection 必须检查正确性、新颖性、关键前提、可证伪性、资源公平性和潜在失败点。 |
| IDEA-005 | P1 | Ranking 必须支持可审计的成对比较、多轮辩论和 Elo 更新。 |
| IDEA-006 | P1 | Proximity 必须生成相似度图、cluster id 和去重判定；不得只按措辞差异判断多样性。 |
| IDEA-007 | P1 | Evolution 必须支持补强、组合、简化和发散生成；原假设不可被原地覆盖。 |
| IDEA-008 | P1 | Meta-review 必须总结重复错误、成功模式和未覆盖区域，并作为下一轮提示上下文。 |
| IDEA-009 | P1 | Elo 只能作为资源分配信号，不得标记为科学真实性或实验通过。 |
| IDEA-010 | P1 | 科学家必须能添加假设、修改目标、提交 review、停止循环和选择最终候选。 |
| IDEA-011 | P1 | 中间假设、review、match 和 proximity graph 必须保存在本次 run；最终选中项输出合法 `proposal.v1`。 |
| IDEA-012 | P1 | Meta-review 和中间经验默认不得直接写长期 Memory；只有人工批准后才能进入 governed memory。 |

### 6.3 DiscoveryService

| ID | 优先级 | 需求 |
|---|---:|---|
| DISC-001 | P0 | DiscoveryService 必须位于 Bridge，通过 agent registry 调用五个 Agent。 |
| DISC-002 | P0 | 启动前必须冻结 task、dataset、baseline、metric、evaluator、search space 和 budget contract，并计算 hash。 |
| DISC-003 | P0 | 影响公平性的 contract 变更必须创建新 run/version，禁止静默覆盖。 |
| DISC-004 | P0 | 必须支持 create/start/pause/resume/stop/crash-recovery。 |
| DISC-005 | P0 | 每个 iteration 必须展开为可重放 DAG 节点或 child run。 |
| DISC-006 | P0 | 必须保证重复 resume 幂等，不重复生成节点、不重复扣减预算。 |
| DISC-007 | P0 | 必须接收 Core 的 stop reason，并交由 Commander/UI 显示。 |

### 6.4 Candidate 与 ModelGenome

| ID | 优先级 | 需求 |
|---|---:|---|
| CAND-001 | P0 | 每个候选必须具备唯一 `candidate_id`、`run_id`、`parent_ids`、generation、iteration。 |
| CAND-002 | P0 | 每个候选必须记录 creator agent、LLM、prompt hash、context manifest 和生成算子。 |
| CAND-003 | P0 | 每个候选必须引用 base commit/snapshot、config/diff、checkpoint、log 和 evaluation artifacts。 |
| CAND-004 | P0 | ModelGenome 必须表示模型家族、结构、关键超参、训练/拟合 recipe 和允许演化区域。 |
| CAND-005 | P0 | 候选不得直接修改 live 外部研究仓；必须使用 commit-pinned snapshot、worktree 或隔离容器。 |
| CAND-006 | P0 | 候选不得修改 baseline、production interface、evaluator、hidden tests、Gate 或审计代码。 |
| CAND-007 | P0 | 候选必须经历 Draft→Validated→Queued→Running→Evaluated 等显式状态迁移。 |
| CAND-008 | P0 | 所有状态迁移必须持久化、可审计并具备幂等写入语义。 |

### 6.5 候选生成

候选生成器必须接收：

- 已冻结的 Experiment/Search Contract；
- 一个主父代；
- 可选 inspiration 候选；
- 父代公开 dev 指标和失败摘要；
- 允许修改的路径、参数、接口和 evolution zone；
- 剩余 token/GPU/wall-time/proposal 预算；
- Meta-scratchpad 中已验证的建议。

| ID | 优先级 | 需求 |
|---|---:|---|
| GEN-001 | P0 | V3.0 仅允许生成结构化 config patch。 |
| GEN-002 | P1 | V3.1 可以生成已注册 block 的 graph/composition delta。 |
| GEN-003 | P1 | V3.1 可以生成受限 SEARCH/REPLACE 或 unified diff。 |
| GEN-004 | P1 | Full rewrite 只能作用于明确标记的 evolution block，必须证明不可变区未变。 |
| GEN-005 | P1 | Crossover 只能组合接口兼容、Project Pack 允许的父代。 |
| GEN-006 | P0 | 生成失败必须记录解析错误和修复次数；达到上限后候选 Rejected，不进入 Execution。 |

### 6.6 Shinka 搜索策略

| ID | 优先级 | 需求 |
|---|---:|---|
| SRCH-001 | P1 | Parent sampler 应综合 Pareto/niche 质量、探索稀缺度、offspring count、recency 和 uncertainty。 |
| SRCH-002 | P1 | 必须保留可配置的随机探索比例，避免始终选择 top-1。 |
| SRCH-003 | P1 | LLM selector 应使用 UCB 或 Thompson Sampling，根据相对父代的有效改进更新。 |
| SRCH-004 | P1 | LLM reward 不得读取 sealed holdout，且应记录 token、成本和重复候选惩罚。 |
| SRCH-005 | P1 | Operator selector 应在 config mutation、graph mutation、code diff、rewrite 和 crossover 中按版本能力选择。 |
| SRCH-006 | P1 | 所有抽样必须记录 seed、候选集合、概率/分数和最终选择理由。 |
| SRCH-007 | P1 | Meta-scratchpad 只保存可验证的变异假设和结果，不保存未经审查的经验真理。 |

### 6.7 Preflight 执行前检查

Preflight 的目标是在消耗训练/GPU 资源前淘汰无效、重复、违规或高风险候选。

| ID | 优先级 | 需求 |
|---|---:|---|
| PRE-001 | P0 | 必须校验 Candidate schema、必填字段和 artifact refs。 |
| PRE-002 | P0 | 必须校验允许路径、禁止路径、symlink/path traversal 和 worktree 边界。 |
| PRE-003 | P0 | 必须执行 parse/AST、接口、shape、type、lint 或等价静态检查。 |
| PRE-004 | P0 | 必须经 ToolRegistry dispatch 和 Gate 5 baseline compatibility 检查。 |
| PRE-005 | P0 | 必须检查 secret、网络、hidden holdout、evaluator 和审计路径访问。 |
| PRE-006 | P0 | 必须按 exact hash→normalized AST→behavior fingerprint→embedding 分层去重。 |
| PRE-007 | P0 | 任一 blocker 失败时不得排队 Execution；必须记录明确 rejection reason。 |
| PRE-008 | P1 | Preflight 应提供静态参数量、MACs、内存和兼容性估计。 |

### 6.8 Execution 与确定性 Evaluation

| ID | 优先级 | 需求 |
|---|---:|---|
| EXEC-001 | P0 | 每个候选必须在独立工作区或容器执行，默认断网、非 root、只读数据。 |
| EXEC-002 | P0 | 必须设置 wall-time、CPU/GPU、内存、磁盘和并发限制。 |
| EXEC-003 | P0 | 必须持续采集 stdout/stderr、结构化事件、曲线、checkpoint 和资源指标。 |
| EXEC-004 | P0 | evaluator 必须由候选进程外部的可信进程执行。 |
| EVAL-001 | P0 | 每次评测必须记录 evaluator hash、dataset/split hash、seed、环境和硬件。 |
| EVAL-002 | P0 | 必须同时保存 raw metrics 和 canonical metrics，明确 unit、direction、aggregation。 |
| EVAL-003 | P0 | NaN、Inf、缺失 metric、非法范围或未通过硬约束的候选不得成为 elite。 |
| EVAL-004 | P0 | LLM finding 只能是 advisory，不得覆盖确定性 metric 和 blocker。 |
| EVAL-005 | P1 | 正式比较必须支持 same-budget、multi-seed、paired protocol 和置信区间。 |

### 6.9 多保真、Archive 与晋级

| 层级 | 内容 | 使用边界 |
|---|---|---|
| F0 | Schema、Patch、Compile、Shape、接口、静态复杂度 | 所有候选必过 |
| F1 | 小数据、短 epoch、低分辨率仿真 | 只用于淘汰 |
| F2 | 完整 dev 数据和正式训练预算 | 形成 dev Pareto |
| F3 | 多 seed、sealed holdout、paired protocol | 只用于晋级确认 |
| F4 | 编译器、目标设备、HIL、真实测量 | 部署结论 |

| ID | 优先级 | 需求 |
|---|---:|---|
| ARCH-001 | P0 | Archive 必须保存 Pareto elites，而不是只保留 top-1。 |
| ARCH-002 | P1 | 应按模型家族、复杂度、场景和硬件维护 MAP-Elites niches。 |
| ARCH-003 | P0 | 必须保存 negative archive：失败、超时、退化、违规和 hack 原因。 |
| ARCH-004 | P0 | 必须保存完整 lineage：父代、算子、生成器、评测、晋级和人工决策。 |
| ARCH-005 | P0 | 异常高分、证据缺失、疑似 evaluator hack 的候选必须进入 Quarantine。 |
| PROM-001 | P0 | 晋级规则必须来自冻结 contract，不得在看到结果后静默修改。 |
| PROM-002 | P0 | F3/F4 结果不得回灌搜索生成器。 |
| PROM-003 | P0 | 最终 promote 必须经过人工批准，且产物 hash 完整。 |

### 6.10 效果不好时的迭代

候选效果不好时，系统必须：

1. 保存 raw result、failed metrics 和失败类型；
2. 将候选写入 Dominated、Failed 或 Quarantine，而不是删除；
3. 生成有界 failure summary；
4. 更新 parent/LLM/operator 的公开 dev 统计；
5. 下一轮重新选择父代、LLM 或变异算子；
6. 不允许通过修改 evaluator、降低 baseline 或查看 holdout 来“修复”结果。

| ID | 优先级 | 需求 |
|---|---:|---|
| ITER-001 | P0 | 失败反馈必须引用证据，不得只给自然语言主观判断。 |
| ITER-002 | P0 | 下一轮上下文只注入最新失败摘要和必要 refs，不注入全部历史日志。 |
| ITER-003 | P1 | 失败类型应影响后续 parent/LLM/operator 选择，但不得自动形成永久知识。 |
| ITER-004 | P0 | 连续 N 个有效候选无 Pareto/niche 改进时必须触发 patience stop。 |

### 6.11 Budget 与停止策略

| ID | 优先级 | 需求 |
|---|---:|---|
| BUD-001 | P0 | 必须分别记录 proposals、LLM tokens、GPU seconds、wall time、API cost 和并行度。 |
| BUD-002 | P0 | 预算扣减必须原子、幂等，resume 不得重复扣减。 |
| BUD-003 | P0 | 任一硬预算耗尽必须停止继续生成，并保留已完成评测。 |
| STOP-001 | P0 | 预算耗尽、patience 到期、未缓解 hack、holdout 泄漏、baseline mutation 必须停止。 |
| STOP-002 | P0 | 停止必须记录 machine-readable reason、最后一致状态和可生成报告。 |
| STOP-003 | P0 | 人工必须能安全停止，已运行任务按策略取消或排空。 |

### 6.12 HITL 与 Memory

| ID | 优先级 | 需求 |
|---|---:|---|
| HITL-001 | P0 | 用户必须能 review/edit/approve/reject proposal、contract、candidate 和 promotion。 |
| HITL-002 | P0 | 所有人工决定必须记录 actor、timestamp、reason 和 artifact version。 |
| HITL-003 | P0 | baseline、holdout、evaluator、Gate、长期 Memory 和最终 promote 必须具备人工控制边界。 |
| MEM-001 | P0 | run-local 候选、Meta-review 和失败经验默认只留在 run。 |
| MEM-002 | P0 | 只有 approved/active memory 才能进入未来 Agent Context。 |
| MEM-003 | P0 | 连续负反馈可以将既有 memory 标记为 stale，但不得无审计删除。 |

---

## 7. Project Pack 需求

### 7.1 通用 Project Pack Contract

每个项目必须提供：

- `project.yaml`：项目身份和能力声明；
- `repo_link.yaml`：外部代码接入方式和 commit/snapshot；
- `discovery.yaml`：搜索空间、允许算子、niche 和预算建议；
- `metrics.yaml`：raw/canonical 名称、单位、方向、聚合和范围；
- `workflow.yaml`：执行入口、多保真层级和 promotion；
- `AGENTS.md`：baseline、接口和领域硬约束；
- adapter：prepare、execute、collect、normalize、profile 接口。

### 7.2 PIMC V3.0

首个正式任务必须是 config-only evolution：

- 固定训练/仿真入口、数据 manifest、baseline 和 metric parser；
- 只允许白名单搜索 memory depth、order、rank、LUT knots、top-k、router 和安全训练超参；
- 保持受保护 baseline、`forward(x, stream_label)` 和公共对照不变；
- 指标必须覆盖取消质量、复杂度、时延、内存、稳定性和 seen/unseen；
- 正式结论必须多 seed，并保留 raw PIM/RES/APE/DTNF 等原始量及其签字后的 canonical 映射。

### 7.3 DPD V3.2

DPD 接入前必须完成：

- repo/data ownership、授权和 `repo_link.yaml`；
- MP/GMP/LUT/spline/compact-NN 等模型家族定义；
- NMSE、ACLR/ACPR、EVM、峰值误差和稳定性等指标语义确认；
- PA、波形、带宽、功率点和 train/dev/holdout 切分；
- 参数量、MACs、时延、内存和目标设备 profile；
- 同预算 baseline 和 anti-overfit 协议。

PIMC 与 DPD 必须共用同一 Candidate/Archive/Budget API；不得复制一套领域专用 Discovery Core。

---

## 8. API、CLI 与 UI 需求

### 8.1 API

计划接口：

```text
POST /api/discovery/runs
GET  /api/discovery/runs/{id}
POST /api/discovery/runs/{id}/start
POST /api/discovery/runs/{id}/pause
POST /api/discovery/runs/{id}/resume
POST /api/discovery/runs/{id}/stop
GET  /api/discovery/runs/{id}/candidates
GET  /api/discovery/runs/{id}/archive
GET  /api/discovery/runs/{id}/pareto
GET  /api/discovery/runs/{id}/lineage
GET  /api/discovery/runs/{id}/budget
GET  /api/discovery/candidates/{id}
POST /api/discovery/candidates/{id}/approve
POST /api/discovery/candidates/{id}/reject
POST /api/discovery/candidates/{id}/promote
GET  /api/models
GET  /api/datasets
GET  /api/hardware-profiles
```

所有写操作必须经过 Bridge、ToolRegistry、权限和 HITL；UI 不得直接写 Store 或项目仓库。

### 8.2 CLI

必须提供：

- 创建/校验 task contract；
- dry-run 和 preflight；
- start/pause/resume/stop；
- list/show candidate；
- replay candidate；
- export discovery report。

CLI 必须调用与 API 相同的 Bridge Service，不得实现第二套业务逻辑。

### 8.3 UI

V3.0 必须提供：

- Task Contract 与 Preflight 页面；
- Candidate Table 和状态/Gate；
- Pareto Scatter 和 niche coverage；
- Budget burn-down；
- Lineage、diff、metrics、logs、evidence drawer；
- Quarantine 和 HITL review queue；
- Commander 诊断、feedback packet 和 retry attempt 展示。

V3.1 应提供：

- Hypothesis Tournament；
- Elo、Proximity Graph、Reflection 和 Meta-review；
- Parent/LLM/operator 选择记录；
- Model/Dataset Registry。

UI 只能显示后端签名/版本化 scientific metrics，不得在浏览器重新计算正式指标。

---

## 9. 非功能需求

### 9.1 向后兼容

- NFR-COMPAT-001：V2 mock E2E、Standalone、Commander feedback、五 Agent Schema 和 Gate 必须继续通过。
- NFR-COMPAT-002：未启用 V3 配置时，现有 Pipeline 行为不得变化。
- NFR-COMPAT-003：V3 数据迁移必须支持旧 run 只读查看。

### 9.2 可复现性

- NFR-REPRO-001：每个正式候选必须能在 clean snapshot 重放。
- NFR-REPRO-002：必须保存代码、配置、数据、evaluator、环境、硬件和 seed hash。
- NFR-REPRO-003：Hosted LLM 不要求逐 token 复现，但候选 artifact 的执行必须可复放。

### 9.3 安全与治理

- NFR-SEC-001：默认拒绝网络、secret、holdout 和 evaluator 实现访问。
- NFR-SEC-002：路径、symlink、subprocess、tool dispatch 和 artifact 写入必须审计。
- NFR-SEC-003：高风险动作必须 HITL，且支持 rollback。

### 9.4 可靠性

- NFR-REL-001：暂停、崩溃和服务重启后必须从最后一致 checkpoint 恢复。
- NFR-REL-002：Event、Budget、Candidate State 和 Archive 更新必须幂等。
- NFR-REL-003：单个候选失败不得导致整个 Discovery Run 丢失。

### 9.5 可观测性

- NFR-OBS-001：所有 LLM call、tool call、candidate transition、evaluation、budget 和 HITL 必须有事件。
- NFR-OBS-002：必须能从 UI/API 追溯 Candidate→Parent→Prompt→Diff→Execution→Metric→Decision。
- NFR-OBS-003：每个正式 metric 必须有 evidence ref。

### 9.6 性能与扩展

- NFR-PERF-001：Discovery Core 必须支持异步生成和并行评测，但遵守 execution 并发上限。
- NFR-PERF-002：Context 必须使用摘要和 raw ref，禁止将完整历史塞进每轮 prompt。
- NFR-EXT-001：新增 Project Pack 不得修改通用 Core 的领域逻辑。

---

## 10. 核心数据记录

### 10.1 ResearchTaskContract

最少字段：

- `schema/version`、`run_id`、`project`、`objective`；
- allowed/forbidden paths 和 evolution zones；
- dataset/split/baseline/evaluator refs 与 hash；
- hard constraints 和 multi-objectives；
- proposal/token/GPU/wall-time/cost budget；
- seed、stop 和 promotion policy；
- owner、reviewer、created_at、frozen_at。

### 10.2 CandidateRecord

最少字段：

- candidate/run/parent/generation/iteration；
- kind、creator、model、operator、prompt/context hash；
- ModelGenome/config/diff/checkpoint/log refs；
- allowed zone、preflight 和 Gate results；
- exact/AST/behavior/embedding fingerprints；
- lifecycle state、timestamps、failure reason。

### 10.3 CandidateEvaluation

最少字段：

- evaluator/data/environment/hardware hash；
- fidelity、seed、raw/canonical metrics；
- hard constraints、uncertainty、confidence interval；
- token/GPU/wall-time/cost；
- hack findings、evidence refs 和 evaluator status。

### 10.4 ArchiveSnapshot

最少字段：

- snapshot id、run id、iteration 和 hash；
- Pareto front、MAP-Elites niches、negative archive；
- elite replacement reason；
- lineage refs；
- budget snapshot 和 stop status。

---

## 11. 验收场景

### AC-01：V2 Commander 反馈循环不回归

给定一个不达标的 synthetic execution，系统必须：

1. 生成合法 diagnosis；
2. 定位 Experiment 或 Coding；
3. 非 auto-approve 模式进入 `waiting_feedback`；
4. 人工批准后追加新 attempt；
5. 保留旧 artifact 并重新 Execution；
6. 达到预算后停止并生成失败报告。

### AC-02：Idea Agent 深度发现

在 deterministic mock fixture 下，系统必须：

- 生成多个假设；
- 为每个假设生成 Reflection；
- 产生 Proximity cluster、pairwise matches 和 Elo；
- 对高排名假设生成新的 Evolution child；
- 产生 Meta-review 并回灌下一轮；
- 人工选择后输出 Schema 合法的 `proposal.v1`。

### AC-03：Candidate 生成与谱系

从冻结 PIMC config contract 启动后，每个候选必须拥有父代、生成器、operator、config delta、Preflight、Evaluation 和预算 refs。

### AC-04：Preflight 阻断

以下候选必须在 Execution 前被拒绝：

- 修改 baseline/evaluator/hidden tests；
- 越权路径或 symlink 逃逸；
- 接口或 shape 不兼容；
- Schema 缺失；
- 完全重复或已知行为重复；
- secret/network/holdout 违规。

### AC-05：效果差后的迭代

给定一个指标退化候选，系统必须保存失败证据、更新 dev 统计，并在下一轮改变父代、LLM 或 operator 中至少一项；不得修改 evaluator 或删除失败记录。

### AC-06：Archive 正确性

- Pareto front 不得包含被支配候选；
- NaN/Inf/缺失 metric 不得成为 elite；
- 同一 niche 只按冻结 replacement policy 更新；
- 插入顺序不应改变 deterministic snapshot。

### AC-07：Budget 与恢复

暂停、崩溃、重启和重复 resume 后：

- 不重复扣预算；
- 不重复创建候选；
- 已完成 Evaluation 不重复执行，除非明确 replay；
- stop reason 和最后一致状态可恢复。

### AC-08：Quarantine 与 Anti-Hack

看似高分但修改计时、样本数、metric JSON、baseline 或 evaluator 的 golden candidates 必须全部进入 Quarantine，不能进入正向 Archive。

### AC-09：Sealed Holdout

搜索阶段 holdout 访问次数必须为 0；只有 Promotion Policy 允许的候选才能进入 F3，结果不得反馈给生成器。

### AC-10：跨领域复用

PIMC synthetic adapter 和 DPD synthetic adapter 必须使用同一 Candidate/Archive/Budget API，Harness 中不得出现领域反向 import。

### AC-11：人工审批

Candidate、Model、Prompt/Memory 变更和最终 Promotion 必须具备 approve/reject、理由、版本、actor 和 rollback 记录。

### AC-12：端到端展示

零外部依赖下必须完成：

```text
创建 Discovery Run
→ 生成 mock candidates
→ Preflight
→ mock execution/evaluation
→ Archive/Quarantine
→ Budget/Lineage/Pareto UI
→ HITL promotion
→ Writing report
```

---

## 12. Release Gates 与质量目标

| Gate | 发布要求 |
|---|---|
| G0 Backward Compatibility | V2 E2E、Commander feedback、Schema、Gate、import contracts 全部通过 |
| G1 Contract Integrity | task/data/baseline/evaluator/budget 冻结且具备 hash |
| G2 Candidate Integrity | Candidate provenance 完整率 100% |
| G3 Sandbox & Anti-Hack | 无未处置路径、网络、holdout 或 evaluator 逃逸 |
| G4 Evaluation Validity | metric 方向、单位、聚合和 golden tests 全部通过 |
| G5 Reproducibility | top candidate clean replay 通过 |
| G6 Statistical Fairness | same-budget、multi-seed、失败率和 holdout 完整 |
| G7 Human Promotion | model/patch/memory/promotion 均经批准 |
| G8 Cross-Domain | PIMC/DPD 共用 Core，无领域反向 import |

建议质量目标，需 Phase 0 由 owner 批准：

- Agent Schema 合规率 ≥95%；
- Candidate provenance 完整率 100%；
- 正向 Archive 未处置 blocker 为 0；
- Top candidate clean replay 100%；
- Promotion artifact hash 覆盖 100%；
- Budget ledger 差额 ≤1%；
- 搜索期间 sealed holdout 访问次数为 0；
- 正式 metric evidence ref 覆盖 100%。

研究指标如 Pareto hypervolume、同质量成本下降、hidden gap 或人工接受率属于研究 KPI，不是平台发布硬门槛。负结果必须保留和报告。

---

## 13. 测试要求

### 13.1 Unit

- Candidate 状态迁移和非法迁移；
- Budget 原子扣减和幂等；
- Pareto dominance、niche replacement 和 archive snapshot；
- parent/LLM/operator sampling 固定 seed；
- exact/AST/behavior/embedding novelty；
- metric unit/direction/aggregation；
- stop、patience 和 promotion policy；
- Idea deep-discovery role contracts 和 Elo update。

### 13.2 Property/Fuzz

- Archive 插入顺序不改变 deterministic result；
- 任意 path/symlink 无法逃逸；
- resume 不双扣预算；
- NaN/Inf/缺失 metric 永不成为 elite；
- Candidate 无法构造修改 evaluator 的依赖路径；
- Proximity/去重不会覆盖原始假设和 lineage。

### 13.3 Integration

- Commander diagnosis→feedback→retry；
- Idea deep discovery→proposal.v1；
- task contract→candidate→Preflight→sandbox→evaluation→archive；
- pause/crash/restart→resume；
- multi-fidelity→holdout→HITL→Model Registry；
- PIMC/DPD synthetic adapters；
- Event stream→UI 恢复。

### 13.4 Adversarial

- baseline/evaluator/metric tampering；
- hidden holdout probing；
- runtime shortening、样本过滤和 metric JSON 伪造；
- secret/network/path/symlink 逃逸；
- 成本/时延测量投机；
- prompt injection 诱导绕过 Gate。

### 13.5 E2E 层级

1. 零外部依赖：mock PIMC/DPD，完整 Discovery Run；
2. 本机 CPU：PIMC cancellation smoke；
3. 外部只读 snapshot：短训练和正式 metric parser；
4. Remote GPU：checkpoint/resume/multi-seed；
5. Device/HIL：硬件和授权数据就绪后执行。

---

## 14. 分阶段开发计划

### Phase 0：基线冻结与 RFC

交付：

- V2 全量 release check 基线；
- Candidate/Evaluation/Budget/Archive RFC；
- PIMC metric、baseline、data split 和 evaluator 签字版；
- Discovery API 和状态机 RFC；
- threat model 与 anti-hack fixtures。

退出条件：所有 P0 数据契约和信任边界获批准。

### Phase 1：Discovery Core 最小闭环

交付：

- CandidateStore、Lineage、BudgetLedger；
- config-only candidate builder；
- Preflight；
- deterministic mock evaluator；
- Pareto/negative archive；
- Bridge DiscoveryService 和最小 API/CLI；
- 20-candidate mock E2E。

退出条件：AC-03、AC-04、AC-06、AC-07、AC-12 通过，同时 AC-01 不回归。

### Phase 2：PIMC Config Evolution

交付：

- PIMC Project Pack；
- PIMC CPU/static adapter；
- F0/F1/F2；
- Candidate/Lineage/Pareto/Budget/Quarantine UI；
- 同预算 random/best-of-N 基线。

退出条件：PIMC clean replay、metric golden tests 和 anti-hack suite 通过。

### Phase 3：Idea Deep Discovery + Shinka

交付：

- Generation/Reflection/Ranking/Proximity/Evolution/Meta-review；
- 科学家假设与 review 输入；
- weighted parent sampler；
- LLM bandit；
- novelty rejection；
- crossover 和 Meta-scratchpad。

退出条件：AC-02、AC-05 通过，并证明中间结果未自动污染长期 Memory。

### Phase 4：Formal Promotion 与 Registry

交付：

- F3 multi-seed/sealed holdout；
- Model/Dataset Registry；
- HITL promotion 和 rollback；
- discovery report 和 Writing 集成。

退出条件：G5、G6、G7 通过。

### Phase 5：DPD 与 Hardware-Aware Loop

交付：

- DPD Project Pack 和 synthetic/real adapters；
- F4 compiler/device/HIL；
- 量化、剪枝、蒸馏、部署候选；
- latency/energy/quality Pareto。

退出条件：G8 通过，目标设备测量具备完整 provenance。

每个 Phase 完成后，系统必须仍能运行端到端 demo；禁止各模块横向独立开发完后再集成。

---

## 15. Definition of Done

一个需求只有同时满足以下条件才能标记 Done：

- 代码进入正确依赖层，无反向 import；
- 配置、Schema/record 和迁移完成；
- unit、integration、adversarial 和相关 E2E 通过；
- mock 和真实适配路径按风险完成验证；
- API/CLI/UI 与后端状态一致；
- Trace、Event、Budget 和 evidence refs 完整；
- 文档、操作说明和已知限制更新；
- 相关 HITL、Gate 和 rollback 路径可用；
- 产物在 clean checkout/snapshot 可重放；
- 验收报告列出实际执行命令、测试结果和 artifact 路径。

不得以页面可见、Markdown 已生成、测试文件存在或单一 mock 通过作为功能完成证明。

---

## 16. 风险与待确认项

### 16.1 主要风险

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 指标方向或单位错误 | 搜索方向完全错误 | metric contract、golden tests、owner 签字 |
| evaluator/holdout 泄漏 | 结果失真 | 进程隔离、零搜索访问、审计和 adversarial tests |
| LLM 生成重复候选 | 浪费 token/GPU | 分层 novelty、行为指纹、negative archive |
| top-1 贪心早熟 | 陷入局部最优 | weighted sampling、niche、随机探索 |
| Meta-review 错误强化 | 长期污染 | run-local 默认、人工批准后入 Memory |
| live 研究仓污染 | 用户改动丢失或混入候选 | 只读 snapshot/worktree、manifest |
| 预算失控 | 成本和时间不可控 | 原子 BudgetLedger、硬停止、burn-down UI |
| 快速评测与正式结果错位 | 错误晋级 | F1 只淘汰、F2/F3 paired protocol |
| DPD 输入不完整 | 无法形成可信闭环 | Phase 0 前置清单和 synthetic adapter |

### 16.2 必须由负责人确认

- PIMC raw/canonical metric 的正式映射；
- PIMC baseline、public baseline 和 oracle 的分类；
- PIMC data split、正式 seed 和预算；
- DPD repo/data ownership 和授权；
- DPD PA/波形/功率点和指标契约；
- production 的 proposal/token/GPU/cost 上限；
- sealed holdout 管理员和访问审批流程；
- Model/Memory 最终 promotion reviewer。

---

## 17. 交付清单

- 本开发需求文档；
- 最新可编辑架构图；
- Candidate/Evaluation/Budget/Archive RFC；
- Threat Model 和 Anti-Hack 测试清单；
- PIMC Project Pack；
- DPD Project Pack；
- API/CLI/UI 契约；
- 单元、集成、对抗和 E2E 测试；
- V2 兼容性报告；
- 每 Phase 实现报告；
- 最终 Discovery Run、候选谱系、Pareto、预算和 Promotion 证据包。

## 18. 最终开发原则

> 先冻结可信评测，再生成候选；先做可回放闭环，再提高搜索智能；先保证 V2 不回归，再引入 Co-Scientist、AlphaEvolve 和 ShinkaEvolve 的增强机制。
