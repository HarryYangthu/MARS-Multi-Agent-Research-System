# 冻结合同本地 CPU 作业适配器

`bridge/research_job_service.py` 将已冻结项目范围、同 run 的 SQLite 预算和独立 `LocalRunner` 连接起来。它是真实作业开发接口，尚未接入 Orchestrator、HTTP/UI 或通用研究 start；不能将单个作业完成当成整个研究完成或科学目标达成。

## 支持的边界

首片只允许 local/cpu、无外部 data 声明的合同，使用当前可用的宿主 Python 解释器，argv 只能是合同声明的一个相对 `.py` 文件；入口还必须列入冻结 command.entrypoint_files。`-c`、`-m`、自由参数、另一解释器、SSH/GPU 或外部数据路径都不在此适配器的授权范围。三个命令 purpose 均要求真实 measurement result，check 也不能用退出码代替合同指标。

这仍是**可信命令执行**。只读快照、敏感环境过滤和 CPU 可见设备设置不是 OS 沙箱，不保证恶意 Python 不读取外部文件、联网、修改权限或逃逸进程组。宿主必须先接受实际入口及其导入代码；将来执行未经审查的模型代码需要独立隔离能力。当前验证可用解释器文件/版本与指纹，不声称已审计所有第三方依赖。

`configs/research_jobs.yaml` 提供日志捕获字节上限和执行快照文件数/字节上限。日志限额不等于磁盘配额。23 项冻结预算不改写；单作业保守预留完整 training_job_seconds、training_process_us、一个 job 槽位和 activity，CPU gpus=0。累计剩余额度不足完整预留时拒绝，不暗中扩容。命令配置、seed、steps 是真实 request 输入并计入幂等摘要；本片不解析程序是否使用某个配置字段。

## 接口与权威

构造 `ResearchJobService(run, ledger, policy)` 后显式 `initialize()`，在既有 `run_state.sqlite3` 内建立独立 version=1 的 `research_job_extension`/`research_jobs` 表。读取不安装或迁移表；身份、版本、部分扩展、binding SHA 不符均失败。没有新增 JSON 或另一数据库作业状态权威。

`submit(ResearchJobRequest, ProjectScope)` 要求当前宿主绑定的 execution stage 为 execution，project/run/task/ledger 全部一致。请求只含 job_id、attempt_id、experiment_id、command_name、config、seed、steps，不能传 argv、cwd、GPU 数或放宽预算。

提交执行以下步骤：

1. 完整审计候选树的真实增删改和权限；将候选复制为新的内容寻址只读 execution snapshot，再独立复核来源未漂移。
2. 从冻结合同取 command 和 required metrics，记录快照、候选差异、可用解释器文件 SHA/版本、完整 LocalJobSpec。
3. 同一个 SQLite 事务提交 job binding 和额度 reservation；只有 committed 新预留才允许启动 LocalRunner。
4. 独立 supervisor 执行 `local_command_request.v1`，产生已有 `local_command_result.v1` / `local_command_receipt.v1`，不引入另一套成功结果协议。

同 job 的宿主锁覆盖 COMMIT 到 spawn，避免另一个查询将仍活跃的派发间隙误判失联。COMMIT 后启动者死亡且无 submission 时，恢复为 unknown，保留全部预留与槽位；任何重复 submit 都只读已存在 job，不补发。操作摘要不含 job/attempt/experiment 标签，因此换标签也不能重复同快照、命令、环境、config、seed、steps 的动作。已知失败的显式重试入口尚未实现，不自动消费额外重试授权。

`status(job_id)` 和 `reconcile_all()` 从同 SQL binding 匹配独立 runner submission、spec SHA 和真实回执；completed 还由 runner 重新校验 request/result/measurement evidence。适配器加验只读 execution snapshot、command_files、时间和环境身份。首次有效终态在同事务结算额度并保存 receipt SHA。已结算的重复 status 继续验证证据，**不移动账本时钟或重写结算/回执**。

恢复不要求原始 source 目录仍存在，也不要求重新运行 live preflight。已验证终态时用实际 `finished_at` 关闭该活动区间；观察时间仍是当前真实时刻，不将过去的结束时刻当成时钟回退。训练用量使用回执的单调 duration，向上取整为整数微秒；已知超额如实记账并阻止后续新预留。

