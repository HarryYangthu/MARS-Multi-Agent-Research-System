# 冻结合同的模型调用预算

此增量在 `guarded_complete` 接入同一 `run_state.sqlite3` 预算扩展。冻结合同的调用必须由可信宿主绑定 run、task hash、stage 和 invocation；只有 coding stage 能使用 coding 输出额度。模型参数、工具参数及 correlation 不授予 stage 权限。同一合同没有 scope、scope 跨 run、冻结文件丢失或策略不符时拒绝，不回退全局 JSON 账本。

请求前冻结最大 SDK 尝试数、输入和计费输出额度。输入边界按完整 messages/tool schema 的 UTF-8 字节及消息开销保守计数，不宣称是供应商 tokenizer 的精确值；实际 usage 超出预留会保存真实超额并阻断后续操作。SDK 重试数取用户调用配置与合同 operation_retries 的较小值，0 禁止重试。当前只允许已经关闭 SDK 隐式重试、逐次发出 attempt 事件的 OpenAI-compatible provider 家族；其它 provider 明确拒绝。

每个 SDK attempt 前检查累计活动时间与连续同错次数；整个调用由剩余研究活动时间和有限 SDK deadline 共同截止。无法获知供应商是否停止或完整 usage 的调用保留预留和开放活动区间，不能把客户端取消称为撤销收费。相同请求内容与实际 endpoint 身份摘要不因新 invocation 自动重发；此增量没有提供重新发送未知模型请求的恢复按钮。

只有完整 attempt 事件、完整 usage 和一致的实际返回型号才能结算精确 token。输出计费量使用 total_tokens − prompt_tokens，包含未单独解释的计费推理余量，不保存推理内容。不完整 usage 仍是已知下界：completed、failed、cancelled 都至少保留最大预留与已观察用量的逐项较大值，超额不能被未知状态掩盖。没有可核验价格表时费用为未知；不会宣称 20 元上限已精确执行。带价格依据的合同目前要求尚未实现的报价适配器，直接拒绝。

模型回执只包含 run/task/调用身份、实际返回型号、有限 attempt 计数、usage、错误摘要和证据摘要，不保存 prompt、completion、私有 reasoning 或 endpoint 明文。公开 endpoint 元数据隐藏所有 query 值，回执进一步仅保存 endpoint 摘要，避免自定义 URL 路径或参数带入凭据。截断或空最终响应的结构化模型身份同样保留。

## 实际检查

2026-09-28：持久核心、StateJournal、旧模型预算和本适配器定向合跑 **146 passed**，变更的 10 个 Python 文件 strict mypy 通过。本适配器 12 项另经独立复跑通过，包括真实 SDK 到实际未监听 TCP 端点的连接拒绝、零重试、连续两次同错拒绝第三次，以及纯账本下界/权限/身份检查。没有伪造服务或模型成功响应。

真实诊断先经 freeze → create_research_run → StateJournal → 显式预算初始化，再通过实际 GLM-5.3 调用返回一次随机 nonce。最终 v2 扩展回执：**1 次请求/1 次 SDK attempt，34 输入 token、27 计费输出 token**，相同请求的第二次调用在发送前拒绝；未创建旧 model_budget.v1.json，价格未知。脱敏回执见 `evidence/contract-model-real.json`，完整源文件指纹随回执保存。之后增加的只读 host scope getter 不改变该请求路径，并由定向测试检查。

中间第一次真实请求使用当时未发布的 v1 扩展，1 次请求、34 输入/25 输出；保留原始回执，不将它冒充 v2 恢复验收。审查发现部分 usage 下界遗漏后改为 v2，旧 v1 扩展明确拒绝，未静默清零或迁移。自定义 endpoint 凭据残留也经过真实连接失败复现后修复。

此模块不是完整任务预算验收。ToolRegistry、Reader/检索/候选/迭代、训练/远端作业与整个编排 watchdog 仍需各自接入；产品合同 start 继续阻塞，诊断脚本 `scripts/verify_contract_model.py` 不启动研究 DAG。价格未知、候选材料、schema、科学判断和真实实验是独立验收层。
