# 产品化实施状态

更新日期：2026-09-28。执行依据：[PRODUCTIZATION_PLAN.md](../PRODUCTIZATION_PLAN.md)。

发布结论：**未达到发布要求**。此分支交付经过验证的工程增量；安装包、完整研究闭环和发布矩阵尚未通过。

## 工作现场与交付

- 实施基线 `4d703f7`；原桌面 `main` 的六项已跟踪修改及未跟踪资料保持原状。私有补丁快照存于本机隔离工作树旁。
- 分支 `codex/productization-foundation` 使用独立托管工作树。已同步 [草稿 PR #25](https://github.com/HarryYangthu/MARS-Multi-Agent-Research-System/pull/25)。
- 原 `.env`、研究数据和运行历史未复制进源码或发行包；真实 GLM 检查仅通过本机进程环境注入凭据。

## 工作包

| 包 | 状态 | 已完成增量与剩余门槛 |
|---|---|---|
| A | 进行中 | 基线冻结、精确发行清单、macOS 生产页面桌面壳和进程生命周期已有证据；干净安装、完整清理和升级仍未完成 |
| B | 进行中 | 通用项目/任务合同、有限预算；CLI 可经 HTTP 调用同一后端预检/冻结，并控制网页的同一 owner 任务。旧 StaticPIMC CLI 标为 legacy；通用合同到完整执行的接入、领域 adapter 和桌面 CLI 身份交接仍待完成 |
| C | 进行中 | 全部默认角色及辩论参与者改用 GLM-5.3；原生只读文件工具闭环实测；完整能力库及逐项认证仍待完成 |
| D | 进行中 | SDK 重试、输入/计费输出分项有限；状态与 agent_state 事件原子保存、历史只读/显式迁移，迁移校验项目/运行身份与路径归属；持久化失败仍取消实际 owned task。预算/作业统一事务、活动时间与完整取消恢复仍待完成 |
| E | 进行中 | 下游默认提示和模板已去领域假设及伪成功示例，复用项目知识快照；真实研究到实验的统一闭环仍未完成 |
| F | 进行中 | AsyncSSH 密码/密钥连接层、严格主机校验与 SFTP 路径约束；真实本机 sshd 密钥/加密密钥/拒绝路径已测。密码成功、Linux GPU 和两种远端代码模式待验收 |
| G | 进行中 | 四项主导航、项目页、全部任务/结果目录、合同导入、真实 owner 停止、结果导出和超时/离页处理已实现；真实浏览器联调已执行。完整通用向导、暂停恢复和外部用户测试待完成 |
| H | 进行中 | 真实回执核验、通用指标/曲线/描述统计/未知费用；HTML/MD/JSON/CSV/SVG 离线 ZIP 已校验哈希。仅可审阅，独立复跑、patch/论文集成和产品 PDF 导出待完成 |
| I | 进行中 | macOS arm64 自包含 ad-hoc 测试应用已通过搬迁 GUI/真实服务、119 个原生文件逐库签名与开发目录拒读检查；正式安装、升级回滚、平台凭据存储、正式签名和最终清理未完成 |
| J | 未开始 | 未冻结发布候选，也未执行同一构建的 20 次发布矩阵 |

## 证据索引

- [VERIFICATION.md](VERIFICATION.md)：本批完整回归、真实 GLM 型号/工具回执及明确验收边界。

- [状态增量回执](evidence/state-increment-checks.json)：最终 2798 通过、178 跳过，597 个源文件 strict mypy 与 4 条依赖合同通过；不代表完整产品验收。
- [CLI 增量回执](evidence/cli-increment-checks.json)：相关 292 通过、7 跳过，599 个源文件 strict mypy、4 条依赖合同、14 项桌面测试通过；包含同一后端的实际终端子进程验收。

- [BASELINE.md](BASELINE.md)：初始失败、环境边界、修复及回归。Python 3.13.9 本机检查不替代 CI 的 Python 3.11 或 Windows 实机。
- [CLEANUP_INVENTORY.md](CLEANUP_INVENTORY.md)：精确清单及动态依赖；已删除未使用个人页面/图片，移除受跟踪的 TypeScript 缓存，清空默认个人执行路径。
- [DESKTOP_DECISION.md](DESKTOP_DECISION.md)：实际 Electron/Python/Next 生产页面验证；[全部烟测尝试](evidence/desktop-smoke-attempts.json)、[安全回归](evidence/desktop-security-checks.json)、[第二实例](evidence/desktop-second-instance.json)。保留失败，不拼接成发布批次。
- [STATE_JOURNAL.md](STATE_JOURNAL.md)：RunGraph/事件事务、旧任务只读和显式迁移。
- [TOKEN_BUDGET.md](TOKEN_BUDGET.md)：输入/计费输出上限及未知量保留。
- [AGENT_RESOURCE_MIGRATION.md](AGENT_RESOURCE_MIGRATION.md)：默认资源通用化与项目知识边界。
- [PROJECT_CONTRACT.md](PROJECT_CONTRACT.md)：API/CLI 预检、冻结与保护范围。返回 prepared 明确不代表研究已经启动。

桌面源码模式仍使用开发环境；新 macOS arm64 本地测试应用内置 CPython 3.11.15、Electron 44.4.5/Node 24.21.0 与 Next 生产服务。它通过搬迁启动与开发目录拒读下的真实服务检查，但没有 Developer ID、公证、正式安装/升级及完整研究验收。构建源快照和实际失败/重验见 [MACOS_TEST_BUNDLE.md](MACOS_TEST_BUNDLE.md) 与 [回执](evidence/macos-self-contained-local-test.json)。真实 GLM 检查只证明指定工具与协议链路，费用因缺少核验价格表显示未知；不证明正式 Idea Reflection、论文审查、PIMC 仿真或科学目标达成。

当前发行门禁仍被待迁移的领域内容阻断，没有生成可发布归档。Git 历史扫描的七条命中已逐项证明为摘要、空变量或协议/幂等标识；按精确不可变提交指纹确认误报后复扫通过，新命中仍阻断。Git 历史未改写。

状态增量 `e0114c3` 已推送且通过 GitHub Core compatibility CI 和 Windows native CPU launcher；后一项不能代替 Windows 11 干净安装验收。源树审计选择 549 个文件，秘密扫描通过，内容/历史领域规则仍阻断且历史结果达到扫描上限；详见清理清单。

## 外部验收资源

UI/CLI 已可保存同一冻结合同的真实待执行任务，并重新核对声明源文件指纹。23 项合同预算及项目能力尚未全部接入执行器，合同任务仍明确只读/阻塞，不能启动。独立本地 CPU runner 与 macOS 测试包已有各自独立检查；这些证据不能合并称为完整研究已验收。`981ac04` 的 GitHub Core compatibility CI 全部五项作业及 Windows native CPU launcher 已通过，包含 Linux SSH 测试修复后复验。

Windows 11 x64 干净实机、Linux GPU/SSH 两种认证环境、发布签名/公证资源、五名目标用户及授权研究输入尚未完成此次核验。对应门槛不豁免，其余开发继续。
