# Agent Context Blueprints

> 用途:定义每个 Agent 独立的上下文装载顺序与上下文工程策略。
> 原则:每个 Agent 根据任务类型单独设计装载路径;共享的只是底层能力,包括 Context Quarantine（上下文隔离）/ Context Pruning（上下文修剪）/ Context Summarization（上下文摘要）/ Context Offloading（上下文卸载）/ Context Packing（上下文打包）。

## 策略术语

| 策略 | 中文 | 含义 |
| --- | --- | --- |
| Quarantine | 上下文隔离 | 隔离有风险、冲突、未验证、失败、mock、过期的上下文,默认不进入主 prompt。 |
| Pruning | 上下文修剪 | 删除或降级无关、重复、过量、低优先级上下文。 |
| Summarization | 上下文摘要 | 将长上下文压缩成结构化摘要,保留决策所需字段。 |
| Offloading | 上下文卸载 | 将完整原文、日志、代码、工具输出写入外部文件或 KB,主 prompt 只保留摘要和 ref。 |
| Packing | 上下文打包 | 按 Agent 任务目标、优先级、风险和 token 预算组织最终 LLM messages。 |

---

## Idea Agent

目标:从研究背景、代码现状和调研证据中生成可验证 hypothesis。

| 顺序 | 上下文层 | 具体内容 | 保存文件/目录 | 必需性 | 风险 | 工程策略 | Packing 位置 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 系统提示词 | Idea 角色、`proposal.v1` 输出规则、禁止编造、证据缺口声明 | <code>configs/<wbr>agents.yaml</code><br><code>backend/<wbr>app/<wbr>agents/<wbr>idea/<wbr>prompts/<wbr></code> | 必装 | 低 | 不修剪、不卸载 | system 最前 |
| 2 | 研究大背景 | PIMC、FDD Massive MIMO、beam/layer switching、项目 AGENTS.md、baseline 保护 | <code>projects/<wbr>pimc/<wbr>AGENTS.md</code><br><code>projects/<wbr>pimc/<wbr>project.yaml</code><br><code>knowledge/<wbr></code> | 必装 | 背景过长导致干扰 | Summarization;只保留任务相关约束 | system/project 后 |
| 3 | 代码仓核心上下文 | baseline 结构、核心算法、可调 knobs、数据生成入口、不可改区域 | <code>projects/<wbr>pimc/<wbr>repo_link.yaml</code><br><code>workspace/<wbr>repos/<wbr></code><br><code>backend/<wbr>app/<wbr>agents/<wbr>idea/<wbr>docs/<wbr></code> | 必装 | 代码太长、无关文件干扰 | Summarization + Offloading;核心约束不可剪 | project/code constraints |
| 4 | Commander 当前任务 | 研究问题、目标、输出要求、约束 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>input/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 必装 | 低 | 不可摘要失真 | task 末尾附近 |
| 5 | 长期 memory | 历史有效假设、失败假设、方法偏好、领域经验 | <code>configs/<wbr>agent_contexts/<wbr>idea.yaml</code><br><code>knowledge/<wbr>methodology/<wbr></code><br><code>knowledge/<wbr>run_archive/<wbr></code> | 条件装 | memory 污染、过期经验 | 无证据 memory Quarantine;按相似度 Pruning top-k | task 后 |
| 6 | 调研结果 | KB 检索、论文摘要、source summaries、evidence index、research summary | <code>runs/<wbr>&lt;run_id&gt;/<wbr>idea/<wbr>research/<wbr></code><br><code>knowledge/<wbr>literature/<wbr></code> | 条件装 | 低质量来源、证据冲突 | source 原文 Offloading;Pack evidence summary / claim / citation / gap;冲突来源 Quarantine | evidence 区 |
| 7 | Debate 结果 | supporter / critic / synthesizer 共识、分歧、风险、evidence gaps | <code>runs/<wbr>&lt;run_id&gt;/<wbr>idea/<wbr>debate_transcript*.md</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr></code> | 条件装 | transcript 过长、分歧干扰 | transcript Offloading;只 Pack 共识和未解决分歧 | evidence 后 |
| 8 | Commander 迭代指令 | 补充 router 简化、降低风险、调整 hypothesis 等 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>hitl/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code> | 条件装 | 新旧指令冲突 | 最新指令高优先级 Pack;旧指令 Quarantine | final recap 前 |
| 9 | 动态上下文 | 工具失败、schema repair、HITL feedback、最新检索 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>raw/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>hitl/<wbr></code> | 条件装 | 错误信息污染 | 失败原文 Quarantine/Offload;Pack 错误摘要 | final recap 前 |
| 10 | 最终打包 | 当前任务所需最小上下文集合 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>context_manifest.v2.*.json</code> | 必装 | lost-in-middle | 按下方顺序 Packing | final messages |