unknown 后取得有效真实终态，只做保守 stopped reconciliation：保留 max(原预留, 已知用量下界)，关闭实际结束时间和槽位，不退款、不自动重放。未知状态的累计占用不能当成确认实际消耗。

## 停止和独立截止

`stop(job_id)` 只接受 StateJournal 身份、权威 request 的 task hash、job extension/binding SHA 都匹配的 SQL-owned job，再与 runner submission 匹配。仅存在 runner 文件而没有该 SQL binding 的作业不可通过此服务停止；不对保存的 PID 直接发信号。

即使 frozen input 暂时丢失，停止也先根据上述可信 SQL 所有权写入本作业 stop 意图，由实际 supervisor 停止它拥有的进程组。预算无法完整核验时返回 budget_state=unknown，并在 SQL job phase 保留 unknown；原 reservation 不变。以后文件恢复且终态回执验证通过仍按 retained 处理，不能利用此前未能打开账本的间隙退款。

LocalJobSpec 新增可选 `not_after_epoch_seconds`，独立截止为 min(提交时刻 + 相对 timeout, 绝对截止)。合同服务在预留时用剩余 activity 和单作业时限确定绝对截止，COMMIT/spawn 延迟不能延长它。未提供绝对截止的旧 direct runner 保留旧 spec JSON/hash 形状及相对截止语义。结果中心使用相同公式验证两种记录。

supervisor 设置 `PYTHONDONTWRITEBYTECODE=1`，真实导入候选模块不会在只读快照生成 `__pycache__`。启动后端被 SIGKILL 后截止继续由独立 supervisor 持有；supervisor 本身失联仍是 unknown，本增量没有声称 supervisor 自身被杀后能自动接管进程树。

## 账本最小接口补充

`settle` 与 `reconcile_stopped` 新增可信 `activity_ended_us`，要求 reservation start <= ended <= 当前 observation。只关闭对应区间；不回退 high watermark，不改其他活动，已关闭区间的结束时刻不可改。只有真实回执或宿主实际动作返回才能提供该时刻。

新增预留在同 SQLite 事务检查 StateJournal status，只允许 created/running；created 用于已冻结开发诊断，不代表 UI 研究 start 授权。等待审核、暂停、取消、失败、完成等状态返回 run_not_executable。既有结算、停止和保守恢复仍可进行；replay 仅表示已有记账，不能再次执行。

`BudgetTransaction.observe_clock()` 为多额度 SAVEPOINT 回滚后保存时钟证据提供入口。它仍验证同 authority，单调 high watermark 与持久 clock_uncertain 不可由调用者清除。

## 验证记录

测试均使用真实临时合同/代码、真实创建 bridge、SQLite、Python 子进程与独立 supervisor，无 provider/tool/service 成功替身。覆盖数值测量和结果中心复用、所有权/命令/数据拒绝、预算拒绝无 spawn、真实并发抢最后槽位、COMMIT 后派发前 SIGKILL 不重发、派发后杀启动者仍超时、停止、回执/快照损坏、source 删除后恢复、unknown 后终态不退款、晚到结束不多扣活动和旧 direct runner 兼容。

保留中间 attempt：最初 13 项失败源于新 fixture 没走 pipeline 创建契约，以及 GPU 在真实 freeze preflight 已被阻断；随后补 pipeline 后仍缺真实 run options。fixture 已改为 `freeze_research_task → create_research_run`，没有放宽运行时校验。基础 14 项随后通过；新增进程和账本钩子的四模块组合 149 passed / 8.39 秒，加入旧 runner 的组合 135 passed / 18.30 秒，再加入 results/local-command 的组合 64 passed / 12.25 秒。这些是中间不同范围的证据，不冒充最终完整验收。

独立审查真实复现并修复两项 P2：同一失败命令换 experiment 标签可再次启动；frozen input 丢失会提前阻止 owned job stop。审查者在真实临时进程上复验四项全部通过，包含 SQL 外 job 拒绝停止、执行快照损坏仍能停止、输入声明丢失仍能停止和换标签拒绝。不存在“作业提交等于研究成功”的回执。

最终七模块定向回归 **225 passed / 26.69 秒**：新 job service（20 项）、既有 durable runner、结果中心、local command、预算核心、StateJournal 和 owned-stop；变更源文件/测试共 **7 文件 strict mypy 通过**，diff check 通过。此结果包含最后两项 P2 修复，不是全仓回归，也不是完整研究 start 或真实供应商调用验收。
