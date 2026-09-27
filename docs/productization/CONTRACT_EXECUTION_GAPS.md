# 冻结合同执行接入审计

2026-09-28，只读代码审计。当前合同可以冻结、创建真实 run 并恢复核验，但研究执行仍被准入阻断。本记录是下一步开发依赖分析，不是运行时能力清单或验收声明。

## 23 项预算的实际落点

所有默认值继续取 `configs/research_defaults.yaml`，不得用恰好相同的全局值冒充合同绑定。

| 合同字段 | 默认值 | 已有机制与接入缺口 |
| --- | ---: | --- |
| search_candidates | 20 | `tools/search.arxiv_search_tool` 仅限制单次 top_k；需跨来源/子会话的论文身份去重与任务总额度预留。 |
| deep_read_papers | 5 | `tools/search/source_fetch.fetch_sources_tool` 保存真实解析/页窗口回执；缺任务篇数账本，追加同篇页窗口不应重复算篇数。 |
| concurrent_readers | 2 | `agents/idea/research_delegate.ResearchSession` 只有委派次数；需论文级并发租约。 |
| proposal_candidates | 2 | Idea proposal 与 Discovery proposals 属于不同接口；需明确 candidate ID 和持久化准入。 |
| implemented_candidates | 1 | `CandidateWorkspaceManager.prepare_secure_from_repo` 可核验实际实现；materialize 前缺合同候选计数。 |
| debate_rounds | 1 | `DebateRunner` 读取 AgentConfig，Reflection 另行调用；需合同轮次和所有评审请求统一入账。 |
| automatic_iterations | 2 | `CommanderAgent` 使用全局 max_iterations；`Orchestrator._append_feedback_attempt` 追加图前须原子扣额度。 |
| model_requests | 60 | `guarded_complete`/`RunModelBudget.reserve` 已计 SDK attempts；当前仍读全局 resources.yaml。 |
| tool_executions | 120 | native loop 单次 max_tool_steps 不能覆盖所有入口；`ToolRegistry.dispatch` 实际 handler 前须统一预留。 |
| research_activity_seconds | 5400 | 模型账本 elapsed 不等于活动区间并集；需活动租约、截止取消、真实暂停条件。 |
| input_tokens | 1000000 | 已有预留、实际 usage 结算与未知用量保守处理；缺冻结策略来源。 |
| billed_output_tokens | 128000 | 已有组件账本；需合同绑定并继续包含供应商计费推理用量。 |
| model_cost_cny | 20 | 已有金额预留，但 prices 为空且 max_cost 为 null；缺有来源/日期的冻结价格策略。 |
| request_input_tokens | 48000 | context pack 是每 Agent 策略；需完整 messages/tools 在共同 provider 边界核验。 |
| request_output_tokens | 8192 | 现为各 Agent max_tokens；需调用前按合同限制。 |
| coding_output_tokens | 16384 | 需可信 stage 身份授予 coding 特例，不能接受调用参数自报身份。 |
| training_job_seconds | 600 | 现 local command/ProcessAdapter 超时由后端持有；需独立 runner 截止。 |
| concurrent_training_jobs | 1 | BatchConfig Semaphore 仅限一个 batch；需任务持久 job slot。 |
| max_gpus | 1 | 无通用设备分配权威；CPU 能力不授予 GPU 作业，保留上限且实际用量为 0。 |
| training_process_seconds | 3600 | 已有单作业 duration；缺任务累计预留、结算与未知作业保守占用。 |
| gpu_seconds | 3600 | Discovery 的独立字段不是此合同账本；GPU 能力接入前继续阻断。 |
| operation_retries | 2 | SDK、下载、native 工具重试分散；需 operation ID 与统一上限，0 真正禁用重试。 |
| repeated_error_limit | 2 | native loop 仅部分 tool+args 去重；需跨子会话/恢复的错误、动作、输入和证据指纹。 |

`RunModelBudget.begin_revision` 当前更新 revision_started_at，并允许采纳新全局 policy；`agent_runner.run_agent_node` 在 revision 时调用它。合同任务不得通过修订重置活动计时或预算。

费用语义须明确：计划允许价格未知时依赖有限 token/调用额度，但不得声称精确金额已受控。合同有 20 元字段不能自动证明这一硬上限生效；需真实价格策略或明确未知状态，不能输出虚假零费用。

## 项目路径与执行能力

- `project_workspace.folder_project/project_root` 和 `load_project_repo` 查可变全局 registry；不得以注册/覆盖同名项目方式接入合同。bridge 应构造绑定 run 与 task hash 的项目能力。
- 仅传 `ToolContext.project_repo_root` 不够：`tools/code._resolve_project_file/_is_allowed_path/_read_only_error` 仍读全局 repo_link；Gate5 也读全局规则，非 folder 项目仍有旧 PIMC forward 规则。根目录、允许/保护范围、命令和规则须一起绑定，Gate5 保留 dispatch 必经。
- `BaseAgent.build_context`、`load_project_knowledge`、Agent repository 配置仍读取全局资料。上下文与读工具也必须使用合同资料清单，避免同名项目串用。
- 当前冻结只哈希声明的 baseline/entrypoint，不能充当完整代码/数据/知识快照。可复用 `CandidateWorkspaceManager.prepare_secure_from_repo`，但授权代码快照不能直接等同于 allowed_paths，否则可能漏掉基线与保护文件。
- 通用命令须显式接入已有 `local_command_request.v1/result.v1`，核验 invocation/run/experiment、必需有限指标及真实 evidence 文件。普通脚本 exit 0 不代表实验成功。
- `process_runtime` 明示可信本地命令边界，不提供 OS 沙箱。候选工作目录隔离不能冒充防恶意代码的文件系统隔离。

## 最小依赖顺序

1. 可并行：StateJournal 权威内的合同预算预留/结算/租约；run-bound 项目能力与代码快照；独立 CPU runner 与现有命令回执协议。
2. 模型、工具、活动 watchdog 接统一账本；同时接 Reader/candidate/review/feedback 数量准入。恢复与修订均继承累计用量。
3. 原 Orchestrator 接通真实 baseline/candidate/必要消融与指标判断，UI/CLI 继续同一 owner。全部支持能力完成真实检查后才解除合同 start 阻断。

本地 runner 必须独立持有作业 deadline：仅把后端 asyncio timeout 改成 600 秒，不能保证后端 SIGKILL 后子进程仍会截止。CPU 进程、真实文件、SQLite 并发/中断及路径范围验证不依赖外部 GPU；SSH/GPU、未绑定工具与外部模型进程按具体能力继续阻断，不应阻塞这些开发。
