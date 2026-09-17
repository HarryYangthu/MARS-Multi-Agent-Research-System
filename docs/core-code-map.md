# 当前核心代码与实现思路

本文按本次 CLI 合入版本的实际代码整理。运行命令见 [cli-research.md](cli-research.md)。目录存在、软件测试通过、真实科研效果成立是不同层面的事实。

## 先读哪些代码

| 顺序 | 文件 | 需要理解的内容 |
|---|---|---|
| 1 | [cli.py](../backend/app/cli.py) | 自然语言任务、仓库、数据、预算进入系统；doctor/research/status/resume |
| 2 | [cli_research_service.py](../backend/app/bridge/cli_research_service.py) | 冻结输入、运行状态、调研、实验、逐轮反馈、最终选择与报告 |
| 3 | [cli_composition.py](../backend/app/cli_composition.py) | 在 Bridge 外装配具体 Agent，保持编排与实现分离 |
| 4 | [base.py](../backend/app/agents/base.py) | RunRequest/ContextPack/Artifact 契约、项目上下文、Schema、执行器注入 |
| 5 | [agent_loop/executor.py](../backend/app/harness/agent_loop/executor.py) | NativeAgentLoop 的模型调用、工具、Observation、修复、Reflection、预算和 checkpoint |
| 6 | [focused_agent.py](../backend/app/agents/idea/focused_agent.py) | 实际读方法、提出一个方案、另一模型审查，并核验材料引用 |
| 7 | [research_cli.py](../backend/app/agents/research_cli.py) | 生成候选模块、基于实测做分析；代码契约与导入限制 |
| 8 | [pimc_static_worker.py](../backend/app/execution/pimc_static_worker.py) | 真实模型、训练、验证、参数统计、checkpoint 与最终测试 |
| 9 | [research_trial.py](../backend/app/harness/research_trial.py) | 有限值检查、参数/性能硬条件、只用验证集选候选 |

## 仓库按职责划分

| 路径 | 实现职责 | 在本次 CLI 中的关系 |
|---|---|---|
| `backend/app/agents/` | Idea、Experiment、Coding、Execution、Writing 及基础接口 | 复用 FocusedIdea；新增 CLI Coding/Analysis |
| `backend/app/bridge/` | 产品流程、Commander、Discovery、状态协调 | CLI 使用新增服务；其他编排路径仍存在 |
| `backend/app/harness/` | 不绑定具体 Agent 的执行、上下文、工具、Schema、评测和研究机制 | 共用基础机制，未整体重构 |
| `backend/app/execution/` | 本地/远程适配器、进程、仿真、日志、指标 | 新增静态 PIMC CPU worker 与进程控制 |
| `backend/app/storage/` | 任务、产物、候选、谱系、预算及状态存取 | 旧路径存储组件；CLI 自有运行目录和状态文件 |
| `backend/app/hitl/` | 人工审查、审批记录、修订 | 旧产品审查路径；不能等同于 CLI 的模型自审 |
| `backend/app/reporting/` | 报告、数据包和报告束 | CLI 另有固定模板汇总真实执行记录 |
| `backend/app/api/`、`main.py` | FastAPI/Socket.IO 服务入口 | CLI 可独立运行 |
| `configs/` | 模型、Agent、上下文、工具、门禁、执行和评测配置 | `cli_research.yaml` 固定本轮 CLI 协议 |
| `templates/` | 产物格式和代码规则 | 与 JSON Schema 协同 |
| `projects/` | 项目规则、接入元数据与示例适配器 | 实际私有 PIMC 源码和采集数据外置 |
| `backend/tests/` | 软件行为、纯函数、Schema、文件与执行测试 | 真实外部仓检查通过环境变量启用 |
| `frontend/` | Next.js 工作台 | 独立的界面层 |
| `scripts/`、`deploy/`、`.github/` | 开发验证、部署和 CI | 属于交付配套 |
| `docs/` | 设计说明、历史评测材料、使用文档 | 历史报告不能视为本版本已完成的新实验 |
| `posttrain/`、`workspace/` | 后训练说明/占位、外部仓接入说明 | 不能据此认定已实现在线训练或已带真实数据 |

## Harness 的全部直接子目录

