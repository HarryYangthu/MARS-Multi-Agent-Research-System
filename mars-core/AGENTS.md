# mars-core 开发约束

MARS 最小核心：Step1 提方案 + Step2 Evolve。以实现功能为导向，不堆代码。内核不含任何领域代码：代码、数据、评测、检查、领域说明都由项目文件夹提供（见 docs/项目接口.md）。

## 硬约束
1. 代码预算：`mars/` 下 Python 总行数上限 4,800 行（2026-10-25 用户确定；用 `wc -l mars/*.py` 统计，2026-11-04 通用化后为 4,397）。新增功能前先说明能否复用已有代码或成熟工具；超出前先删减。
2. 新模块必须对应一条验收用例（tests/ 中的单元测试或 live 检查），否则不加。
3. 只允许真实执行：禁止 mock 模型、伪造工具结果或训练结果；缺凭据/数据/服务时明确报错。单元测试只测纯函数和真实临时文件。
4. 所有指标由项目的评测命令与检查脚本计算，模型只负责提出、解释和决策；门槛判定写在代码里（按研究约定），不交给模型。
5. 本仓不放任何领域代码、数据、背景资料或单位内部地址：它们属于项目文件夹（`.mars/project.yaml`、`.mars/pack/`、`context/`）。研究代码只读引用，在项目 workspace/ 下的本地副本和候选分支中运行。
6. 编码 Agent（CodingAgent）只拿本机网关的临时令牌，不接触上游 API Key；权限由 PermissionPolicy 逐条审批，并在 macOS 沙箱中运行（只能写候选 worktree 与自身运行目录，网络只能连本机网关）。
7. 每个阶段的产物写入 runs/<run>/ 并可重入：已有产物直接复用，中断后 `mars resume` 继续。

## 目录
- mars/project.py 项目声明解析与执行绑定；mars/workspace.py 基线副本、候选分支、范围检查、按项目命令评测（本地/远程）
- mars/llm.py 角色化模型访问（LiteLLM）；mars/gateway.py 本机网关；mars/codingagent.py CodingAgent 驱动
- mars/knowledge.py 知识库检索；mars/research.py 文献调研
- mars/step1.py 提方案；mars/step2.py 进化；mars/graph.py 编排（LangGraph）；mars/report.py 报告；mars/cli.py 命令行
- prompts/ 通用提示词（领域内容由项目 prompts 注入）；configs/ 模型与本机配置示例；examples/toy 最小示例项目
