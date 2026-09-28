# 能力目录：声明、注册与执行证据分层

`GET /api/capabilities` 通过既有后端 owner/session 认证，只读取工具注册、host 配置与本地技能定义。返回 `capability_catalog.v1`，不启动研究、MCP 进程或依赖探测，也不调用工具/模型。产品路径为 `api/capabilities.py → bridge/capability_catalog_service.py → harness`；无新增依赖。

## 当前响应

工具行分开保存以下事实，不能合并成一个“可用”徽标：

| 字段 | 事实范围 |
| --- | --- |
| `declared`、`configured_enabled` | 当前 host tools 配置；遵循 `MARS_TOOLS_CONFIG_PATH` |
| `registered`、`effective_spec_present` | 当前 owner `ToolRegistry` 中实际注册的 handler/spec；不把临时合成的 bridge/runtime 声明算作绑定 |
| `origin` | registered / bridge_only / runtime_bound / unbound；运行时私有 fork 不属于本次 owner 快照 |
| `dispatch_enabled` | 实际 dispatch 使用的配置开关；未声明名称当前仍按既有默认值 true，另明确 `declared=false` |
| `effective_transport`、`effective_binding_sha256` | 实际本地 handler 或注册时 MCP closure 的安全摘要；不是按工具所属概念 family 推测 MCP |
| `roles` | 磁盘与缓存中的角色启用/工具授予，以及注册 policy 的静态交集；不检查调用参数、scope 或 Gates，`execution_authorized=false` |
| `drift_status`、`drift_fields` | 已注册规格与当前配置的可检测差异，包括 MCP 绑定更改/移除；不重置 registry 或运行中 agent |
| `contract_adapter` | 仅五个实际文件 handler 身份返回 requires_host_scope；所有其他工具 unsupported；这不是对某个 run 的授权 |
| `dependency_status` | 固定 unknown / not_probed；导入或命令存在不能替代依赖健康探测 |
| `certification_status` | 固定 not_run；完整回执链验证器尚未接入 |

技能行来自现有 `load_selected_skills` 的真实路径/版本/声明验证，返回定义和内容哈希、required tools、definition_valid。技能没有全局 enabled 状态，故 `enabled=null`、`selection_required=true`。读取定义不等于替任何 agent 选中了技能，也不扩展工具权限。文件缺失、越界 symlink、验收规则无效时明确 invalid_definition，不提供正文或异常原文。

输出不包含工具/技能说明正文、JSON Schema 正文、MCP command/argv、文件根路径、环境值、provider key 或异常堆栈。敏感配置参与不可逆摘要，不被回显。无有效 host 配置时返回安全 503 `capability_catalog_unavailable`。

## 快照与漂移边界

`context_sha256` 绑定本次安全响应、配置摘要、有效缓存声明、技能内容、handler 源码摘要和相关 inventory/policy 实现摘要；同样的快照稳定一致。磁盘角色配置与已缓存的角色配置分别摘要，`agent_configuration_drift` 显式显示不一致。

既有 `_spec_from_config` 有空值/or 回退语义，无法只凭当前注册规格恢复全部历史配置来源。因此相等只能报告 **not_detected**，不能声称“已完整热重载”。MCP handler 的实际 closure 单独保留注册时配置摘要，改名/移除绑定不能把仍注册的 stdio handler 误报成普通本地工具。配置读取期间文件发生变化时拒绝给出混合快照。

此哈希不是执行认证或全环境锁定：未验证网络、凭据有效性、远端身份、运行时私有 registry fork、系统依赖版本或完整源码传递闭包。没有通用缓存刷新/热加载功能。旧 `/api/tools`、`/api/tools/adapters` 与现有 UI 本轮未改；其 MCP `available` 仍只表示配置命令可发现，不能作为认证状态。

当前 checkout 静态盘点是 44 个配置启用声明、27 个 builtin 注册、16 个 bridge_only、1 个 runtime_bound、0 个显式 MCP binding、2 个 skill；动态目录以实际 owner 内容为准，不硬编码计数。五个合同文件适配器不等于五个已完成 GLM 认证的工具。已有真实诊断只证明 repo_reader/write_file 的特定 profile，**本接口甚至不会将这些历史摘要直接置为 passed**。

## 下一认证切片

后续独立 `capability_certification.v1` 验证器需要绑定工具/skill/version、角色、执行 profile、run/task/invocation/toolcall/SQL reservation、上下文指纹与证据哈希，核验：真实 SDK 模型身份 → 真实 toolcall → registry/审批/Gate → SQL settled + tool receipt + 效果读回 → 对应 Observation → 后续真实模型响应 → schema 合规产物。

现有 `contract_tool_receipt.v1`、`runtime.contract_model_receipt.v1`、native loop trace 与资源 manifest 可复用。单独 JSON 的 observed_success 不保证 SQL 已 settled，MCP tools/list、HTTP 200、import 成功、skill 文本和 `ToolResult.ok` 都不能独立证明认证。指纹漂移应标 stale，证据不全应标 unknown；不得根据认证目录自动授予权限或解除合同整体 start 阻断。

## 验证

`test_capability_catalog.py`：**13 passed / 4.05 s**，相关 3 文件 strict mypy 通过。使用真实注册 handler（不调用）、真实临时技能/配置、production uvicorn HTTP 与真实 Python 子进程，无模型/工具/服务成功替身。

- 真实 HTTP 认证、稳定哈希、检查前后 runtime 文件字节不变，且全行 unknown/not_run。
- 实际修改 host tools 开关/timeout/敏感描述与 command allowlist，保留原注册 spec、报告漂移、不回显秘密或路径。
- 实际修改 agents.yaml，区分磁盘与缓存角色，未重启/刷新 agent。
- 损坏 YAML 返回安全错误；真实技能文件修改改变内容摘要，缺失/越界/验收规则损坏不显示健康。
- 注册两个真实函数但不执行，证明同名不同 handler 不继承合同 adapter 标记。
- 独立 subprocess 使用 host tools override 注册真实 MCP handler；命令是写临时 marker 的实际脚本。连续目录读取、MCP remote tool 更改和移除声明均未启动脚本，原 stdio closure 与漂移可见，无 MCP 握手或成功响应。

日志：`release-evidence/productization/20260928/capability-catalog.pytest.log` 与 `capability-catalog.mypy.log`。此验证不宣称外部依赖健康、provider/工具执行成功或 Windows 打包验收。

## 设置页

`设置 → 研究能力` 提供名称、类型与配置授予角色筛选，区分开启、注册、依赖与认证；权限交集和 Schema 指纹位于高级详情。读取失败时清空旧目录并显示错误，显式刷新重新读取，不自动执行、认证或重载能力。角色筛选描述当前配置授予，不能替代任务授权。

生产构建 `ff-frkrcV1rRSLgTrUJ47` 经实际浏览器核验 44 工具/2 技能、搜索与类型/角色筛选、详情、真实断网/恢复及 390px 布局（无水平溢出），零 JavaScript errors、网络全部 GET。TypeScript、定向 ESLint 与实际 API 正例/7 个纯解析拒绝检查通过；source/截图/日志指纹见 [UI 回执](evidence/capability-catalog-ui.json)。零执行请求不表示能力已经认证。
