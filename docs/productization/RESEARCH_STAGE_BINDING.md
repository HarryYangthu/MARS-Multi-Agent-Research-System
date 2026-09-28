# E 前置：同一 SQL 内的阶段调用身份

本增量只为现有 RunGraph 节点保存不可变 `TaskEnvelope`，不启动模型、工具或命令，不新增阶段状态或调度器，也不解除合同 run 的 `execution_admission: blocked`。后续 [正式 runner 接线](RESEARCH_STAGE_RUNTIME.md) 已为合同任务消费该身份；旧 JSON invocation 迁移和完整 Orchestrator 启动仍需独立验收。

## 接口与权威来源

`bridge.research_stage_service.bind_research_stage(run, *, node_key, candidate_id, ledger, goal, output_schema, upstream=(), host_context=()) -> TaskEnvelope` 是可信 owner 显式写入口。传入已初始化的同 run 账本；候选必须已经由 `seal_project_scope` 封存。服务复用 `restore_project_scope` 核验冻结合同、项目元数据、完整快照及候选授权变化，再在同一 `run_state.sqlite3` 的 `BEGIN IMMEDIATE` 事务内保存 `research_stage_invocations`。该表只存输入和 invocation 绑定，不存第二套 pending/running/done 生命周期。

每个 `node_key` 仅一条记录，`invocation_id` 同表唯一。首次绑定仅接受 SQL RunGraph 中已存在的 `kind=agent`、`state=pending` 节点，其显式 `metadata.stage` / 整数 `metadata.attempt` 必须与现有节点命名约定一致。不会从模型参数、目录名或 Agent 自报中推导新节点。SQL 事务锁使并发重复绑定返回同一个 invocation；节点之后由原 owner 转为 running 等状态时，重复读取仍返回原 ID。已有未绑定的非 pending 节点明确拒绝，不制造“恢复身份”。

`restore_research_stage(run, *, node_key, ledger) -> TaskEnvelope` 仅核验已保存 SQL 记录和证据，使用 query-only 事务，不创建表、task JSON、trace、锁文件、审批投影或预算文件。缺失/损坏 SQL 表、记录、hash 或证据时失败。它没有自动补齐、自动重试或重放接口。调用成功只证明该绑定仍可核对，不能证明节点现在允许执行、checkpoint 可恢复或工具结果已知。

`TaskEnvelope.task_id` 沿用 `run_id:node_key`，`agent/attempt` 来自该 SQL 节点，`predecessor_task_ids` 来自该节点的真实入边。`input_sha256` 覆盖冻结 task hash、goal、schema、候选 SQL 封存 hash、节点/依赖身份、配置指纹和经验证的上下文引用。节点状态、revision 和预算用量不混入不可变输入；它们继续由原 StateJournal / ResearchBudgetLedger 维护。读取会验证账本，绝不初始化或结算账本，也不清除 unknown 预留。

## 上游证据与有限宿主上下文

`ApprovedStageInput` 只接受 `predecessor_node / schema_id / stem / source_version / approval_sequence`。不接受上游正文、`success=true` 或任意路径。服务按 ArtifactStore 现有 `SCHEMA_TO_AGENT` 定位本 run 的数字版本文件及 `.approvals/<stem>/<sequence>.json`，核对审批历史前缀、run/stage/stem/sequence/source version、原文 SHA-256、frontmatter Schema 与 project；Schema 声明或正文包含 run_id 时，还核对该 run_id。符号链接、硬链接、越界路径、未批准版本或修改后的 source/receipt 都拒绝。

每个非 skipped 的直接依赖必须处于 SQL approved/done 状态，且有角色匹配的已批准输入。skipped 是现有图中显式跳过，不伪造产物。approved 指针不是证据来源：即使 `*.approved.md` 缺失，也只读取已有不可变 source/receipt，绝不修复指针。新的审批不会覆盖原调用已封存的版本；尝试给同一 node 换新版本会拒绝，原版本和回执仍有效时可核对原 invocation。

