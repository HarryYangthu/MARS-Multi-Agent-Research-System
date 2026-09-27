# 冻结合同保存：持久请求身份与核对恢复

此增量只保存合同任务，不启动研究。运行图及预算仍只以各 run 的 `run_state.sqlite3` 为权威；新增 `runs/.creation_requests/catalog.sqlite3` 只保存创建请求的身份与分配关系，不复制运行状态、预算、模型回执或实验结果。

## 接口

`POST /api/research-contracts/runs` 在既有 `name`、`contract` 外接受可选 `request_id`。ID 为 8–128 个 ASCII 字母、数字、`_`、`-`，首字符必须是字母或数字，推荐客户端先持久保存 `crypto.randomUUID()`。ID 在同一 runtime 的 runs 根内唯一，稳定绑定 `name.strip()` 与冻结 `task_sha256`，没有自动过期/回收。

- 成功或已核验的重复请求：HTTP 201，保留原创建回执，增加 `request_id`、`idempotent: true`。返回同一真实 run，不再次创建。
- 同一 ID、不同名称或合同：HTTP 409，`detail.code = creation_request_conflict`。
- 已登记且持有创建 lease：HTTP 202，返回 pending 核对 envelope。
- 不能确认创建结果：HTTP 409，`detail` 为 unknown 核对 envelope。不会自动重建或换 ID。
- 分配之前的真实 live preflight 明确拒绝：HTTP 422，`detail` 为持久 rejected envelope，`admitted: false`、`run_id: null`。用户可以明确修改、重新冻结后使用新 ID。旧 rejected ID 永不隐式重试。
- 无 `request_id` 的旧客户端保持兼容：仍创建真实任务，回执 `request_id: null`、`idempotent: false`；网络重试可能创建另一任务。当前 CLI 仍属此类，不宣称已有请求幂等保障。

`GET /api/research-contracts/requests/{request_id}` 只读核对，返回：

```json
{
  "request_id": "client-generated-stable-id",
  "task_sha256": "64位冻结合同哈希；尚无登记证据时为null",
  "status": "pending | created | unknown | rejected",
  "admitted": "pending/created为true；unknown为null；rejected为false",
  "run_id": "已绑定的真实run ID，或null",
  "research_started": false,
  "run": "created时为真实创建回执；其余为null",
  "reason": "稳定原因代码，或null"
}
```

`status=created` 表示创建身份已核验，不是当前运行生命周期的副本；运行详情仍从原 run API 获取。客户端必须核对 origin、request_id、task_sha256；不能只凭 2xx、ID 或字符串 `created` 解锁。GET 未登记返回 404 `creation_request_not_found`；catalog 缺失/损坏返回安全 503 `creation_catalog_unavailable`。404、unknown、网络失败均不能证明上次请求未产生副作用，不能自动清锁重发。

初次 preflight 持有 lease 但尚未登记意图的窄窗口，重复 POST 返回 unknown、`task_sha256: null`、`reason: creation_preflight_in_progress`，随后可只读查询。所有请求继续经过原 owner 与 desktop session 认证；此查询不提供另一入口绕过认证。

## 持久边界

1. POST 显式初始化 catalog schema v1；GET 使用 SQLite `mode=ro`、`query_only`，不会初始化/升级 catalog、创建 lease 父目录或修复运行投影。
2. 每个 ID 持有独立跨进程 OS lease，SQLite 唯一键与 `BEGIN IMMEDIATE` 保护意图提交。已有 ID 不读取可变源文件，直接核对保存证据。新 ID 先做真实 live preflight；拒绝仅记录 rejected，完全不调用 RunStore/Orchestrator。
3. 预检通过后，以 SQLite `synchronous=FULL` 提交 pending 意图，再调用原 Orchestrator。它在实际 `RunStore.create()` 返回后，立刻通过 host `on_run_allocated` hook 持久绑定真实 run_id，然后沿原路径写冻结合同、request options、run metadata 与 StateJournal。
4. 完成后从真正的 SQLite authority、request、冻结文件及元数据核对名称、项目、入口、目标与合同哈希，再标记创建完成。GET 不调用 `Orchestrator.session()` 或 `RunStateStore.load()`，避免恢复/审批投影副作用；使用相同纯 snapshot 校验器读取实际 authority。
5. lease 消失且权威证据完整，即使进程死于 StateJournal 提交后、catalog completion 前，也能核对为 created。证据缺失或坏损时保持 unknown；旧 request 不能重新调用 create。删除/回收站中的 run 不会被重建。

