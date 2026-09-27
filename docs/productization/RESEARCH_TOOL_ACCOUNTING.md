# D：合同文件工具的原子预算切片

本切片接入 `ToolRegistry.dispatch`，只覆盖真实的 `code.repo_reader`、`code.write_file`、`code.apply_patch`、`code.delete_file`、`code.rollback_patch`。全局冻结合同 start 仍阻断；搜索、论文读取、提案/辩论/迭代、命令、作业等不能凭工具次数预留获得准入，各自需要额外预算适配器。本模块不新增调度器、状态库或 JSON 账本。

## 身份与执行顺序

1. 在 trace、approval、tool event 落盘之前核验主机 `ResearchExecutionScope` 与 `ProjectScope`，两者须属于相同 run、task hash 和规范 run root。冻结输入、SQLite request 或 run 元数据仍有合同标记时，缺 scope 必须拒绝。模型 args/extra 不能创建这些 ContextVar，也不能重定向 run root。非合同工具沿用原 dispatch。
2. 校验已注册真实 handler 身份、工具配置、agent 权限、输入 schema 和适配器支持的参数；拒绝未支持参数、外部 patch_path、dry_run、歧义 patch/rollback、不可证明的文件路径。完整 unified diff 和 rollback 每一项都参与实际文件集合校验，不能相信调用方的 files 声明。
3. 现有审批及 Gate 5 继续位于 dispatch 必经路径。拒绝、schema 错误及待审批都不消耗工具执行次数。删除仍需真实审批记录；scope 不授予命令、数据、网络或审批权限。
4. 使用每次调用独立的 FileLock 实例对同一 run 的文件执行串行化，不跨 await 复用同线程可重入的共享锁。非阻塞等待受工具 timeout 和剩余 activity 限制。获得锁后重新验证文件指纹及 rollback 原文 hash，变化则要求重新准备。
5. 在现有 StateJournal SQLite 事务中预留预算，提交后才调用真实 handler。成功必须具有 schema 合法的实际结果与重新读取的文件状态；回执记录真实前后 hash、可执行位、changed_paths、结果摘要 hash。写入的源代码或文件变化不是测试通过、科学指标或研究成功证据。

## 工具次数与候选额度

每次 handler 的预留为 `kind=tool, tool_executions=1`。写入、补丁、删除及回滚还按 `task + snapshot_id + candidate 相对目录` 派生稳定候选身份，首次进入实现预留 `kind=implementation, implemented_candidates=1`。两次预留位于同一个 SQLite 事务/SAVEPOINT，任意额度拒绝都会撤回整组；时钟观察通过核心 `observe_clock` 保留 high-watermark/uncertain，不能因为回滚额度而消除时钟异常。

同一候选后续修改只扣新的工具次数；不同候选受 `implemented_candidates` 总上限约束。implementation 既有预留的 replay 仅证明额度已占用，不能宣称候选已完成实现或通过验证。首次真实工具成功时工具与候选预留在同事务结算；首次执行失败或取消时二者均保留。后来同候选的成功操作不会清除旧 unknown。

## 重复动作与补丁审批

工具操作指纹包含冻结 task、snapshot、candidate、工具名、实际语义参数和目标文件执行前内容 hash/可执行位；不使用随机 call_id、host invocation_id、审批 ID 或无关 extra。重复调用即使换标签也不能重新触发 handler。

补丁 `version` 影响真实结果文件名，因此由主机根据操作指纹派生 `host_<sha256>`，Gate、审批与 handler 全部看到相同的 host version 和原始 diff。调用方的 requested version 只留 SHA-256 摘要，不明文进入 trace、审批或结果元数据。额外 files 必须与完整 diff 的实际路径一致。

真实 A→B 修改后读取 B 是新内容，可以准入；真实回滚到已经读过的完整 A 后再读 A 仍保守阻断。本切片没有 JSON 文件代次权威。未来若支持这种合法循环，需要使用 SQLite 已提交事实推导代次。

不自动重试：attempt_index 固定为 0，失败或未知动作保留预留，不能通过随机版本绕过冻结 `operation_retries` / `repeated_error_limit`。这是更保守的上限执行，不宣称实现全部合法重试策略。

## 故障、取消与证据解释

- 失败、取消、超时、输出 schema 错误、文件观察不一致或结算失败：保持计费预留，并尽力标记 unknown；实际 handler 未开始时 lower bound 为 0，但保守预留仍保留为 1。
- 进程在预留提交后退出：SQLite 保持 reserved，不能根据缺少回执推断未执行或自动重放。没有伪造完成回执。
- `resources/tool_receipts/*.json` 是实际文件观察证据，不是第二套预算权威。回执落盘后 SQLite 结算失败时，可能存在 `observed_success` 回执而权威预留仍为 unknown；API/tool result 返回失败并保留错误类型，不能只看文件名或回执存在宣称已结算。
- 成功结果 metadata 包含 `budget_outcome=settled`、operation_id、changed_paths、candidate_entry_charged；最后一项表示首次进入实现额度，不是候选验收成功。unknown 包含保留状态与可见 accounting_errors。
- 成功结算的 activity end 来自真实 handler 返回时刻，不把之后的回执/SQLite处理等待冒充工具执行时间。未知活动继续保留，等待有证据的显式 reconciliation。
- asyncio 可取消真实 Git subprocess；同步文件系统调用、既有 trace/path_lock、SQLite busy wait 不能被 asyncio 硬抢占。本切片不证明 2s/5s/30s 停止时限，也不是 OS 文件沙箱；外部进程并发篡改与完整进程组/远端清理仍属后续边界。

## 主机调用接缝

已显式初始化 ResearchBudgetLedger v2，且真实冻结合同已绑定到该 run 后：

```python
scope = prepare_project_scope(run, candidate_id="candidate_1")  # bridge 层调用
execution = ResearchExecutionScope(ledger, stage="coding", invocation_id="host-owned-step")
ctx = ToolContext(run_id=run.run_id, project=run.project, agent="coding",
                  extra={"run_root": str(run.root)})
registry = get_registry()  # 安装真实工具与默认 Gate 5；不另外注册替身
with bind_research_execution(execution), bind_project_scope(scope):
    result = await registry.dispatch("code.repo_reader", {"path": "src/candidate.py"}, ctx)
```

创建 ledger 及 scope 是 host 行为；不能从模型工具参数构造。catalog 可读取 `registry.spec(name)`，但存在 spec 不表示冻结合同支持该适配器。本 slice 只允许上列五个名字且 handler 必须是原生真实实现。`prepare_project_scope` 本身不授予研究总 start、命令或模型权限。

## 已执行验证

`backend/tests/unit/test_research_tool_accounting.py`：30 项真实文件/SQLite/进程用例通过，包括原生读写与重读、真实 Git check/apply、实际 rollback、Gate 5 阻断、临时文件中的显式审批、SQLite trigger 拒绝预留/结算、同进程异步额度竞争、跨候选实现上限、整组额度回滚、保守 unknown、真实文件锁超时、真实时间耗尽，以及真实 spawn 子进程在预算提交后被 kill。

`backend/tests/unit/test_run_project_scope.py` 的 registry Gate 5 用例已改为显式建立真实状态库/预算并绑定两个 scope；其余独立文件 scope 用例不隐式获得预算或执行权限。最终与工具 runtime contracts 联合回归 100 项通过（30 新工具预算、62 项目 scope、8 既有工具 runtime 用例）。新模块、registry 及两个测试文件的 strict mypy 通过。

这些测试未使用模型/工具/服务成功替身，未调用研究模型或 GPU，也没有解除产品合同总准入阻断。
