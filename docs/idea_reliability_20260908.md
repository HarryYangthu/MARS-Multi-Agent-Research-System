# Idea 可靠性迭代与框架选择

本轮在前九次真实运行基础上继续优化。目标是自主取得相关论文正文，解释选文和取舍，形成定义完整的方案，并从同一已验收版本输出中文说明和下游结构化交接。下载组件通过、模型审查通过、独立质量复核和科学实验分别记录。

## 当前修改

- 正文获取使用可配置的连接、无数据等待、完整传输和总时间预算；保留文件大小上限。真实验证配置明确使用 64 MiB，不改变历史运行的 32 MiB 上限。
- 同一 arXiv 论文版本的链接别名共享下载尝试与完整缓存；版本不同不冒充同一内容。每次读取新页段仍生成独立收据。部分传输只记录字节和错误，不进入可引用正文。
- 研究子任务可以提交明确的材料缺口。宿主在已无取证机会且材料不足时停止，不靠报告格式修复填补证据。主 Agent 在委派预算用尽且合格来源不足时也明确停止。
- 正式研究报告增加独立 Reflection，核对实际可见正文、原文结论和迁移推断。新报告的审查状态绑定准确候选和原始 checkpoint；历史报告不被补发审查证明。
- 续跑使用新目录，逐文件核对源档案，保留原失败记录、成功收据、累计预算和未知用量。同源码、同配置且结果已协调的中断才可继续；不会自动重放结果未知的工具。

整合后全量后端及 synthetic regression 测试通过（19 项外部条件或指定真实档案测试显式跳过），462 个文件严格类型检查通过，4 条架构边界检查及 20 候选 synthetic smoke 通过。下载、停止和来源续跑的指定真实档案检查另行执行，证据见下文。这批修改尚需完整真实运行验收，不能仅因合同测试通过而称整个 Idea 可用。

## 已有真实组件证据

`runs/verification/source_fetch_20260908T153228Z/verification_summary.json` 记录两篇完整 PDF 的实际下载：40,863,938 字节与 5,314,178 字节。同版本链接别名再读新页均命中完整缓存、未再次联网。错误版本 404、32 MiB 超限、部分下载超时及尝试耗尽另有真实负向记录。正文没有注入新的 Idea 运行。

当前网络状态也比第九次运行改善，因此不能把下载成功完全归因于增加时限。完整 Agent 验收仍须从自己的真实搜索、阅读和审查记录形成结果。

## 验收顺序

1. 用实际失败来源验证下载、缓存、错误分类和有限重试；用真实旧档案检查停止条件与续跑拒绝条件。
2. 从干净提交启动当前二维 LUT 任务，由 Agent 自主选择至少两篇论文的方法页段。必须通过研究报告审查、方案审查、独立质量复核及双重交付一致性检查。
3. 在不同任务约束下继续真实验证，并分别记录正常交付与材料不足时正确停止的结果。小规模通过记录不被解释为对任意问题的成功保证。
4. 保留实际方案、原始模型输出、来源页段和审查意见。性能是否提升由后续实验回答，Idea 阶段不得提前声称。

## 是否现在迁移 LangChain / LangGraph

建议本轮保留现有执行器。MARS 已声明 LangGraph 依赖，但 Idea 实际经 `BaseAgent → NativeAgentLoop` 执行；Bridge 的 LangGraph facade 只编译占位流程并记录清单，没有配置持久化 checkpointer 或调用该图完成 Idea 循环。依赖或配置标签不能当作已有持久化执行。

LangChain 提供模型、工具、Agent 和结构化输出适配；LangGraph 提供更底层的有状态编排、持久化和人工介入，也可独立于 LangChain 使用。参见 [LangChain 概览](https://docs.langchain.com/oss/python/langchain/overview)、[LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview) 和 [结构化输出](https://docs.langchain.com/oss/python/langchain/structured-output)。

本轮主要问题是正文获取策略、资源身份与重试预算、材料不足的停止规则、论文理解和方法定义。这些业务与证据要求仍需在 MARS 内实现和验收。直接迁移会同时改变执行路径和故障处理，使修复效果更难归因，并需要重新验证 Gate、收据哈希、旧档案和累计用量。

当跨进程恢复、多分支调研及长时间人工审阅成为持续开发负担时，优先做一个可切换的 LangGraph 适配器，保留 NativeAgentLoop 基线，用相同任务比较。持久化状态应只有一个权威来源；工具执行继续经过 MARS 注册中心与 Gate。LangGraph 恢复依赖 checkpoint 边界，部分步骤可能重放，因此幂等处理、未知结果核对和持久化后端仍需设计。参见 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。

模型、检索策略、输出合同和执行框架应分别比较，以便保留可解释的基线。LangChain 仅在具体适配需求出现时引入，不作为当前质量闭环的前置条件。