**已知不可消除窗口：** RunStore 的 mkdir/meta 写入与 allocation callback 之间，进程退出可能留下未绑定孤儿目录。此时 catalog 查询为 unknown、run_id=null；不会按名称猜测目录、自动认领或重建。这不是“没有孤儿 run”的承诺。首次预检通过到原 Orchestrator 再次验证之间，源文件也可能变化；一旦意图已提交，此类后续异常保守保持 unknown，不能冒充 rejected。

catalog 丢失而已有 lease 证据时，显式初始化也拒绝重建空 catalog。catalog/lease/authority 符号链接及 catalog 多硬链接被拒绝。当前不承诺抵抗同权限进程并发替换任意父目录，亦没有 catalog 修复、遗忘、重绑或孤儿认领操作。

## 真实验证

`backend/tests/unit/test_research_creation.py`：24 passed，6.99 s。使用真实临时文件、SQLite、production uvicorn、实际 HTTP 客户端及实际子进程；没有模型、工具、服务替身。

- 24 个并发 HTTP 提交只分配一个实际 run，完整回执指向同一 ID。
- 真实 SQLite writer 暂时持锁导致 HTTP 客户端在发送 body 后发生 ReadTimeout；释放锁后 production 后端继续完成原保存，GET 查回同一 run，随后重复 POST 不产生第二个任务。
- 四个真实 SIGKILL 边界：只有意图、真实 RunStore 分配但尚未绑定、已绑定但未提交 StateJournal、StateJournal 已提交但未标 catalog 完成。前三者不猜成功、不重建；最后者核对恢复原 run。
- 停止并重新启动 production uvicorn 之后，GET 与同 ID POST 仍核对原 run；源文件已删除时同 ID 重放不依赖 live preflight。
- 真正的预检失败获得持久 rejected；恢复源文件后旧 ID 仍拒绝；明确新 ID 才保存。
- 合同、metadata、authority 损坏、run 移入回收站或 symlink 时 unknown 且无新分配；查询前后保存文件字节不变。未登记 GET 与真实 401 均不修改 catalog。
- 原客户端不传 ID 的行为明确为非幂等。

中间失败保留：首轮 14 passed / 2 failed，其中并发断言没有包含预检窗口的明确 unknown 409，新重启 fixture 的测试 token 不满足原安全规则；第二次联测 109 passed / 1 failed，重启 fixture 仍缺原安全规则要求的显式 origins。修复的都是测试配置/断言，未放宽认证或改变生产安全规则。最终新增模块 24 passed / 6.99 s；新增模块最后一个 timeout 用例之前，与既有合同 admission、真实 CLI 的联合回归 114 passed / 21.66 s；strict mypy 5 文件通过，git diff --check 通过。重启测试的 POSIX 继承 socket fixture 在 Windows 明确跳过，不冒充 Windows 打包/运行验收。


后续只读审查真实复现：把 `.initialize.lock` 硬链接到自有临时目录的 27 字节 sentinel 后，原 FileLock 初始化取得锁会截断该 inode，使外部 sentinel 变为 0 字节。现已在进入 `path_lock` 前要求初始化锁为单链接普通文件，定向回归验证拒绝后外部内容完全保留；未扩大通用锁组件。另将保存记录中的无效 request ID 统一为 `CreationCatalogIntegrityError`，避免内部解码契约泄漏普通 `ValueError`。常规 GET 的合法 URL 与 SQL 精确键查询本就使无效 ID 记录难以被选中，因此未把纯解码失败冒充已复现的 HTTP 500。
