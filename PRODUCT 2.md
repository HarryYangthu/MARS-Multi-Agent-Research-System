# PRODUCT.md — MARS 产品定义

## 1. 一句话定位

MARS 是研究型多 Agent 系统的底座。它把"研究问题 → 文献调研 → 假设 → 实验设计 → 编码 → 仿真 → 报告 / 论文"这条研究链路,改造成 5 个 Agent 的可编排流水线,加上 Harness 治理(Schema、HITL、上下文管理、知识沉淀、Baseline 复用),让一个研究人员在自己已有代码和研究资产基础上,把研究周期从月级压缩到周级。

## 2. 北极星指标

| 指标 | 目标 |
|---|---|
| 单次研究任务周期 | 月级 → 周级 |
| Schema 合规率 | ≥ 95% |
| 重复实验减少 | ~ 35%(由 Baseline 复用机制实现) |
| 沉淀可复用研究资产 | ≥ 50 项(到 V0 demo 期结束) |

## 3. 不做什么(Out of Scope)

- ❌ 通用 SaaS 多租户平台,V0 服务单用户
- ❌ GRPO 训练流水线(V1 单独立项)
- ❌ 研究代码托管(真实代码仍在原仓 / GitLab / 本地)
- ❌ 云端部署(V0 单机 4 × L40S)
- ❌ 业务系统工程化(只服务研究)

## 4. 用户与典型工作流

V0 目标用户是**单个算法研究员**(Harry 自己的工作流为基准)。系统服务以下 5 类典型工作流。

### 工作流 A:全链路一次跑通(Pipeline 模式)

> 研究员:"基于 PIMC 现有代码,跑一组 ATK-MoE 在 8L 配置下的消融。"

系统:`Idea → Experiment → Coding → Execution → Writing`,每层暂停 HITL,人 review / 编辑 / approve 后下一步。`runs/` 沉淀完整链路。

### 工作流 B:任意入口直接进入(Standalone)

> 研究员:"我已经写好 `experiment_plan.md`,直接调 Coding Agent 写代码。"

系统:跳过 Idea / Experiment Agent,直接以人工 md 为入口进入 Coding Agent。Coding 输入 schema 校验通过即接受。

### 工作流 C:单 Agent 工具化使用

> 研究员:"我有一个商用 C 代码需求,直接调 Coding Agent。"

系统:Coding Agent 独立调用,选择 C 路径 + 后训练模型(V1)/ 远程 API + 项目代码规范(`templates/code_rules/commercial_c.md`)。输出 patch + tests。

### 工作流 D:中间产物人工迭代

> 研究员:"Idea Agent 输出我不满意,我手动改了 v2,继续走。"

系统:`runs/<id>/idea/idea_proposal.v2.md` 替换 v1,标 `approved`,后续 Agent 读 approved 版本。

### 工作流 E:多实验并发对比

> 研究员:"同一组实验,4 个 baseline 变体并排跑。"

系统:Execution Agent 接 `batch_config.json`,4–6 个 SimulationJob 并发,前端 split view 实时显示叠加 loss 曲线。

## 5. 双形态规则

每个 Agent 都是**独立产品 + Pipeline 节点**:

| 维度 | Standalone | Pipeline |
|---|---|---|
| 入口 | 前端 6 卡片中该 Agent 的卡片 | 前端"Pipeline 模式"卡片 |
| 输入 | 用户直接输入 / 上传 md | 上层 Agent 的 approved md(由 Bridge 路由) |
| 配置 | 独立 LLM / tools / debate 设置 | 同上(同一份 `configs/agents.yaml`) |
| 调度 | 经 Bridge(必经路径) | 经 Bridge,Bridge 编排 RunGraph |
| HITL | 同样支持每层 review / approve | 同上 |
| 沉淀 | `runs/<id>/<agent>/` 局部沉淀 | `runs/<id>/` 完整链路沉淀 |

注意:Standalone **不**绕过 Bridge,只是 RunGraph 退化成单节点。

## 6. 任意入口规则

人写的 md 与 Agent 生成的 md 对下游 Agent **等价**:

