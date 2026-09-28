# E 前置：正式 Agent runner 消费封存阶段身份

`run_agent_node` 现在识别 SQL 请求中的研究合同标记，恢复已封存的 TaskEnvelope、项目 scope 和原预算账本，再调用注册的真实 Agent。旧任务继续走原路径。合同任务不能通过删除外部合同/metadata 标记降级到旧执行路径，也不能把现成的 v1 种子文件当作本次模型输出。

## 一次派发与结果归属

`research_stage_runtime.load_bound_research_stage` 只恢复已有身份，不初始化预算。它核对 SQL graph 节点与 run 均为 running，校验实际 Agent 的完整有效配置、原始配置和 loop policy。Coding 暂限配置的 native model；外部编码后端和后训练 endpoint 覆盖尚未获得该接线的验收。

上下文读取绑定的已批准数字版本与 host 引用，重新核对 hash；不通过 latest/approved 指针猜版本，不复制旧请求中的 skills、external review 或 context 覆盖。BaseAgent 仍创建自己的真实资源快照和原生 trace。全量编译后 messages、动态注册器以及可替换执行器的认证还不包含在阶段绑定中。

派发前，在同一 `run_state.sqlite3` 中提交 `research_stage_dispatches` 意图，并持有独立不可重入文件锁直到实际异步调用结束。表只记录 effect intent 和不可变 ResultEnvelope，不维护第二套节点状态。SQL intent 已存在时拒绝再派发；进程崩溃、取消或结果提交失败留下未知 intent，不能自动重放模型或工具。显式 checkpoint 续跑、revision 和 owner 重试接线仍拒绝，需后续核对策略。

执行期间同时绑定 ProjectScope 与 ResearchExecutionScope；真实工具及模型预算沿用同一 invocation 和合同 SQL。成功输出先由原 ArtifactStore 校验及版本化，再核对 artifact 的角色目录、数字版本、Schema、项目与 SHA-256，并写 SQL producer receipt。失败回执必须对应同一 task/invocation。结果提交不会变更 graph，即使取消后的迟到回调也不能恢复 running。文件写成功但 SQL 回执失败时保留文件与未知 intent；不能把该文件认作已提交结果，也不能自动重跑。

## 验证

26 项专用回归使用实际 CodingAgent、SQLite、临时文件、真实 file tool 和独立进程，无模型或服务成功替身。覆盖只读恢复、原目录消失、错误生命周期、模型/工具/策略/endpoint 漂移、同线程重复锁、实际候选写入与预算、取消后的迟到结果、非法/不匹配产物、SQLite trigger 拒绝结果、进程退出后的未知意图、seed 不能绕过绑定、续跑拒绝，以及真实缺凭据失败的 SQL 回执。

实际开发诊断经 `run_agent_node → CodingAgent → BaseAgent/NativeAgentLoop → GLM-5.3 → file tool → observation → code_spec` 完成。run `2026-09-28T0226_bound_coding_diagnostic`，invocation `d9a74b2dd524415b80757f897182fce4`：4 次实际请求、15,242 输入 token、1,212 计费输出 token、2 次工具执行、1 个候选扣减，无 unknown 预留。`candidate.py` 从 `x+x` 改为 `x*x`；原源码未改，Schema 合规产物写入同一 SQL 身份。模型返回身份均为 GLM-5.3。费用未知，未执行训练或科学实验。

首次诊断在请求前因 native observation history 未配置失败，模型请求为 0。Coding 配置随后明确使用 native_tools / react / observation-only history，再以全新 run 验证通过；未清空或覆盖失败记录。前一次检查命令曾误指不存在的 test_coding_agent.py，零测试 exit 4；改为实际 test_coding_post_training.py 后，相关组合 142 项通过，后增 5 项专用回归均通过。

[实际公开回执](evidence/bound-coding-real.json) 给出产物 hash、调用计数与执行时源文件指纹。原始 trace、SQL、失败/成功日志及所选实现副本存于本地忽略目录 `release-evidence/productization/20260928/bound-coding-real-{a,b}`。

## 尚未达到的标准

该诊断由开发宿主显式构造一个 Coding graph 节点，未调用正常 product start。`research_execution_admission.ready` 仍为 false。Execution 必须走合同 job adapter，禁止使用旧 execution Agent 路径。本增量不声称完整 Idea/Reader/审查/Experiment/Execution/Writing 编排、项目级 OS 隔离、checkpoint 续跑、暂停/终止调度或正式发布已通过；完整任务启动保持阻断。

全量回归：**3461 passed、181 skipped，202.74 秒**；strict mypy **641 个源文件**通过，**4 条**导入边界通过。181 项跳过涉及外部环境、平台或历史证据前置条件，不记为正向验收。[回执](evidence/bound-stage-checks.json)。