**推荐 Packing 顺序**
`system role -> schema/output contract -> research background -> baseline/code constraints -> commander task -> selected memory -> research summary + evidence index -> debate consensus/gaps -> iteration instruction -> final task recap`

---

## Experiment Agent

目标:把 `proposal.v1` 变成可执行、可评估、预算明确的实验计划。

| 顺序 | 上下文层 | 具体内容 | 保存文件/目录 | 必需性 | 风险 | 工程策略 | Packing 位置 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 系统提示词 | Experiment 角色、`experiment_plan.v1` 输出规则、变量/指标/消融要求 | <code>configs/<wbr>agents.yaml</code><br><code>backend/<wbr>app/<wbr>agents/<wbr>experiment/<wbr>prompts/<wbr></code> | 必装 | 低 | 不修剪、不卸载 | system 最前 |
| 2 | 实验背景 | RES / PIM / APE / loss 指标、资源预算、mock/real 执行边界 | <code>projects/<wbr>pimc/<wbr>AGENTS.md</code><br><code>configs/<wbr>execution.yaml</code><br><code>configs/<wbr>evaluation.yaml</code> | 必装 | 背景过宽 | Summarization;只保留实验相关内容 | project/constraints |
| 3 | 上游 Proposal | research_question、hypothesis、constraints、evidence gaps | <code>runs/<wbr>&lt;run_id&gt;/<wbr>idea/<wbr>idea_proposal.approved.md</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>idea/<wbr>idea_proposal.v*.md</code> | 必装 | proposal 太长 | Schema-aware Summarization;原文 Offloading | proposal summary |
| 4 | 代码仓实验入口 | data_gen、simulation runner、config、可调参数 | <code>projects/<wbr>pimc/<wbr>data_gen.py</code><br><code>backend/<wbr>app/<wbr>execution/<wbr></code><br><code>workspace/<wbr>repos/<wbr></code> | 必装 | 代码噪声 | Pack 参数表和入口摘要;完整代码 Offloading | code entry |
| 5 | Commander 当前任务 | 设计哪类实验、成本限制、优先验证点 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>input/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 必装 | 低 | 高优先级 Pack | task 区 |
| 6 | 长期 memory | 历史实验设计、失败消融、资源超预算案例 | <code>configs/<wbr>agent_contexts/<wbr>experiment.yaml</code><br><code>knowledge/<wbr>methodology/<wbr></code><br><code>knowledge/<wbr>run_archive/<wbr></code> | 条件装 | 过期经验 | 按 hypothesis/metric 相似度 Pruning;过期 Quarantine | memory 区 |
| 7 | 历史 run archive | baseline、历史 metrics、fingerprint、可复用结果 | <code>knowledge/<wbr>run_archive/<wbr></code><br><code>runs/<wbr>&lt;previous_run_id&gt;/<wbr>execution/<wbr></code> | 条件装 | 错误复用 baseline | 低相似 fingerprint Quarantine;Pack 可复用摘要 | historical runs |
| 8 | 迭代修正指令 | HITL / Commander / evaluation 对实验计划的修改 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>hitl/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>evaluation/<wbr></code> | 条件装 | 新旧 plan 冲突 | Pack 最新指令;旧版本 Offloading/Quarantine | final recap 前 |
| 9 | 动态上下文 | schema errors、预算冲突、变量缺失 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>raw/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 条件装 | 修复信息过载 | 只 Pack 当前修复所需字段 | repair 区 |
| 10 | 最终打包 | 实验设计所需上下文 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>context_manifest.v2.*.json</code> | 必装 | 信息重复 | 按下方顺序 Packing | final messages |