1. 人写的 md 必须包含合规 YAML frontmatter(模板在 `templates/artifacts/<schema_type>.md`)
2. 提交时通过 `harness/schema/validator.py` 校验
3. 校验通过 → 写入 `runs/<id>/<agent>/<artifact>.approved.md`,RunGraph 从该节点继续
4. 校验不过 → 前端实时高亮缺失 / 错误字段,引导补全

## 7. 五个 Agent 产品定义

每个 Agent 的内部子图、Tool 调用、prompts 细节看 `DESIGN.md` §5。本节只定义产品语义。

### 7.1 Idea Agent

**目的**:从研究问题 + 现有代码 + 文献,产出可验证的研究假设。

**输入**:研究问题(文本)+ 项目 codebase(repo_link 接入)+ 上传文档(论文 / 技术资料)。

**核心行为**:
1. KB 检索(literature 区 + 项目 history)
2. 文献调研(arxiv search 工具)
3. 假设生成
4. **多模型 Debate(默认开)**:3 个 LLM × 2 轮辩论 + Critic 综合
5. 创新性论证

**输出**:`idea_proposal.md`(schema:`proposal.v1`)
frontmatter 关键字段:`research_question / hypothesis / novelty / theoretical_basis / constraints`

**默认配置**(`configs/agents.yaml`):
- model:`claude-opus-4.7`,temperature 0.7
- debate:on,participants `[opus, gpt-5.5, gemini-2.5-pro]`
- tools:`[retrieval.local_docs, retrieval.arxiv_search, knowledge.kb_query, code.repo_reader]`

### 7.2 Experiment Agent

**目的**:把假设转成可执行实验方案,自动做 Baseline 复用决策。

**输入**:`proposal.md`(Agent 生成 / 人工写)。

**核心行为**:
1. 变量定义(独立 / 控制 / 因变量)
2. 指标选择(RES / PIM / APE / 自定义)
3. 消融矩阵设计
4. **Baseline 自动匹配**:对 plan 提取特征 → embed → RunArchive 语义匹配 → 命中触发 Gate 提示复用
5. 实验方案文档化

**输出**:`experiment_plan.md`(schema:`experiment_plan.v1`)
frontmatter 关键字段:`variables / metrics / baseline_ref / ablations / estimated_runs / estimated_gpu_hours`

**默认配置**:
- model:`gpt-5.5` 或 `claude-opus-4.7`,temperature 0.3
- debate:off(可开)
- tools:`[knowledge.kb_query, knowledge.baseline_match, knowledge.experiment_memory]`

### 7.3 Coding Agent

**目的**:基于 experiment_plan + 现有代码,产出代码改动 + 测试方案。

**输入**:`experiment_plan.md` + 项目 codebase(`code.repo_reader` 实时读)。

**核心行为**:
1. 代码理解(repo scan + 关键模块文档化)
2. 代码生成(patch / new files / modifications)
3. **Baseline 兼容性检查**(项目 `AGENTS.md` 静态规则;不通过触发 Gate 5)
4. Lint / Test 自动跑
5. 双语言路径:Python(研究)/ C(生产)

**输出**:`code_spec.md`(schema:`code_spec.v1`)+ `patch.diff` + `tests_plan.md`
frontmatter 关键字段:`target_lang / baseline_compat / files_changed / new_dependencies / test_coverage`

**默认配置**(关键差异):
- model:**3 backend 可选**
  - `remote_api`:claude-opus / gpt-5.5 / qwen-coder-32b API
  - `local_vllm`:挂 GRPO 后训练权重 / LoRA adapter(V1 才训,V0 加载占位 / 公开模型)
  - `live_checkpoint`:V1 启用,训练中边训边测
- post_training:`enabled / mode (adapter|endpoint|fine_tuned_id) / adapter_path / custom_endpoint`
- tools:`[code.repo_reader, code.patch_generator, code.test_runner, knowledge.code_rules]`
- debate:off

### 7.4 Execution Agent

**目的**:跑实验,采集 log / loss / metrics,实时推前端。

**输入**:`code_spec.md` + `patch.diff`(已应用)+ `batch_config.json`(从 experiment_plan 推导或人工指定)。

