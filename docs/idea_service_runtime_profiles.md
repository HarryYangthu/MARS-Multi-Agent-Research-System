# Idea 服务启动配置方案

默认 `MARS_IDEA_RUNTIME_PROFILE=baseline` 继续使用 `configs/agents.yaml` 的 Flash/native 配置。未设置时也是 baseline。可选 `experimental_research_pro_per_insight_v1` 至 `experimental_research_pro_per_insight_v6` 均标记为 **experimental**、`validated=false`；配置和纯检查通过，不代表产品入口或科学质量已经通过。

服务启动前显式设置 `MARS_IDEA_RUNTIME_PROFILE=experimental_research_pro_per_insight_v1`，再用正常的 Uvicorn 入口 `app.main:app` 启动。选择器只接受上述本地已知名称，不能指定文件路径或 URL。运行时不修改环境、YAML、全局 memory 或已注册 Agent 的配置；改变方案需要重新启动服务。当前不是逐请求选择功能，前端及 `POST /api/runs` 均未增加配置入口。模型设置页仍展示/编辑原 agents.yaml，不表示实验方案的有效设置。

实验方案在 `configs/idea_runtime_profiles.yaml` 定义。主、子 Agent 均为 DeepSeek Pro、JSON actions、32768 输出上限；主作者 thinking low，子作者与独立审查 thinking high。主/子模型调用预算 36/16，工具调用预算 5/10，最多委派 3 次。调研子 Agent 使用每个 insight 单独审查后再整份审查的既有运行时；审查仍计入原预算，调用未知或预算不足不能被配置方案转换为通过。字段审查和整份审查的零重试规则由既有 ReviewPlan 运行时执行。

v2 仅额外启用主、子作者的 `author_empty_completion_repair_enabled`。如果服务已返回明确的空正文错误、正常停止原因和完整的本次用量，并且真实响应已记入 trace，作者可在原有协议修复及模型调用预算内继续一次有效动作生成。既有观察、候选、审查意见和用量都保留，模型和 thinking 不变。该路径不处理未知结果、超时或输出截断，不增加审查次数，也不重抽任何单条或整份审查。它的 v2 名称是服务配置版本，与逐项审查合同 v3 不是同一编号。

v3 相对 v2 只把调研子 Agent 的 `research.review_mode` 改为 `per_insight_collect_then_whole`，使用显式逐项审查合同 v5；模型、工具和全部预算相同。它顺序收集同一候选各 insight 的有效审查意见，再把全部拒绝项一次交作者修订，合计一次 reflection。各单元仍只看到原始任务、自己的 insight 和完整匹配材料，不会收到先前单元的判定；即使最后一项通过，前面存在拒绝时整轮仍拒绝。只有所有 insight 均通过才执行整份审查。作者改稿后每项都重新审查，不沿用上一稿的通过结果。

v4 相对 v3 仅为子研究配置显式增加 `search.cvf_search`，主工具、模型、审查模式及内部预算不变。子工具覆盖只允许已注册、已启用、允许该 Agent 的无审批只读工具，并保留 `search.fetch_sources`。ResearchSession 将经宿主校验的工具范围传给 ToolContext 独立字段；模型参数及 `extra` 不能声明该权限。默认路径与 v1/v2/v3 仍按原工具集合执行，跨 Agent、写操作及禁用工具不能借新范围获权。

v5 相对 v4 仅将主 Agent 工具显式收窄为 `idea.research_delegate` 和 `knowledge.kb_query`。子工具、模型、审查合同和全部预算保持 v4 原值；不修改网络配置或调用者的来源数量要求。选择器为 `MARS_IDEA_RUNTIME_PROFILE=experimental_research_pro_per_insight_v5`。主 Agent 负责决定调研缺口、组合合格报告和提出方案，具体检索与 PDF 阅读交给子 Agent。此前 API v4 沿用完整产品工具集，CLI 调研场景则只给主 Agent 委派与记忆查询工具，二者并非相同的工具实验。第 21 次真实 API 运行在首次委派失败后由主 Agent 直接取文献，最终仅启动 1 次委派却耗尽 5 次工具预算，合格报告仍为零；现有验收正确停止了交付。v5 针对这条行动路径提供独立开关，尚无新的完整真实运行证明它改善了成功率。

