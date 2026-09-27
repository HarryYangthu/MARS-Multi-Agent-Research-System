# 冻结研究预算的 SQLite 持久核心

此增量提供 `harness/runtime/research_budget_ledger.py`，只处理可信宿主提交的预算记录与原子准入。模型边界适配器另行验证；ToolRegistry、Reader、Orchestrator 和本地 runner 仍需接入，合同研究依然阻断，不能把本模块测试当成全部预算已约束真实研究。

## 权威与显式升级

所有表位于原 `run_state.sqlite3`，不新增 JSON 或另一 SQLite 权威。StateJournal 主 schema_version 仍为 1，预算扩展有独立 version=2。`initialize()` 是唯一建表入口，在 BEGIN IMMEDIATE 内创建完整扩展并冻结 run/journal/task SHA-256、23 项预算及可选价格依据摘要。重复相同策略幂等；策略、身份或不同扩展版本拒绝。

尚未发布的 v1 试验扩展缺少未知用量下界记录，不能原地自动升级。读取或初始化 v1 都明确拒绝，并保留原数据库字节；历史试验回执仍可作为原版本证据，不能当成 v2 验收。未来迁移须另行显式实现，不能清空重建账本。

首次安装只允许 authoritative run request 的 research_task_sha256 匹配，状态 created、全部节点 pending 且不存在旧 model/Discovery 账本。已有执行或历史用量必须另行实施显式迁移；当前不会导入、清空或假装原账本用量为零。已有预算扩展不会在只读 snapshot 时升级，缺表/部分表/缺数据库/身份或指纹损坏均失败。

StateJournal 新增 `transaction()` 与 `commit_in_transaction()`。budget 的 `in_transaction(connection)` 仅接受同数据库、同 journal 且已开启的事务，使额度、活动区间、租约、图状态和 outbox 可以共同提交或回滚。持久核心不发布事件、不启动进程。

## 预留、幂等与未知结果

`BudgetReservation` 绑定 reservation_id、operation_id、操作输入摘要、attempt_index、类型和最大可能用量。相同 reservation_id/内容返回 replay=True，**调用方不得再次执行**；同 ID 不同内容拒绝。同一操作摘要不能换 ID 绕过限制。新尝试必须连续，前次必须有明确失败回执；成功、进行中及 unknown 均不能自动重试。

`reserve()` 返回 BudgetAdmission。只有 admitted=True 且 replay=False 表示新增预留；在事务提交成功后才可发起对应动作。预算拒绝返回原因，不抛出预算耗尽异常，以便时钟不确定标记仍能提交。身份/策略/损坏冲突抛异常并应让整个外层事务回滚，调用方不得吞掉异常后继续启动动作。

`settle()` 接收实际用量、成功/失败、证据引用及证据摘要；失败还需错误指纹。重复结算内容必须一致。实际用量超过预留仍被如实保存，reservation_overrun 会阻止后续新增操作，不能通过拒写超额用量来掩盖事实。

预留、结算和持久读取都校验数量类型：模型请求、token 与非零模型金额只可记入 model，训练/GPU 时间只可记入 job。model/tool 的预留与明确结算至少包含一次对应请求/执行；未开始或结果未知不能用空的成功结算退款。普通业务计数可复合记账，例如一次搜索工具同时预留 tool_executions 与 search_candidates。明确实际用量可少于预留，不能因此把最大候选数或训练时长当成已消耗量。

进程崩溃不会退款：已提交 reserved 继续占用，未提交事务由 SQLite 回滚。持久核心不靠 PID 猜测 owner 死亡，宿主取得真实失联证据后显式 `mark_unknown(reason, observed_lower_bound=None)`。可选 BudgetAmounts 下界单独持久保存并带哈希，不改原预留；每项占用为 max(原预留, 已知下界)，重复观察只能提高下界。已知下界超额同样设置 reservation_overrun，不能因总用量未知而丢弃已知超额。unknown 保留活动区间和 Reader/Job 槽位。明确确认工作已停止后可 `reconcile_stopped(actor, evidence_refs)`，关闭活动并释放槽位，但仍保留上述保守用量、unknown 标记且禁止自动重放原操作。

## 计时、金额与 23 项字段

累计资源包括 search_candidates、deep_read_papers、proposal_candidates、implemented_candidates、debate_rounds、automatic_iterations、model_requests、tool_executions、input_tokens、billed_output_tokens、training_process_seconds、gpu_seconds、model_cost_cny。时间换算为整数微秒，金额换算为整数微元；不能精确表示为整微元的预算拒绝，无浮点金额加减。

research_activity_seconds 单独按活动区间并集核算：并行操作只计算重叠墙钟一次，无活动的人工等待不计入。每个预留开启一个区间，明确结算或确认停止才关闭。unknown 区间保守保持开放，所以 activity_us 可能是上界，不能在 UI 声称都是已确认工作时长。

并发 reader/job 槽位与 GPU 张数在预留事务内检查并持久占用。单请求输入/普通输出/coding 输出上限、单作业时长也在预留前验证。coding 类别须由将来的可信 stage 绑定器提供，不能让模型自行选择。SDK 最大尝试数按模型请求预留量计入该 operation 的额外重试预算；连续相同错误和相同证据达到 repeated_error_limit 拒绝下一尝试。网络层每次错误仍需将来接入，本模块不会自动中断供应商 SDK 内部循环。