**核心行为**:
1. 多实验调度(GPU 分配,默认 1 vLLM 卡 + 3 实验池,并发上限 6)
2. 仿真 / 训练运行
3. log / loss / metrics 实时流(WebSocket → 前端)
4. 失败重试 / 标记
5. **Baseline Fingerprint 写入 RunArchive**(运行结束时)

**输出**:`run_log.md`(schema:`run_log.v1`)+ `results.json` + `figures/` + `logs/<run>.log` + `curves/<metric>.json`
frontmatter 关键字段:`run_id / batch_size / metrics / gpu_used / duration / fingerprint_hash`

**默认配置**:
- model:`gpt-5.5`,temperature 0.1(主要做调度决策)
- debate:off
- tools:`[execution.simulation_runner, execution.batch_runner, execution.metrics_collector]`

### 7.5 Writing Agent

**目的**:把全链路产物综合成可发布的报告 / 论文片段 / PPT 提纲。

**输入**:proposal + experiment_plan + code_spec + run_log + results.json(整个 run 链路)。

**核心行为**:
1. 受众识别(导师 review / 投资人 / 论文 reviewer / 内部技术分享)
2. 报告结构生成
3. **多模型 Debate(默认开)**:reviewer 视角 critique 后改写
4. 多产物输出:研究报告 / 技术总结 / PPT 提纲 / 论文片段

**输出**:`report.md`(schema:`report.v1`)+ 可选 `ppt_outline.md` / `paper_fragment.md`
frontmatter 关键字段:`deliverable_type / target_audience / chain_refs(指向上游所有 artifacts)`

**默认配置**:
- model:`claude-sonnet-4.6`,temperature 0.4
- debate:on,reviewer 角色 `[opus-as-critic, gpt-as-positive-reviewer]`
- tools:`[knowledge.research_outputs, knowledge.experiment_memory]`

## 8. 知识库 4 区(KB)

| 内部命名(代码) | UI 中文 | 内容 | 主要消费方 |
|---|---|---|---|
| `literature` | 文献库 | 论文、技术报告、领域资料 | Idea Agent |
| `methodology` | 方法库 | 历史 proposal / experiment_plan / 报告模板 / 消融模式 | Experiment / Writing Agent |
| `code_assets` | 代码资产库 | 可复用模块 / 算子 / 训练脚手架 / 代码规范 | Coding Agent |
| `run_archive` | 实验运行档案 | 历史 run_log / results / Baseline Fingerprint | Experiment(复用)/ Writing(对比) |

每区独立 ChromaDB collection。跨区检索由 `harness/kb/retriever.py` 统一调度。

## 9. 关键产品决策日志

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| 1 | 平台 vs 工具 | MARS 是平台,PIMC 是首个项目 | 长期复用,但单用户场景不过度抽象 |
| 2 | Bridge 是否必经 | 必经(产品),开发可绕 | 审计 / HITL / 沉淀链路完整 |
| 3 | 真实代码接入 | `repo_link.yaml` 指针 | 不污染 mars 仓,生产代码安全 |
| 4 | KB 命名 | 内部英文 / UI 中文 | 代码可读性 + 用户友好 |
| 5 | HITL 颗粒度 | 每 Agent + 5 系统 Gate | 高频 / 低频分层 |
| 6 | Posttrain 边界 | V0 load only | 控制范围,避免训练平台陷阱 |
| 7 | 前端定位 | 工作台 ≠ Dashboard | P0 闭环优先,P1 增强延后 |
| 8 | LangGraph 绑定 | V0 用,接口不绑死 | 快速实现 + 长期可替换 |

## 10. 前端 P0 / P1 切分

**P0(V0 必做)**:
- 6 入口卡片(5 Agent + Pipeline)
- Pipeline 工作区(节点可视化、当前活动节点高亮)
- Agent 输出审查与 Approve(`hitl/` 配套)
- md artifact 版本管理(v1 / v2 / approved)
- 实验 log 实时显示(WebSocket)
- loss / metrics 曲线显示
- 多实验并排对比(最多 6 组,曲线叠加)
- HITL 弹窗(5 个 Gate 触发时)

**P1(V0 之后)**:
- GPU 资源面板(实时利用率 / 队列状态)
- LangSmith Trace 嵌入
- Server Config 高级抽屉(细颗粒 LLM / GPU / 并发配置)
- 复杂项目切换(V0 默认单项目)
- 前端 i18n