旧 ArtifactStore 审批回执没有生产它的 node/attempt/invocation。因此这里的准确声明是“本 run 已批准、供该依赖使用的 Schema 产物”，不能声称已经证明某个父 invocation 生产了它，也不能替代后续输出提交时的生产者绑定。TaskEnvelope 中的 predecessor ID 只是 SQL 图关系。

`host_context` 只允许三个枚举引用：`frozen_contract`、`project_rules`、`project_knowledge`。内容分别来自已验证冻结合同、已封存项目 AGENTS.md 和已封存 knowledge.md；未声明知识不能用字符串补充。宿主引用只记录来源和 hash，不将任意文本包装成上游研究结果。它们不能代替缺失的已批准依赖。

## 配置漂移与权限边界

输出 schema 同时核对 ArtifactStore 的角色映射、当前阶段配置中的真实 output_schema 和 Schema 内容 hash。阶段配置、模型注册、工具声明、Gate、上下文配置及阶段上下文 YAML 的源文件 hash 会封存；`agents.yaml` 包含 baseline loop 声明，另封存 `AgentLoopPolicy.fingerprint_data()` 的有效策略 hash，以覆盖代码默认值。Idea 额外记录现有 profile YAML、profile selector hash，以及已存在的 run profile receipt hash。读取不会调用 profile resolver、创建 profile 快照或加载模型。

SQL 只保存这些来源的相对名称与 SHA-256，不保存配置正文、解析后的 API key、环境变量值或凭据。配置与项目 scope 组合 hash 是漂移检测指纹，不是工具认证或授权清单。任何所检查的源文件变化（包括空白变化）都会拒绝沿用原 invocation，不能静默带新策略恢复；当前采用保守的整份配置文件 hash，其他阶段修改也可能触发拒绝。

本增量尚未封存完整编译后的模型 messages、动态 Skill/工具注册状态、Agent 实例的外部覆盖配置或全部提示资源。实际 runner 接线时仍需核对已构建 Agent / loop / 工具 / profile 与该绑定，并沿用现有 loop checkpoint、trace 与工具 unknown 检查。不能仅因 TaskEnvelope 可恢复就重放原调用。恢复时也没有为原 SQLite 权威提供抗任意同机 SQL 篡改的签名或 OS 隔离保证。

## 真实验证

`backend/tests/unit/test_research_stage_service.py` 使用真实 RunStore、冻结合同、sealed scope、RunGraph、ArtifactStore 审批与 SQLite 事务；人工编写的 Schema 文档标明是计划，不冒充模型或科学实验结果。覆盖：

- 同节点首次/重复/只读恢复，动态 attempt、缺表、未知节点、跨 run/ledger、不同 goal/schema/candidate/context 拒绝。
- 真实已批准输入、审批指针缺失时不修复、未批准/错项目/错依赖/源或回执损坏和链接拒绝。
- 实际节点转换后 invocation 不变，图中角色/attempt/依赖变化及 SQL binding 损坏拒绝；实际 SQLite trigger 拒绝插入后无新身份或 trace。
- 四个独立 Python 进程同时争用同节点，仅一条记录和一个 invocation；进程 `os._exit(0)` 后新进程核对同一 ID。
- 真正 Git patch 失败后的已计费工具次数与 unknown 预留不重置；原源码目录消失仍可恢复。
- 隔离资源目录中的 loop/tool/context/schema 配置源改变，进程重启后拒绝恢复且原 run 文件不变。

这些是持久身份和输入完整性验证，不是正式模型研究、阶段输出生产者绑定、完整 E 自动闭环或执行准入验收。

父任务独立检查实际删除 state_events 后，发现早期版本仍能恢复已绑定阶段；随后在共享 scope 恢复的身份检查中复用核心 SQL schema 验证。新增缺失日志表/状态表/身份表、日志列改名、用 view 替换日志表五项真实拒绝用例，恢复与再次绑定均不修复原文件。最终本模块 59 项，与 scope、旧 task runtime 和 ArtifactStore 联合 **97 项通过，8.36 秒**；相关 3 文件 strict mypy 通过。首次组合命令误写不存在的 test_task_runtime.py，exit 4 且零测试，改成真实文件名后才得到上述结果；失败日志保留。