主工具覆盖只能选择原 `idea` 工具的安全子集，必须保留委派，不能增加未知、禁用、未获该 Agent 权限、需要审批或具有写操作的工具。普通工具必须已注册；精确的 `idea.research_delegate` 必须符合现有运行时绑定合同，由真实 ResearchSession 注册实例，循环在模型调用前继续检查实际工具及 schema。它不会被错误地当作全局静态工具，也不复用要求保留 PDF 工具的子 Agent 校验器。工具清单同时进入有效配置快照、实际动作提示 schema 和既有循环指纹。v1–v4 未声明主工具覆盖，继续使用原清单。

`max_delegations=3` 是委派上限，所有工具仍共享主循环的 5 次调用预算，并非预留三个委派名额。v5 不自动派发工具、补造研究答案或允许直接读取替代合格报告；最终仍要求真实接受的调研报告及对应方法引用。纯配置与真实第 21 次档案回放只验证清单、schema、拒绝跨配置恢复和旧记录不变，不能据此认定论文相关性、调研恢复能力或完整 Idea 交付已经通过。

CVF 工具要求部署允许 `openaccess.thecvf.com`，使用官方会议目录及论文页元数据，不需要通用搜索 API key；当前仅支持现代 `/content/{venue}{year}/` 布局。元数据和实际 PDF 阅读分别记账。公开验证场景为 `configs/evaluation/idea_research_publisher_real.yaml`；API 只采用其中的问题与要求，有效模型及工具来自显式服务 profile，不能把场景文件中的 CLI 模型配置误认为服务配置。

v6 在 v5 基础上组合两项改动：子 Agent 的 `search.neurips_search` 官方元数据通道，以及主研究配置 `per_delegation_min_sources=1`。该组合不是单变量比较，v1—v5 保留原值。单篇策略要求父 Agent 拆分可由一篇相关论文支持的信息缺口；工具实际 Schema 明示 `min_sources=1`，其它值在创建子任务前拒绝，不静默改写。子作者和整报告审查都接收宿主的单篇交付范围，同时保留完整原任务及委派说明；若自由文本仍要求多篇，须明确处理范围冲突，不能借数值门槛忽略任务。研究员可继续比较多个来源，但每篇采用的材料仍需来源、正文、引用及科学审查。原任务的全局来源要求和父级方法链接门槛不变，失败材料不会自动成为接受报告。

NeurIPS 通道从官方年份目录匹配题名，核对真实论文页及 Paper 按钮，保存原始 HTML 和来源收据。引文元数据中的另一个 PDF 域名不自动变成已验证别名，会议版也不冒充 arXiv 版本。元数据不是正文，后续阅读仍由现有 `search.fetch_sources` 独立生成收据。部署需允许实际官方站点；对应公开场景为 `configs/evaluation/idea_research_single_publication_real.yaml`，显式增加 `papers.nips.cc` 和 `proceedings.neurips.cc`，保留原问题、至少两篇来源/PDF、256 参数上限和模型预算。NeurIPS 的真实注册工具组件已完成检索、下载和正文收据链验证；单篇策略与完整组合仍需新的实际 Agent 验证。独立组件使用指定题名，不能当成自主选文或研究报告通过。

开始及继续逐项审查均按原模型预算预留剩余单元和一次整份审查。结果未知、响应合同冲突、材料装配或输入预算校验失败都立即停止，不通过汇总模式重抽、截断证据或扩大预算。checkpoint 和 trace 保存原始单项判定、同候选汇总及各自哈希；新模式通过合同、plan 的 `failure_mode` 和运行时版本绑定身份。旧 v2/v3/v4 审查合同及默认遇拒即返回作者的行为保持，不能把旧运行迁移为 v5。纯状态检查与已终态第十九次子任务档案只验证边界、来源和旧消息重建；该子任务原稿与修订稿的判定属于不同候选，不是新模式已经收集同稿意见的真实证据。v3 的实际质量和调用效率仍待新的完整真实运行。