**推荐 Packing 顺序**
`system role -> schema/output contract -> metrics/budget constraints -> proposal distilled summary -> code/data/simulation entry summary -> commander task -> selected historical runs -> memory lessons -> repair/iteration instruction -> final experiment-plan recap`

---

## Coding Agent

目标:根据 code spec 和当前任务安全修改代码,不破坏 baseline。

| 顺序 | 上下文层 | 具体内容 | 保存文件/目录 | 必需性 | 风险 | 工程策略 | Packing 位置 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 系统提示词 | Coding 角色、patch 目标、最小改动原则、输出要求 | <code>configs/<wbr>agents.yaml</code><br><code>backend/<wbr>app/<wbr>agents/<wbr>coding/<wbr>prompts/<wbr></code> | 必装 | 低 | 不修剪、不卸载 | system 最前 |
| 2 | 代码安全规则 | AGENTS.md、project AGENTS.md、Gate 5、protected paths、禁止事项 | <code>AGENTS.md</code><br><code>projects/<wbr>pimc/<wbr>AGENTS.md</code><br><code>configs/<wbr>gates.yaml</code><br><code>projects/<wbr>pimc/<wbr>repo_link.yaml</code> | 必装 | 低 | 不可修剪;高优先级 Pack | safety constraints |
| 3 | 代码仓核心代码 | 目标文件、相关接口、调用链、测试入口 | <code>workspace/<wbr>repos/<wbr></code><br><code>projects/<wbr>pimc/<wbr>repo_link.yaml</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr>workspace/<wbr></code> | 必装 | 文件过多干扰 | 无关文件 Pruning;目标片段 Pack;完整文件 Offloading | repo snippets |
| 4 | 上游 Code Spec | files_to_change、contracts、tests、risks、acceptance | <code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr>code_spec.approved.md</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr>code_spec.v*.md</code> | 必装 | spec 过长或含糊 | Schema-aware Summarization;原文 Offloading | code_spec summary |
| 5 | Commander 当前任务 | 本轮实现目标、限制、禁止改动范围 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>input/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code> | 必装 | 与旧 spec 冲突 | 高优先级 Pack;旧任务 Quarantine | task 区 |
| 6 | 长期 memory | 历史 patch 经验、测试坑、代码风格规则 | <code>configs/<wbr>agent_contexts/<wbr>coding.yaml</code><br><code>knowledge/<wbr>code_assets/<wbr></code><br><code>knowledge/<wbr>run_archive/<wbr></code> | 条件装 | 过期或错误经验 | 按 target file/error type Pruning;无证据 Quarantine | memory 区 |
| 7 | 工具读取结果 | repo_reader、grep、lint/test output | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>raw/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr>tool_results/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 条件装 | 日志过长 | Summarization;原始输出 Offloading | tool observations |
| 8 | 迭代修复上下文 | 上次 patch、失败测试、错误日志、Commander feedback | <code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr>patches/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>evaluation/<wbr></code> | 条件装 | 错误信息污染 | Pack 最新失败摘要;旧 patch Quarantine | failure recap |
| 9 | 动态上下文 | 当前 diff、rollback info、未通过检查 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>raw/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 条件装 | 状态冲突 | 只保留最新 attempt;历史 attempt Summarization | dynamic |
| 10 | 最终打包 | 安全改代码所需上下文 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>context_manifest.v2.*.json</code> | 必装 | 无关上下文干扰 | 按下方顺序 Packing | final messages |