每次写入观察时钟，持久 high watermark 单调增加。遇到时间回退，持久 clock_uncertain 并保守占满活动预算，后续新增动作拒绝；不会用负时长退款。只读 snapshot 可报告观测回退但不写状态。没有自动时钟核对/清除接口；不能仅等待时间恢复就悄悄清空不确定性。测试的 now_us 是确定性时间输入，不是实际研究执行证据。

有价格依据摘要时，模型最大费用必须用整微元提供；已知价格预留而实际费用未知时保留该上界，cost_usage_exact=False。没有价格依据时模型费用必须为未知，used.model_cost_micro_cny=None、cost_ceiling_enforced=False，known_cost_subtotal 仅为已知部分，不得显示成完整零费用。价格依据摘要由后续可信价格加载器核验；本模块不检索报价、不证明其来源真实性。

所有 23 项字段都有上述数据或准入接口，但**合同 start 仍未开放**。活动 watchdog、子进程 deadline/资源分配、上下文计数、provider usage/价格匹配、Reader/候选/迭代身份归一和各级重试事件须分别接入并验证。并发槽位是账本租约，不是 OS 锁或自动 owner 探测；金额上限受控也只针对实际接入并正确报价的调用，不能宣称整个当前产品已启用。

## 接入接口

`ResearchBudgetLedger(journal, task_sha256, budget, price_reference_sha256=None)` 中 task_sha256 严格采用现有 FrozenResearchTask 的 **64 位 hex、不带前缀**。operation/evidence/price_reference 摘要使用 `sha256:` 前缀。每次读取和写入都会核对同 run 的 `input/research_task.v1.json`：领域无关 ResearchTaskContract schema、完整任务规范哈希、项目身份及预算必须匹配；拒绝 missing/损坏/符号链接。不会读取实时源代码或启动 preflight。这样首次初始化也不能将全局预算误绑到现有合同哈希。

`reserve(BudgetReservation)` 返回 `{admitted, reservation_id, replay, reason}`；BudgetAmounts 使用上文累计字段，其中训练/GPU 单位为 `training_process_us`/`gpu_us`，金额为 `model_cost_micro_cny`。模型操作须提供单次正 input/output 上界和普通/coding 类别，amounts 至少覆盖最大 SDK 尝试数乘以单次 token 上界；job 操作提供 job_duration_us、gpus 并预留完整时长。

`snapshot()` 返回 task_sha256、used、limits、known_cost_subtotal_micro_cny、cost_ceiling_enforced、cost_usage_exact、activity_us、activity_remaining_us、clock_uncertain、reservation_overrun、unknown_reservations、active_readers、active_jobs、active_gpus。limits 中是累计资源上限；单次/并发上限仍来自同一冻结 ResearchBudget。`unknown_reservations` 包括确认已停止但用量仍未知的 retained 项。调用方不能只看 admitted 而忽略 replay，也不能把已预留上界画成实际消耗。

异常返回错误类型而不是假额度：BudgetNotInitialized 表示显式安装未完成；BudgetConflictError 表示身份/策略/幂等内容冲突；RunStateIntegrityError 表示权威、冻结文件、表版本或持久记录不可相信。

审查保留项：最初持久层 fixture 使用带 `sha256:` 的 task hash，没有覆盖真实创建路径，父任务审查发现与 FrozenResearchTask 的 64hex 规范不符。已修正类型并新增真实 freeze → create_research_run → StateJournal → budget initialize 集成，所有持久层 fixture 也改为真实冻结文件；随后补首次 budget 替换与冻结输入损坏拒绝，不将早期纯持久层通过当成接入通过。

## 验证记录

2026-09-28：新预算核心、既有 StateJournal 和 owned-stop 存储故障回归合跑 **93 passed / 2.48 秒**，3 个变更 Python 文件严格 mypy 通过，diff check 通过。覆盖真实 SQLite 多进程抢最后额度、实际进程在 COMMIT 前/后被杀、预算/状态/outbox 同事务回滚、活动并集和确定性时钟回退、Reader/Job/GPU 槽位、所有累计和单次额度、SDK 尝试重试上限、未知用量保留、金额整微元、报价未知、首次预算替换拒绝、真实冻结合同创建链路和冻结文件/DB/表/身份损坏。

中间纯持久层 21 项以及联测 86/88 项通过不包含最终真实合同哈希接入修正；修正后联测 89 项通过，补冻结输入拒绝后得到当前 93 项结果。没有调用模型或执行研究，没有伪造工具/服务成功，也未把纯计账 fixture 当作实际消耗或端到端预算验收。

独立审查随后发现模型适配器不完整 usage 的已知下界可能被较小预留覆盖，以及核心允许跨资源类型结算。纯回执输入 output=80、reserved=8 时旧行为记录 8 且允许继续；这不是供应商执行成功证据。v2 新增持久下界并拒绝错误类型/空 model-tool 结算后，同三模块合跑 **117 passed / 2.71 秒**，严格 mypy **3 文件通过**，diff check 通过。新增覆盖单调下界、已知越界继续阻断、原预留不变、unknown 活动/槽位保留、停后保留、下界/结算内容及哈希损坏拒绝、真实旧 v1 表结构拒绝自动升级。
