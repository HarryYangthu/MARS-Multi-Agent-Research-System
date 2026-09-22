# Idea Agent：研究任务到可尝试方案

## 目的与交付

Idea Agent 在理解 PIMC、实际代码与当前任务的基础上，寻找有用的相关研究，读懂其中可迁移的方法，形成一份可交给下一阶段验证的方案。当前阶段完成方案生成、证据检查和独立会话评审；实验收益、科学创新性和真实数据泛化需要下游验证。

同一份方案有两种呈现：`proposal.v1` 及其内嵌的 `idea.handoff.v1` 交接字段供后续 Agent 使用，独立的 `research_handoff.json` 使用 `idea.research_handoff.v2` 保存研究证据和评审收据；工作台展示方案说明、验证办法和研究依据。详细方法在 `method_spec` 中只定义一次，交接字段通过 JSON Pointer 引用它，避免多处定义互相矛盾。

## 固定背景

维护 [`projects/pimc/context/public_context.md`](../projects/pimc/context/public_context.md)。它包含 PIMC 的物理背景、公式、当前关键代码、数据与指标约定、已有研究、无效结果和经验。关键来源带文件位置与内容哈希，历史结论不当作本次实验结果。

`project.yaml` 的 `knowledge_file` 指向该文件。每个新任务自动加载全文；任务首次加载时保存 `input/project_knowledge.v1.json`，包含完整正文及哈希。恢复同一个任务使用其原始快照；新任务读取最新文件。必需背景缺失、为空或超出输入预算时明确失败，不静默截断。

当前任务、代码输入和数据选择仍是独立输入。固定知识不能证明本次已选定真实数据，也不能代替对当前源码的核对。

## 工作过程

1. 加载固定知识、用户任务、输入资料和项目约束，识别需要调研的问题。
2. 自主检索相关研究，说明采用、排除或待补充的原因。停止条件是关键问题获得足够支撑、可迁移的方法已读懂且剩余缺口已说明，不是达到固定文章数量。
3. 摘要用于筛选。采用论文前，实际阅读完整的关键方法、公式、假设，以及所依赖的附录和相关实验。PDF 与全文 HTML 均可提供正文；只下载文件不代表已读完。
4. 结合真实代码形成一个方案，明确改动位置、输入输出、步骤或公式、初始化、适用边界和最小验证办法。
5. 评审者在独立会话中接收原任务、完整背景、实际阅读窗口和候选方案进行评审。当前配置为 DeepSeek V4.1 Flash（API 标识 `deepseek-flash`）生成和评审，分别使用独立会话；发现阻断问题后修订并复核，最多四轮有效评审，受同一个总调用预算约束。
6. 宿主验证 schema、引用对应关系、文件完整性、实际调用及评审记录，输出交付物并进入人工查看状态。

## 论文证据

`research_context` 记录研究问题、选文原则、每个候选的判断、原方法、迁移思路、限制和结束理由。新任务使用 `idea.research_context.v2`：采用 PDF 时通过 `method_pages` 声明关键方法所在页；宿主合并这些页的实际字符窗口，缺少前缀、中间段或结尾时拒绝提交，并显示缺口。HTML 的 `method_pages` 使用空数组。采用项还必须引用真实全文读取返回的 `source_id`，并关联具体 `method_spec` 字段。宿主核对实际 URL、读取记录、文件位置和哈希；评审模型进一步判断方法理解和迁移是否成立。历史 v1 收据按原契约审核，不升级为 v2 通过记录。

PDF 成功下载后保存在运行目录，可在工作台打开。后续页段和字符窗口复用本地文件，不重复下载。大文件、网络限流、不可访问全文和公式提取缺失均保留失败信息；不得把摘要或失败下载标为完整方法证据。

自动检查与模型评审不能保证论文解释永不出错，原文和评审意见保留供人工复核。

## 架构与配置