**推荐 Packing 顺序**
`system role -> code safety / Gate 5 constraints -> code_spec distilled -> target repo map + file snippets -> commander task -> relevant memory -> latest tool observations -> failed test/lint summary -> current patch objective recap`

---

## Execution Agent

目标:执行实验、收集 metrics、生成 `run_log.v1`,真实记录 mock/real 状态。

| 顺序 | 上下文层 | 具体内容 | 保存文件/目录 | 必需性 | 风险 | 工程策略 | Packing 位置 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 系统提示词 | Execution 角色、`run_log.v1` 输出规则、真实记录要求 | <code>configs/<wbr>agents.yaml</code><br><code>backend/<wbr>app/<wbr>agents/<wbr>execution/<wbr>prompts/<wbr></code> | 必装 | 低 | 不修剪、不卸载 | system 最前 |
| 2 | Execution Policy | CPU/mock/GPU 策略、并发、timeout、路径规则 | <code>configs/<wbr>execution.yaml</code><br><code>configs/<wbr>tools.yaml</code> | 必装 | 配置过多 | 当前策略不可剪;非当前 backend Summarization | execution constraints |
| 3 | Experiment Plan | variables、metrics、ablations、budget、baseline | <code>runs/<wbr>&lt;run_id&gt;/<wbr>experiment/<wbr>experiment_plan.approved.md</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>experiment/<wbr>experiment_plan.v*.md</code> | 必装 | 计划太长 | 保留核心字段;原文 Offloading | plan summary |
| 4 | 代码/脚本入口 | simulation runner、data path、config template、metrics collector | <code>backend/<wbr>app/<wbr>execution/<wbr></code><br><code>projects/<wbr>pimc/<wbr>data_gen.py</code><br><code>workspace/<wbr>repos/<wbr></code> | 必装 | 代码噪声 | Pack 执行入口和参数;完整代码 Offloading | script entry |
| 5 | Commander 当前任务 | 执行哪组实验、是否 mock、是否复用 baseline | <code>runs/<wbr>&lt;run_id&gt;/<wbr>input/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 必装 | 低 | 高优先级 Pack | task 区 |
| 6 | 历史 run memory | 可复用 baseline、失败 run、相同 fingerprint 结果 | <code>knowledge/<wbr>run_archive/<wbr></code><br><code>runs/<wbr>&lt;previous_run_id&gt;/<wbr>execution/<wbr></code> | 条件装 | baseline 误匹配 | 低相似 run Pruning;可疑结果 Quarantine | baseline memory |
| 7 | 工具执行结果 | batch_runner output、metrics、curves、logs | <code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr>metrics.json</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr>curves/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr>logs/<wbr></code> | 条件装 | 日志/曲线过大 | metrics Pack;curves/logs Offloading | results |
| 8 | 失败上下文 | stderr、timeout、missing file、failed job | <code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr>logs/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>raw/<wbr></code> | 条件装 | 错误污染 | Pack error summary + tail;完整日志 Offloading | failure context |
| 9 | 动态上下文 | retry、partial results、stop/retry 指令 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>raw/<wbr></code> | 条件装 | 状态冲突 | 最新状态优先;旧 partial run Quarantine | dynamic |
| 10 | 最终打包 | 生成 run_log 所需上下文 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>context_manifest.v2.*.json</code> | 必装 | 结果遗漏 | 按下方顺序 Packing | final messages |

**推荐 Packing 顺序**
`system role -> execution policy -> experiment_plan distilled -> simulation/code entry summary -> commander execution instruction -> baseline/fingerprint memory -> latest metrics summary -> failures/retry context -> run_log output contract recap`

---

## Writing Agent

目标:把全链路结果写成有证据、有边界、有结论的研究报告。

