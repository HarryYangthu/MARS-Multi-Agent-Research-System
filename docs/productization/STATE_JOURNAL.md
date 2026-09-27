# RunGraph 状态与事件事务增量

2026-09-28，工作包 D 的局部实现。保留现有 Orchestrator、RunGraph 与节点状态机；未新增研究调度循环。

新任务以每 run 的 `run_state.sqlite3` 保存权威 RunGraph snapshot 和 `agent_state` 事件 outbox。状态更新、revision CAS 与这些事件在同一 SQLite 事务提交；提交后才更新实时图并发布事件。`run_state.json` 只作为可重建兼容投影，投影写失败不撤销已提交事务，也不触发重复状态转换。

CAS 失败恢复已提交图并把缓存会话置为只读，防止失败的内存修改继续执行。事件发送或确认失败保留 outbox；重放沿用同一 `event_id`，采用至少一次投递，消费者按 ID 去重。真实 API 提供 `POST /api/runs/{run_id}/replay-state-events`；这项操作只重投已提交事件，不运行 Agent。GET 展示不触发事件重放或研究恢复。

旧 JSON 任务显式调用 `POST /api/runs/{run_id}/migrate-state` 迁移：验证身份、图结构和阶段产物，拒绝活动 driver/model lease，保留字节一致的 `run_state.legacy.json` 与 SHA256，再建立权威标记和数据库。产物必须属于当前 project；schema 声明了 run_id 时还须匹配当前 run。加锁或恢复审批指针前检查 run 根、状态/预算/备份/审批路径与产物的符号链接，拒绝越界或间接引用。成功只迁移、不启动研究。缺少持久状态的历史产物仅供查看/下载，启动、恢复、编辑、批准、补丁应用、反馈推进和 self-evolution mutation 都会被拒绝，不能从推断图制造可执行状态。

权威标记存在但 DB 丢失/损坏时明确失败，不静默回退 JSON。初始化先写标记；若进程恰在数据库安装前死亡，将留下明确的不可用状态，需要恢复工具/人工修复；自动修复这类初始化中断尚未实现。此切片只适用于本地单机文件系统；旧应用进程必须关闭后再迁移，不能同时运行旧二进制写这些目录。

已执行的局部验证：22 项新真实 SQLite 测试（真实进程竞争、事务提交前后被杀、SQLite trigger 故障、Redis 拒连、ACK 失败重投、相对目录、CRLF 原文备份、缺失/坏 DB），与既有状态、owned cancellation、历史只读、外部服务只读投影、缺 Agent 回归联合为 66 通过/1 跳过。跳过项需要未提供的真实研究 checkpoint，不以替身补齐。严格 mypy 通过；最终集成回归另见验证索引。

## 停止请求遇到持久化故障

本轮修复 `_request_owned_stop` 的故障路径：尝试写入 `cancelling` 后，无论成功或抛出 SQLite/CAS 异常，都在 `finally` 中调用 `cancel_once`，随后等待本进程实际拥有的异步任务结束。已置为只读的缓存会话仍允许取消/等待其现有 owned task；重试不会再次取消正在进行的异步清理。一个任务的状态写入失败不会中断 shutdown 对其他 owned task 的取消。

请求阶段或清理阶段的持久化错误独立保存在进程内 `stop_state_error`，不会充当另一份权威 run 状态。实际任务已结束但停止状态未能提交时，返回 `ok=false`、`status=stop_state_error`、`state_persisted=false`、`owned_task_done=true`，并通过 `state_persistence_error.type` 和 `phase`（`request` / `cleanup`）保留错误类别与阶段。若任务尚未结束，则仍返回 `stop_incomplete`、`owned_task_done=false`，保持任务引用。API 沿用失败结果的 HTTP 409 响应。`termination` 中的请求信息不能被视作已提交停止状态；持久化失败时不会把未提交的 `cleanup_complete=true` 留作成功结果，也不会发布取消完成事件。

新增测试文件：`backend/tests/unit/test_owned_stop_storage_failure.py`，共 6 项真实测试：请求阶段 SQLite trigger 拒写、真实 CAS 冲突、慢清理期间只读重试且只发送一次取消、清理阶段 SQLite trigger 拒写、清理阶段数据库丢失，以及多任务 shutdown 的取消隔离。这些测试执行实际 owned asyncio task/finally，并检查真实 driver 文件锁释放；没有执行模型或研究 Agent，也不声称模型/工具/实验成功。

本方定向验证：上述文件与 `test_owned_run_cancellation.py`、`test_state_journal.py` 联合 **54 passed / 1 skipped**；跳过项仍是缺少真实研究 checkpoint。新增测试和 `orchestrator.py` 的严格 mypy、`git diff --check` 通过。独立 review 用真实 SQLite trigger 复现原缺陷后，主代理按原复现脚本重跑修复：两次停止都返回 `stop_state_error`，实际 owned task 已结束、取消标志与清理回执存在，没有声称状态已提交。迁移身份/路径与历史只读的最终定向回归为 **58 passed**；完整集成结果见 [验证索引](VERIFICATION.md)。

## 未完成边界

取消信号仍位于同步持久化调用之后。现有 `path_lock` 可无界等待，SQLite 连接超时配置为 10 秒；因此存储锁竞争可能延迟取消信号和停止响应，10 秒也不是整体停止耗时上限。本切片仅验证持久化调用失败返回后仍会取消并等待拥有的任务，**没有验收发布要求中的 2 秒 / 5 秒 / 30 秒停止时限**。完整进程组、外部训练作业及远端执行器停止仍待完成；本地异步任务清理不能代替这些验收。

预算账本、外部训练 job、artifact approval receipt 和其他生命周期事件尚未并入同一数据库事务。旧专用 CLI 状态也没有因此迁移为此状态机。完整 D、运行恢复矩阵、两领域统一研究和远端断连验收仍未完成。