```text
工作台 / API → Bridge → FocusedIdeaAgent → NativeAgentLoop
                             │                  │
                项目知识 + 当前任务      实际检索 / 正文阅读 / 源码读取
                             │                  │
                             └──── 结构化候选 ───┘
                                        ↓
                                独立会话评审
                                        ↓
                         校验与存档 → 工作台 / 下游交接
```

服务默认使用 `focused_v1`。[`configs/idea_focused.yaml`](../configs/idea_focused.yaml) 控制工具和运行预算；[`configs/agents.yaml`](../configs/agents.yaml) 分别配置 `idea_author` 和 `idea_reviewer`。模型调用及工具次数是可调的运行上限，不是必须读多少篇论文。旧的研究 profile 继续保留，任务快照不同不能混用恢复。

当前聚焦 Idea 路径使用 `loop.max_model_calls: null`，取消原 36 次 LLM 请求上限；共享资源配置 `limits.max_model_requests: null` 同时取消原 256 次总请求门槛。`null` 明确表示不限制请求次数，实际请求、SDK 尝试和用量继续累计记录，模型可见预算也说明该含义。工具、校验修复、评审轮数、token、运行时长和人工停止仍各自生效。此配置用于新任务；已保存的历史任务仍按其原始快照核验，不能通过修改配置重置旧预算或自动重放未知调用。

生成者默认关闭 thinking，使用原生工具检索、阅读和简洁起草，单次输出上限为 16,384 tokens；独立评审仍启用 high thinking。宿主以公开 Observation 重建输入（`native_observation_history`），不回放缺少私有推理历史的 assistant/tool 对话。真实调用、结果与来源保留在审计轨迹，私有推理内容不保存或显示。

`submission_body_field: human_summary` 让初稿直接提交完整 metadata 对象，去掉冗余的 metadata/body 包装；宿主只将作者的 `human_summary` 原样复制为正文，不补写内容。`document_revisions_enabled` 提供 `mars_revise_document`：作者提交当前候选的 SHA256 和明确的 set/remove 字段操作，宿主原子应用后，对完整结果重新执行 schema、材料、约束和模型评审。父路径不存在、数组越界、旧版本哈希或非法值会拒绝整批操作；它不能绕过验收，也不会代写科学内容。初稿仍需要完整提交。

`deduplicate_evidence_enabled` 只在同一次评审输入内移除完全相同的源码/正文副本，保留完整原文、定位信息和原始轨迹。评审强制保留实际源码读取与论文阅读窗口；预算无法容纳时明确失败，不靠截断源码继续给出通过意见。作者应核验评审提出的数学反例，有依据的反驳可以写入 `method_spec.review_resolutions`，不能把评审意见自动当作事实。

格式或结构修复各最多 4 次，实质评审最多四轮，共享总调用预算；有明确完成记录的空响应可消耗同一修复预算重新请求，未知结果、余额错误或未解决的评审问题不会被转成成功。方案可以提出新的参数化，但要把已读的基础方法与新设计分开说明，评审检查推导与迁移条件。上下文格式已更新；旧调用不能用新代码直接恢复，可通过 `--revise-run` 建立保留来源的新任务。

### 固定性能目标

API 可在 `idea_requirements.performance_requirement` 中明确性能门槛。例如：

```json
{"metric":"RES","direction":"minimize","max_degradation":0.0,"unit":"dB","baseline":"matched_run","acceptance_split":"held_out_test"}
```

候选的 `decision_rule.performance` 必须保留这些字段，同时明确 `selection_split=validation`、`report_split=held_out_test`、`status=pending_experiment` 和比较公式。宿主拒绝把 0 改成 0.3、用历史分数替代同条件重跑基线、用测试集挑选方案或声明实验已通过。自然语言中是否也满足同一门槛仍需模型和人工核对；自动检查不证明实验收益。

`acceptance_split` 是可选的显式输入：指定 `held_out_test` 后，候选必须原样保留，区分“验证集选模型”与“保留测试集最终验收”。未指定该字段的历史任务仍按原始约束检查。