| 顺序 | 上下文层 | 具体内容 | 保存文件/目录 | 必需性 | 风险 | 工程策略 | Packing 位置 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 系统提示词 | Writing 角色、`report.v1` 输出规则、claim 必须有 evidence | <code>configs/<wbr>agents.yaml</code><br><code>backend/<wbr>app/<wbr>agents/<wbr>writing/<wbr>prompts/<wbr></code> | 必装 | 低 | 不修剪、不卸载 | system 最前 |
| 2 | 报告背景 | 项目目标、研究问题、受众、风格 | <code>projects/<wbr>pimc/<wbr>project.yaml</code><br><code>projects/<wbr>pimc/<wbr>AGENTS.md</code><br><code>backend/<wbr>app/<wbr>agents/<wbr>writing/<wbr>docs/<wbr></code> | 必装 | 背景过长 | Summarization | report background |
| 3 | 上游 artifacts | proposal、experiment_plan、code_spec、run_log、evaluation_report | <code>runs/<wbr>&lt;run_id&gt;/<wbr>idea/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>experiment/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>evaluation/<wbr></code> | 必装 | 链路太长 | 每个 artifact schema-aware Summarization;原文 Offloading | artifact chain |
| 4 | metrics/curves/logs | 关键结果、失败实验、baseline 对比 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr>metrics.json</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr>curves/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr>logs/<wbr></code> | 必装 | 数据过大 | Pack metrics table;curves/logs Offloading | metrics/evidence |
| 5 | Commander 当前任务 | 报告重点、要解释的失败、限制说明 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>input/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code> | 必装 | 低 | 高优先级 Pack | task 区 |
| 6 | 长期 memory | 写作偏好、审稿经验、报告常见问题 | <code>configs/<wbr>agent_contexts/<wbr>writing.yaml</code><br><code>knowledge/<wbr>methodology/<wbr></code><br><code>knowledge/<wbr>run_archive/<wbr></code> | 条件装 | 与当前报告无关 | Prune 技术实现 memory;只保留写作相关 | memory 区 |
| 7 | Debate 结果 | 对 claims 的质疑、evidence gap、limitation | <code>runs/<wbr>&lt;run_id&gt;/<wbr>writing/<wbr>debate_transcript*.md</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr></code> | 条件装 | 争议干扰 | Pack unresolved concerns;transcript Offloading | debate concerns |
| 8 | HITL/evaluation feedback | 人工意见、质量评估、缺失证据 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>hitl/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>evaluation/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 条件装 | 多轮反馈冲突 | Pack 最新有效反馈;旧反馈 Summarization | feedback |
| 9 | 动态上下文 | report bundle、data pack、scorecard | <code>runs/<wbr>&lt;run_id&gt;/<wbr>writing/<wbr>report_bundle.v1.md</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>writing/<wbr>report_data_pack.v1.json</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 条件装 | 数据重复 | Pack index 和关键结果;大数据 Offloading | dynamic |
| 10 | 最终打包 | 生成 report 所需上下文 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>context_manifest.v2.*.json</code> | 必装 | claim 无证据 | 按下方顺序 Packing | final messages |

**推荐 Packing 顺序**
`system role -> report/output contract -> project/report background -> artifact chain summaries -> metrics table + evidence refs -> commander writing instruction -> evaluation/HITL feedback -> debate concerns -> final claim/evidence/limitation recap`

---

## Commander / Bridge

目标:拆任务、调度 Agent、诊断失败、生成下一步迭代指令。