| 子目录 | 已有机制 | 需要注意 |
|---|---|---|
| `agent_loop/` | 原生工具协议、执行器、上下文拼装、审查、停止条件、trace | 单 Agent 内部循环，不等同于研究项目级规划器 |
| `context/` | 系统/项目/任务层、文件夹上下文、片段选择与压缩、预算及 manifest | 存在不同装配路径；当前原生 loop 固定关键上游并累积 Observation，未统一全部上下文策略 |
| `discovery/` | 候选定义、快照、内容寻址落盘、Pareto 归档、采样、晋级与停止机制 | CLI 复用快照/落盘/源码归档；未复用整个 Discovery 搜索调度 |
| `evaluation/` | 产物和运行评测、rubric、汇总、校准、导出、自进化记录 | 机制存在不代表研究能力已获端到端验证 |
| `gates/` | 基线兼容、实验启动、方案确定、结论和大改动门禁 | CLI 候选落盘实际经过工具分发中的 Gate 5 |
| `kb/` | 文档摄入、embedding、检索、基线匹配、来源与记忆写入 | 依赖可用知识内容与后端配置 |
| `llm/` | Provider 接口、模型注册、多服务适配、后训练模型加载 | 本次使用 DeepSeek 配置；缺 Key 明确失败 |
| `memory/` | 情景/语义记忆、重要性、冲突、选择、注入与使用记录 | 未在 CLI 中建成跨研究任务的自动经验改进闭环 |
| `observability/` | 事件、trace、可选外部追踪 | CLI 同时记录阶段进度与原生 Agent trace |
| `project_packs/` | 项目包模型与注册 | 面向可替换项目适配 |
| `runtime/` | RunGraph、状态机、队列、事件总线、运行就绪检查 | 不在此硬编码业务阶段拓扑 |
| `schema/` | frontmatter 解析、JSON Schema 与校验 | 软件契约校验，不是科学正确性证明 |
| `sedimentation/` | 产物元数据、提取与沉淀 hook | 并非所有新路径自动复用全部沉淀流程 |
| `tools/` | 统一注册、参数校验、权限、门禁、调用收据；搜索/代码/执行/知识等工具 | `mcp_adapters/__init__.py` 实现 stdio MCP 初始化、工具发现与调用；需配置并启动对应服务 |

`harness/` 根目录还包含 `execution_intent.py`（执行意图）、`project_workspace.py`（项目定位）和 `research_trial.py`（本次 CLI 预算与比较规则）。完整递归目录见目录索引。

## 一次研究如何执行

1. **固定问题与测量规则。** CLI 接收自然语言背景和显式约束，Bridge 冻结代码、配置、数据哈希、依赖版本和预算。先检查真实基线与数据，再调用模型。
2. **提出可执行假设。** FocusedIdea 使用 Pro 调研/提案、Flash 审查，检索并读取方法正文。产物包含方法、来源、参数计算、代码交接和验证条件，经过 Schema 与领域检查。
3. **执行公平比较。** 原始基线与候选使用同一固定划分、seed、损失、优化器和更新预算。Coding 输出单个 `build_model(config)` 模块；宿主保护评分器与基线，Gate 5 后在隔离副本执行。
4. **用证据驱动下一轮。** worker 记录真实参数量、loss、梯度、验证指标和权重。Analysis 接收这些结果或失败信息，下一轮 Coding 获得上一轮代码与分析。模型阶段被拒绝时，携带实际审查反馈有界重试。
5. **冻结选择后测试。** 达到配置轮数后，只按验证结果选择满足参数约束的候选；随后测试基线与所选候选，按固定规则判断目标是否达成，生成报告。最终测试不反向用于挑选下一候选。

关键约束是由宿主执行的：实参数至少减少 20%、默认 RES 退化容忍为 0 dB。模型不能通过修改评分器或写一段“实验成功”文字改变判定。默认 50 次更新只能支持限定预算下的比较，不能证明充分收敛或普遍性能等价。

## 三种循环目前没有完全统一

| 循环 | 所在代码 | 当前行为 |
|---|---|---|
| 单 Agent 推理循环 | `BaseAgent` + `NativeAgentLoop` | 模型行动、工具观察、输出校验、审查修订，受调用预算控制 |
| 研究工作流循环 | `bridge/cli_research_service.py` | 调研一次，按固定轮数编码/实验/分析，最后冻结选择 |
| Discovery 搜索循环 | `bridge/discovery_service.py` + `harness/discovery/` | 候选、预算账本、评测、归档与晋级等另一套编排 |

此外，原有 `workflow_service.py` 构建 Idea→Experiment→Coding→Execution→Writing 的 RunGraph；Commander 有失败诊断、反馈路由和记忆相关实现。这些代码继续保留，不能把不同路径的能力直接相加，宣称 CLI 已全部具备。

## 本次改动与尚未完成的核心升级

本次新增 CLI 入口、装配层、Bridge 研究服务、Coding/Analysis Agent、PIMC worker、进程控制、预算比较、源码归档和相关检查；删除三个无调用的旧模块。`BaseAgent`、`NativeAgentLoop`、FocusedIdea 的核心实现沿用既有代码。

目前 CLI 的 Analysis 建议会传给下一轮，但没有驱动任意任务重拆、动态回到调研、按信息价值分配预算或自主选择下一种实验；循环仍按配置的轮数运行。子 Agent 动态分工、统一上下文/长期记忆、Discovery 与 CLI 的统一搜索状态、跨任务经验验证也未完成系统升级。

下一步应以真实 PIMC 任务建立任务状态与验收集，再把“下一步动作”做成可校验的决策对象，允许有预算地补充调研、修复代码、开展实验或停止；用实际质量、成本和失败率比较修改前后效果。现阶段不能声称普通模型已达到“超级研究者”能力。

## 验证边界

已有真实 DeepSeek 调研和代码生成的开发记录，候选实际参数为 19,264→14,960，形状和梯度检查通过。该记录包括开发修复与阶段重跑，不能称为一次完整科学实验闭环。

独立测试目录的 11 项检查通过；其中优化器/checkpoint 测试使用明确标注的解析张量做真实计算，不能替代 RF 数据。真实静态 `.pth` 尚不可访问，因此尚未完成参数减少 20% 且性能不下降的真实数据验收；当前环境也未连接用户 PC。合入前软件回归结果记录在对应 PR 中。