工具使用现有统一注册和权限机制。当前直接提供 OpenAlex、arXiv、CVF、NeurIPS、本地知识检索、正文获取及源码读取；通用搜索仅在配置供应商时提供。自研 Tools 可继续通过同一注册层接入。没有配置的 MCP 或 skill 不会被声称已经接通。

当前无需新增 LangChain 或迁移 Idea 执行器。现有 NativeAgentLoop 已承担真实工具调用、记录、恢复和评审；Bridge 中原有编排继续使用。先验证这条单 Agent 路径，再按具体的多分支、人工暂停或可视化编排需求评估框架迁移。

## 本地使用与核验

本地配置需有可用的生成和评审模型凭据，并启用 `MARS_ENABLE_NETWORK_TOOLS=true`。正文域名通过 `MARS_WEB_SEARCH_ALLOWLIST` 配置，例如 arXiv、CVF、NeurIPS、OpenReview 和 PMLR 的官方网站。不要将凭据提交到代码仓。

在工作台创建 Idea 任务会单独运行 Idea Agent。打开结果后可查看完整方案、验证办法、候选文章及原文链接；人工批准才进入后续授权流程。

可通过 `scripts/check_idea_models.py` 检查两个真实模型是否可用；`scripts/verify_focused_idea.py start` 经正常 API 创建并启动真实 PIMC 验证任务。后者默认连接本机 8011 端口，不注入预写答案。`status` 读取当前验证任务状态。

`start --revise-run <run_id>` 把历史候选和评审作为“已有分析”传给新任务，保留旧运行原样。新任务仍调用真实模型、核对原文和源码并独立验收，不使用跳过模型生成的 seed_artifact 路径。

`start --request-file <task.json> --record <record.json> --serve --wait` 在同一进程环境启动真实本地 API，提交指定任务，等待 `waiting_review`、完成或失败后退出。`waiting_review` 表示已生成供人查看的结果，不自动批准或启动下游。`--timeout` 和 `--poll-interval` 控制等待上限；失败以非零退出码结束。任务文件只提供输入，不能包含预写输出。已有服务可省略 `--serve`。

`revise --record <record.json> --reason-file <feedback.txt> --serve --wait` 通过正式 retry API 修订已有任务。Bridge 将最新数值版本的真实候选与反馈一起交给 Idea Agent；作者保留不受影响的内容，只针对具体证据缺口补读。脚本等待新版本，不能把上一版的 `waiting_review` 误判为修订完成。修订仍使用真实作者调用、完整宿主校验和独立会话评审，不自动批准方案。

运行目录保存知识快照、模型和工具轨迹、初稿、评审意见、失败原因和最终交付。结构检查通过、独立模型评审通过、真实仿真完成是不同证据层，报告应分别说明。

对已完成的运行执行 `scripts/audit_focused_idea.py <run_id>`，审核最新数值版本，检查真实作者与评审模型调用、每次输入中的完整知识正文、schema 和证据交接，并生成 `idea/focused_verification.json`。审计包含真实 token 用量、首次/末次调用时间、字段修订次数和合并阅读覆盖率。可读方案同时呈现参数账本和交接前置条件，原始结构化提案仍是规范输出。

### 指定单一模型时的评审

`configs/idea_focused.yaml` 的 `review_mode: independent_session` 显式允许生成与评审使用同一模型。评审仍独立构建输入并检查原任务、实际阅读材料与候选；接受条件、论文证据校验和修订预算保持有效。它不等于跨模型评审，审计结果会分别记录 `independent_review_passed` 和 `cross_model_review_passed`。设置 `review_mode: cross_model` 时必须配置不同的模型；历史快照未填写此字段仍按跨模型要求校验。

模型预检失败会以非零退出码退出，避免凭据、余额或连接失败被脚本当作成功。