| 顺序 | 上下文层 | 具体内容 | 保存文件/目录 | 必需性 | 风险 | 工程策略 | Packing 位置 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 系统提示词 | 主控角色、Bridge 必经、不能绕 Gate、不能伪造 Agent 产物 | <code>configs/<wbr>agents.yaml</code><br><code>backend/<wbr>app/<wbr>bridge/<wbr>prompts/<wbr></code><br><code>backend/<wbr>app/<wbr>bridge/<wbr>docs/<wbr></code> | 必装 | 低 | 不修剪、不卸载 | system 最前 |
| 2 | 全局项目状态 | run state、workflow graph、已完成节点、blocked gates | <code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>trace_manifest.v2.json</code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>state/<wbr></code> | 必装 | 状态过长 | Summarization;完整 event stream Offloading | run state |
| 3 | 用户原始任务 | 用户目标、约束、偏好 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>input/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 必装 | 低 | 不可丢;高优先级 Pack | user task |
| 4 | 各 Agent 最新产物 | approved/draft/invalid artifacts | <code>runs/<wbr>&lt;run_id&gt;/<wbr>idea/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>experiment/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>coding/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>execution/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>writing/<wbr></code> | 必装 | invalid 污染 | approved summary Pack;invalid Quarantine | artifacts |
| 5 | HITL 状态 | review/edit/approve/reject、人工评论 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>hitl/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 条件装 | 多轮评论冲突 | 最新人工指令 Pack;历史评论 Summarization | HITL |
| 6 | Gate/Evaluation 结果 | gate block、baseline compatibility、quality scorecard | <code>runs/<wbr>&lt;run_id&gt;/<wbr>evaluation/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>configs/<wbr>gates.yaml</code> | 条件装 | 报告太长 | blocker 不可剪;完整报告 Offloading | blockers |
| 7 | 长期 memory | 调度经验、失败诊断、历史修复策略 | <code>configs/<wbr>agent_contexts/<wbr>commander.yaml</code><br><code>knowledge/<wbr>run_archive/<wbr></code><br><code>knowledge/<wbr>methodology/<wbr></code> | 条件装 | 错误策略复用 | 按 failure type 检索;无证据 Quarantine | memory |
| 8 | 运行时动态上下文 | events、trace、tool audit、attempt ledger | <code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code> | 条件装 | 事件流过长 | Pack current blockers/latest attempts;trace Offloading | runtime |
| 9 | 下一步决策上下文 | 给哪个 Agent、修什么、带哪些约束 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>diagnosis/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>feedback/<wbr></code><br><code>runs/<wbr>&lt;run_id&gt;/<wbr>events/<wbr></code> | 必装 | 决策受旧状态干扰 | Pack 最新状态;旧 attempt Quarantine | decision |
| 10 | 最终打包 | 生成调度/诊断/迭代指令 | <code>runs/<wbr>&lt;run_id&gt;/<wbr>context/<wbr>context_manifest.v2.*.json</code> | 必装 | 上下文冲突 | 按下方顺序 Packing | final messages |

**推荐 Packing 顺序**
`system role -> global hard constraints -> user original task -> run state summary -> latest approved artifacts -> blockers/gates/evaluation -> HITL comments -> selected commander memory -> dynamic failure/attempt summary -> next-action decision prompt`

---

## 待定问题

| 问题 | 当前建议 | 用户修改 |
| --- | --- | --- |
| Idea 是否必须装载代码仓上下文 | 是,但只装摘要和 baseline/knobs |  |
| Experiment 是否需要长期 memory | 需要,但只按 hypothesis/metric 检索 |  |
| Coding 是否允许装载历史 patch | 允许,但旧 patch 默认 Quarantine,只 pack 摘要 |  |
| Execution 是否允许复用历史 run | 允许,但必须 fingerprint 匹配 |  |
| Writing 是否需要 Debate 结果 | 需要,但只 pack 分歧和 limitation |  |
| Commander 是否需要长期 memory | 需要,但必须强 Quarantine + evidence refs |  |
| Debate 是否只给 Idea/Writing | 暂定只给 Idea/Writing |  |
| 每个 Agent 的 token 预算 | 未定 |  |
| Context Quarantine 的放行条件 | 未定 |  |
| 最终 Packing 顺序 | 待用户确认 |  |
