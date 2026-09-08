# Idea 服务启动配置方案

默认 `MARS_IDEA_RUNTIME_PROFILE=baseline` 继续使用 `configs/agents.yaml` 的 Flash/native 配置。未设置时也是 baseline。可选 `experimental_research_pro_per_insight_v1` 目前标记为 **experimental**、`validated=false`；配置和纯检查通过，不代表产品入口或科学质量已经通过。

服务启动前显式设置 `MARS_IDEA_RUNTIME_PROFILE=experimental_research_pro_per_insight_v1`，再用正常的 Uvicorn 入口 `app.main:app` 启动。选择器只接受上述本地已知名称，不能指定文件路径或 URL。运行时不修改环境、YAML、全局 memory 或已注册 Agent 的配置；改变方案需要重新启动服务。当前不是逐请求选择功能，前端及 `POST /api/runs` 均未增加配置入口。模型设置页仍展示/编辑原 agents.yaml，不表示实验方案的有效设置。

实验方案在 `configs/idea_runtime_profiles.yaml` 定义。主、子 Agent 均为 DeepSeek Pro、JSON actions、32768 输出上限；主作者 thinking low，子作者与独立审查 thinking high。主/子模型调用预算 36/16，工具调用预算 5/10，最多委派 3 次。调研子 Agent 使用每个 insight 单独审查后再整份审查的既有运行时；审查仍计入原预算，调用未知或预算不足不能被配置方案转换为通过。字段审查和整份审查的零重试规则由既有 ReviewPlan 运行时执行。

它保留 agents.yaml 中的全部产品工具，包括本地文档、代码仓、baseline 检索及网页搜索，也保留项目规则、代码仓和数据源的产品默认装载。公开评估的问题、两篇论文门槛、256 参数限制及参数比值没有写入产品方案；调用者继续通过已有需求字段表达任务约束。工具增加、项目上下文和 memory 不同，意味着它不是公开评估的严格复现；5/10 工具预算也可能被项目工具消耗，需要新的真实 API 验证。

网络开关、允许域、下载上限、凭证与 memory 仍由正常服务部署设置提供。本方案不会自动打开网络、扩大下载、重置知识库或读取并持久化凭证。服务启动前需检查这些条件；若要对照公开调研，应明确记录允许域及 64 MiB 下载配置。实验方案固定官方 DeepSeek endpoint，关闭 `DEEPSEEK_BASE_URL` 间接覆盖，只保留 `DEEPSEEK_API_KEY` 环境变量名称。模型单请求超时 360 秒，不能据此声称 API 具有评估脚本的 3600 秒总运行期限。

注册时同时解析主、子配置。每个请求通过已有 `request.runtime["idea_research_config"]` 接收独立的子配置副本，避免改变另一个请求。初次构建上下文前，在 run 的 `input/idea_runtime_profile.v1.json` 独占写入非秘密有效配置、启用工具列表、工具配置哈希、方案文件哈希及配置哈希；再次构建上下文或开始 draft 时核对相同内容。该文件是配置身份收据，不保存候选、调用数、token、工具结果或审查判定，不是第二份 checkpoint，也不授权恢复运行。

已有收据不得覆盖；不同方案、模型、预算、子审查或工具配置拒绝继续。实验方案不能接管没有原收据的历史执行或续跑；baseline 服务也不能继续带实验收据的运行。旧的无收据 baseline 路径保持原行为。收据缺失、损坏或中途截断明确失败，不自动补写身份。真正的恢复资格、输入/协议 fingerprint、未知工具或模型调用、剩余预算、收据来源和下游 handoff 仍按既有执行器与加载器校验；匹配配置收据本身不能证明这些条件满足，也不替代真实验证所记录的源码提交和原 trace。

接下来应通过真实 `POST /api/runs` 创建独立 Idea 任务，`auto_approve=false`、不提供 seed，实际经过 Bridge。审阅配置收据和模型 trace，确认主、子配置及逐项审查确实执行，再独立核查论文相关性、迁移主张、最终方案及双重输出。`waiting_review` 表示等待人工审阅，不能称为已批准、完整项目完成或已验证性能收益。