v1 保留未启用此功能的行为，baseline 默认值也不变。新增目录条目和配置字段会改变配置收据哈希，因此新服务不能接管旧服务已保存的配置收据；即使名称仍选 v1，也不应宣称旧运行可原样续跑。旧档案保持原始源码、配置和调用记录。恢复只接受已在原循环中记账并持久化的修复状态；旧的终态空响应或结果未知的模型请求不能借新开关获得额外调用。

baseline 和 v1–v4 保留 agents.yaml 中的全部主 Agent 产品工具，包括本地文档、代码仓、baseline 检索及网页搜索；v5 仅在显式选择时收窄主工具。各方案仍保留项目规则、代码仓和数据源的产品默认装载，v5 子 Agent 工具与 v4 相同。公开评估的问题、两篇论文门槛、256 参数限制及参数比值没有写入产品方案；调用者继续通过已有需求字段表达任务约束。项目上下文、子工具和 memory 仍可能不同，v5 也不是公开评估的严格复现；需要新的真实 API 验证。

网络开关、允许域、下载上限、凭证与 memory 仍由正常服务部署设置提供。本方案不会自动打开网络、扩大下载、重置知识库或读取并持久化凭证。服务启动前需检查这些条件；若要对照公开调研，应明确记录允许域及 64 MiB 下载配置。实验方案固定官方 DeepSeek endpoint，关闭 `DEEPSEEK_BASE_URL` 间接覆盖，只保留 `DEEPSEEK_API_KEY` 环境变量名称。360 秒是传给流式 SDK 的超时配置，包括等待流数据，不是完整生成的固定墙钟期限。第十六次冻结版本 `41fb00c` 的作者外层期限按重试和余量计算为 726 秒；ReviewPlan 审查关闭重试。该次外部驱动另设 3600 秒总预算，不能据此声称 API 自带相同运行期限。

注册时同时解析主、子配置。每个请求通过已有 `request.runtime["idea_research_config"]` 接收独立的子配置副本，避免改变另一个请求。初次构建上下文前，在 run 的 `input/idea_runtime_profile.v1.json` 独占写入非秘密有效配置、启用工具列表、工具配置哈希、方案文件哈希及配置哈希；再次构建上下文或开始 draft 时核对相同内容。该文件是配置身份收据，不保存候选、调用数、token、工具结果或审查判定，不是第二份 checkpoint，也不授权恢复运行。

已有收据不得覆盖；不同方案、模型、预算、子审查或工具配置拒绝继续。实验方案不能接管没有原收据的历史执行或续跑；baseline 服务也不能继续带实验收据的运行。旧的无收据 baseline 路径保持原行为。收据缺失、损坏或中途截断明确失败，不自动补写身份。真正的恢复资格、输入/协议 fingerprint、未知工具或模型调用、剩余预算、收据来源和下游 handoff 仍按既有执行器与加载器校验；匹配配置收据本身不能证明这些条件满足，也不替代真实验证所记录的源码提交和原 trace。

第十六次已通过真实 `POST /api/runs` 创建独立 Idea 任务，`auto_approve=false`、不提供 seed，实际经过 Bridge；配置收据和主、子模型 trace 均存在。它在外部驱动原定 3600 秒期限结束，30 次请求、28 次响应、11 次工具调用，已知 651,167 token、用量不完整。研究子任务完成 v2 审查并自动接受，独立复核仍发现统计推断错误；主候选未接受，没有最终双重交付，方案继续标为 experimental、validated=false。详见 [独立 API 记录](evaluation/idea_quality_20260908/api_verification_20260909.json)。未来的 `waiting_review` 也仅表示等待人工审阅，不能称为已批准、完整项目完成或已验证性能收益。