## 11. 完整配置样例(可直接 copy)

### 11.1 `configs/agents.yaml` 完整示例

```yaml
idea:
  enabled: true
  model:
    provider: anthropic
    model: claude-opus-4.7
    temperature: 0.7
  debate:
    enabled: true
    rounds: 2
    participants:
      - role: proposer
        provider: openai
        model: gpt-5.5
      - role: critic
        provider: anthropic
        model: claude-opus-4.7
      - role: judge
        provider: google
        model: gemini-2.5-pro
  tools:
    - search.local_docs
    - search.arxiv_search
    - knowledge.kb_query
    - knowledge.baseline_match
    - code.repo_reader

experiment:
  enabled: true
  model:
    provider: openai
    model: gpt-5.5
    temperature: 0.3
  debate:
    enabled: false
  tools:
    - knowledge.kb_query
    - knowledge.baseline_match
    - knowledge.experiment_memory

coding:
  enabled: true
  model:
    provider: local_vllm
    model: qwen2.5-coder-7b
    temperature: 0.1
  post_training:
    enabled: false              # V0 默认 false,V1 启用
    mode: load_only             # load_only | adapter | endpoint | fine_tuned_id
    local_vllm_model_path: ""
    lora_adapter_path: ""
    custom_endpoint: ""
    fine_tuned_model_id: ""
    live_checkpoint_path: ""    # V1 启用
  language_tracks:
    - python_research
    - c_production
  tools:
    - code.repo_reader
    - code.patch_generator
    - code.test_runner
    - code.lint
    - knowledge.code_assets

execution:
  enabled: true
  model:
    provider: openai
    model: gpt-5.5
    temperature: 0.1
  tools:
    - execution.simulation_runner
    - execution.batch_runner
    - execution.log_streamer
    - execution.metrics_collector

writing:
  enabled: true
  model:
    provider: anthropic
    model: claude-sonnet-4.6
    temperature: 0.4
  debate:
    enabled: true
    rounds: 1
    participants:
      - role: critic
        provider: anthropic
        model: claude-opus-4.7
      - role: positive_reviewer
        provider: openai
        model: gpt-5.5
  tools:
    - knowledge.methodology
    - knowledge.run_archive
```

**关键规则**:
- 不在代码硬编码任何模型名
- 配置缺失时 fallback 到 `mock_provider`(让 demo 在没有真实 API key 时仍能跑)
- API key 在 `.env`,`models.yaml` 引用 env var

### 11.2 `projects/<name>/repo_link.yaml` 完整示例

```yaml
project: moe-pimc
repo_mode: local_path           # local_path | git_submodule | mirror
repo_path: ../../workspace/repos/pimc-current
read_only: false
sync_strategy: live             # live | snapshot

# Coding Agent 可读 / 可改的路径
allowed_paths:
  - libs/
  - configs/
  - tests/
  - main.py

# Gate 5 静态检查时,任何修改这些路径的 patch 强制阻塞
protected_paths:
  - baseline/
  - libs/Model.py:Paper_Total_0327   # 类级别保护(继承自项目 AGENTS.md)
  - production_interface/

# 项目级 baseline 规则文件,Gate 5 读取
baseline_rules_file: ./AGENTS.md

# KB indexer 的忽略 pattern
ignore_patterns:
  - "data/"
  - "*.npy"
  - "*.npz"
  - "__pycache__/"
  - "runs/"
```

### 11.3 `configs/execution.yaml` 关键字段

```yaml
gpu_policy:
  vllm_reserved_cards: 1
  experiment_pool_cards: 3
  max_concurrent_experiments: 6
  cpu_only_fallback: true       # 没 GPU 时走 mock_simulation

mock_simulation:
  enabled: auto                 # auto | always | never
  loss_curve_template: "exponential_decay"
  duration_seconds_per_run: 30  # mock 单 run 模拟时长

gates:
  experiment_launch:
    gpu_hours_threshold: 12     # 超过此 GPU 小时触发 Gate 3
  large_refactor:
    files_changed_threshold: 5  # 超过此文件数触发 Gate 2
```
