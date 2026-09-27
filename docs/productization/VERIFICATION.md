# 产品化增量验证

2026-09-28，本机 macOS Apple Silicon、Python 3.13.9。结果对应 [源码指纹与检查回执](evidence/increment-checks.json)，不是最终发布候选验收。GitHub CI 的 Python 3.11、Windows 实机与安装包另行验证。

| 检查 | 实际结果 |
|---|---|
| `python -m pytest backend/tests projects/synthetic_regression/tests -o addopts= -ra -q` | 2678 通过，178 跳过，0 失败，约 117 秒 |
| `python -m mypy --strict backend/ scripts/release projects/synthetic_regression/src scripts/verify_glm_native.py deploy/windows-native/configure_api.py deploy/windows-native/test_api.py` | 590 个源文件通过 |
| `lint-imports` | 339 文件、1107 依赖，4 条合同保持 |
| `python -m scripts.release.run_synthetic_smoke` | 20 个真实 CPU 合成候选、20 个唯一候选及指标回执通过 |
| 前端 `npm ci`、`npm run typecheck`、`npm run build` | 通过；8 项既有 lint 警告；生产构建 `CBgrP4a2MxiZhv7EnkQLh` |
| 桌面 `npm test` 与实际 Electron smoke g | 14 项通过；真实页面、API、WebSocket、鉴权和退出通过，详见桌面证据索引 |
| 真实 GLM native probe b | 2 个请求、2 个 SDK 尝试、1 个文件工具与 observation、schema/nonce 校验通过；1896 token |

测试 PATH 使用已有本机 Python 工具和仓外官方 gitleaks 8.30.1，源码路径显式设为本工作树。178 个 skip 主要依赖未提供的真实历史运行、外部服务或平台条件；不能计为正例通过。

第一次集成回归出现 7 个失败：四项测试切换 JSON protocol 时仍继承 native-only 开关；旧 Commander 失败测试复用了仓库账本；真实 TensorBoard 子进程跨测试未清理导致事件循环不匹配；一项委派配置断言仍要求关闭 thinking。修复测试输入、隔离真实临时账本并关闭本测试拥有的进程后，定向复查 242 通过/2 跳过，再执行上述完整回归。未删除失败测试、未放松协议或伪造模型/工具成功。

## 真实模型证据

[probe b](evidence/glm-native-probe.json) 使用实际注册的 `code.repo_reader`，读取随机生成的真实文件，再由实际 GLM 提交带 nonce 的结构化产物。请求和服务端 SDK 返回型号均为 `glm-5.3`，没有格式修复；原始 trace、工具 observation、账本与产物保存在本工作树 `runs/glm-native-productization-20260928-b/`（不入 Git）。公开回执保存源码、输入和产物哈希。秘密只从进程环境提供；推理内容不保存。

[先前尝试 a](evidence/glm-native-prior-attempt.json) 保留为传输检查，彼时未独立记录 SDK 响应型号，不计入型号认证。b 的型号仅表示供应商所报告的型号，不证明服务器内部模型权重。

费用缺少核验价格表，因此显示未知。此检查未执行正式 Idea Reflection、论文阅读/独立审查、PIMC 仿真、两领域研究或性能验收。合成 smoke 是真实数值工程检查，未调用模型，也不是文档要求的 20 次研究发布矩阵。

## 发行结论

未达到发布要求。精确白名单与动态资源测试通过，但待迁移领域内容仍阻止公开发行；没有生成发行归档。历史秘密扫描的七条命中均已逐项证明是摘要、空变量或协议/幂等标识，只按精确不可变提交指纹确认误报，再扫描通过；新命中仍阻断，Git 历史未改写。详见 [扫描审查](evidence/secret-scan-review.json)。桌面仍是源码生产构建原型，缺少内置运行时、安装升级、签名和完整用户旅程。后续变更须依据受影响范围重验，最终 R01–R27 必须使用一致候选构建。

## 状态事务、历史保护和通用资源增量

在上述首批增量之后，增加本地 SQLite RunGraph 权威状态、与状态同事务提交的 agent_state outbox、显式历史迁移和缺失状态只读保护。下游四阶段的默认提示与产物模板移除领域指标和预填成功结果，保留项目知识的显式装载；这些改动没有新增研究循环，也没有把完整任务账本宣称为同一事务。

中间回归为 2756 通过、178 跳过、2 失败：两项旧存储测试使用空图，与新的可恢复状态约束不符。将其输入改为真实单节点图并保留原审批恢复/CAS 断言后，完整回归为 2774 通过、178 跳过、0 失败，约 119 秒；596 个 Python 源文件 strict mypy 通过，4 条依赖合同通过。随后补充 token 总量类型校验，相关 30 项通过。独立审查发现并真实复现持久化失败阻断取消、历史 self-evolution 写入口遗漏、迁移接受其他项目产物和符号链接等问题，均已修复；修复中的 2788 通过/178 跳过回归仍保留，没有替换为最终结果。

最终状态增量回归：**2798 通过、178 跳过、0 失败，119.78 秒**；**597 个源文件 strict mypy 通过，4 条依赖合同通过**。[最终源码指纹与日志哈希](evidence/state-increment-checks.json) 固定本批对应关系。并行开发且尚未提交的 CLI HTTP client 两个文件明确排除在本批收据和测试之外，单独验收；其余本批现有测试全部执行。缺少 checkpoint、外部依赖或平台条件的 178 个跳过仍不计为正例通过。

[真实 probe c](evidence/glm-native-split-budget-probe.json) 使用实际 GLM 与注册文件工具：2 个请求、2 次 SDK 尝试、1 次工具调用，完整 usage 为输入 1653、计费输出 253、合计 1906 token。schema 与随机 nonce 校验通过。该回执按实际源码哈希固定，后续损坏记录校验未追溯伪装成已重跑的模型调用；费用仍未知，也不计为正式研究或实验验收。

已推送的 `1ddbbf2` 与 `a0fd864` 均通过 GitHub Core compatibility CI 及 Windows native CPU launcher 工作流。Windows CPU launcher 检查不等于 Windows 11 干净安装或 GPU 研究验收。完整日志与此前失败尝试在本机 `release-evidence/productization/20260928/` 保留，公开证据只含来源指纹和汇总。

状态增量提交 `e0114c32ef140a868bd90c7fd360f9e303b01aa9` 随后也通过上述两条 GitHub 工作流。完整源树 dry-run 的秘密扫描通过，领域内容/历史规则仍阻断，没有生成发行包。

## 共享 CLI 控制增量

`mars run --server <origin>` 经真实 HTTP 控制现有后端的任务，CLI 退出后后端仍持有 driver；没有另建调度循环。`mars project --server` 使用同一预检/冻结接口，默认与显式预算的整个合同及哈希都与直接 API 结果一致。旧 StaticPIMC 命令保留历史兼容并明确标记 legacy，完整通用研究接入尚未合并。

相关回归最终 **292 通过、7 跳过，约 14 秒**；7 个跳过均需要未提供的外部 StaticPIMC checkout。**599 个 Python 源文件 strict mypy 通过，4 条依赖合同通过，14 项桌面资源/生命周期测试通过**。来源、日志哈希和边界见 [CLI 增量回执](evidence/cli-increment-checks.json)。前端源码未变化，沿用前一批生产构建与实际桌面烟测证据。

其中 62 项真实 HTTP/子进程测试使用独立 uvicorn 运行未替换的 MARS 应用，验证同一任务 list/show/start/HITL/stop、重复启动、真实 401/404/409、只读历史、合同准备和已有文件不覆盖。人手编写的 schema 合法提案进入真实审核，没有替身模型或伪造研究完成。成功恢复中断模型需要实际 checkpoint，本次只测真实拒绝路径。独立审查先重跑 54 项客户端测试，再重跑新增 8 项 CLI 子进程测试，均通过。

保留的失败与修复包括：IPv4-mapped IPv6 的跨 Python 判断差异、macOS 未监听端口可能真实超时、`1e309` 被解析为非有限数，以及 CLI 本地 Pydantic 诊断回显误贴的 session token。最后一项先真实失败，再改为不打印原始异常/输入；argparse 的坏参数也不再回显。回归同时检查完整凭据和前 12 字符不进入 stdout/stderr。网络响应受总读取时限和字节上限约束，不宣称可抢占同步 JSON 解析。

该增量不证明通用合同预算已经接入所有执行工具、不证明自动研究或成功模型恢复，也未实现桌面到独立 CLI 的自动凭据交接。

## 合同保存、SSH 连接、结果中心与前端增量

冻结合同现在可从 UI/CLI 保存为真实任务，并由同一后端管理。保存前重新核对合同哈希与声明源文件；23 项预算及 run-bound 项目能力尚未全部接入，任务明确只读/阻塞，不调用模型。损坏状态下仍可查询真实 owner 并停止其工作；不能用损坏数据库抹掉实际工作。

完整后端回归 **2972 通过、178 跳过、0 失败，148.286 秒**；**605 个源文件 strict mypy、4 条依赖合同通过**。之后修正浏览器发现的时间线假启动，单独 5 项投影/真实临时任务测试和 strict mypy 通过；前端随后重新生产构建。完整回归未包含正在独立开发的本地 durable runner 或 packaged runtime。

新依赖 AsyncSSH 用于现成密码/密钥协议与严格主机校验。真实本机 sshd 检查含密钥、加密密钥、拒绝认证、错误指纹/known_hosts、撤销密钥及 SFTP。独立审查真实复现符号链接下载/目标覆盖问题后已修正，原失败和复验均保留。没有密码成功和 Linux GPU 验收。

结果检查使用真实 Python CPU 线性回归的两个作业回执，各 12 步；MSE 分别约 0.56498023、0.66286825。这里没有模型研究、基线改善或独立复现结论。浏览器显示两条真实曲线、未知目标与费用；下载离线包并逐一校验 10 个清单文件的 SHA-256/字节数。断网下直接打开 HTML，图表与相对链接自包含。浏览器打印 PDF 是打印路径证据，不代表产品已实现自动 PDF 导出。

前端 YAML 依赖用于从 configs/frontend.yaml 编译有限请求时限/轮询/文件大小策略。创建与启动之间、离页/切项目时检查取消，不把客户端取消当作服务器操作撤销，不自动重试不确定 mutation。目录默认显示全部任务，未注册项目的合同仍可找到。readonly 时间线不再由任务存在推导“已启动”。独立审查发现的三项问题（离页后启动、极值曲线坐标溢出、未注册合同不可达）均已修复。

实际暂停本测试拥有的 uvicorn 进程，页面立即显示加载、15.54 秒后出现中文超时且按钮解除 busy；SIGCONT 后刷新恢复。没有替换网络响应或模拟服务成功。运行目录中的早期 results.yaml 仍含已删除字段，首次刷新真实返回 409；仅更新此隔离测试目录的配置后重验，不能当作升级迁移已经通过。

G/F/H 仍为进行中，未用这些局部证据替代两领域研究、五名用户、完整故障矩阵、独立环境复跑或安装发布。

## 独立 CPU 作业与 CI 可移植性修复

新增独立本地 CPU supervisor，后端被真实 SIGKILL 后作业仍受自身 deadline 约束；结果中心可读取其同协议回执。独立审查发现并修复元数据冒充测量证据、失败回执被改 status 后冒充完成两项问题。最终定向回归 97 项通过、6 个文件 strict mypy 通过；详见 [LOCAL_DURABLE_RUNNER.md](LOCAL_DURABLE_RUNNER.md)。未接通合同预算或自动研究，不承诺 supervisor 自身被杀后仍能停止其子进程。

提交 `42849d9` 的 Windows native CPU、前端生产构建与桌面边界 CI 通过，Linux 后端 CI 的 14 项 SSH 正例因认证失败而失败。真实本机用 `/private/tmp` 父目录复现，sshd 明确记录 `bad ownership or modes`；将临时密钥移到用户目录下自动清理的私有目录，保留 StrictModes、主机密钥与认证校验后，35 项 SSH 测试全部通过。Linux CI 的下一次结果另行核验，不能用本机修复提前声称通过。

该提交源树 dry-run 精确选择 567 文件，秘密扫描通过，634 条内容/历史门禁记录仍阻止归档。实际 macOS 测试包首次启动另外暴露 `desktop_session.py` 未纳精确源码清单，现已补入；测试包启动验收由后续构建独立记录。

## 自包含 macOS arm64 本地测试应用增量（2026-09-28）

内置独立 CPython 3.11.15、Electron 44.4.5/Node 24.21.0、锁定依赖和生产前端。实际 `.app` 搬到带空格的新路径后通过 GUI/API/WS、鉴权拒绝、退出清理与无关进程存活检查；独立服务进程在五个开发根目录拒读条件下仍能启动并返回真实页面。父任务独立重验 35,682 项文件清单、119 个 Mach-O 静态加载闭包及逐库/根应用 ad-hoc 签名通过。17 项 Node 测试、34 项真实文件/clang 与发行清单测试、4 个文件 strict mypy 通过。一次父任务测试命令写错不存在的文件名（exit 4），修正后才取得上述 34 项结果，原日志保留。

所有构建/启动失败及修复详见 `MACOS_TEST_BUNDLE.md` 与 `evidence/macos-self-contained-local-test.json`。测试应用使用其冻结的源快照，未混入随后开发的预算/项目 scope；不能宣称它等于后续源码 HEAD。整个 GUI 外套 sandbox-exec 未通过 macOS 嵌套 sandbox 限制，保持 Electron 自身 sandbox，不以关闭隔离换通过。尚无 Developer ID、公证、正式安装升级、Windows 自包含包或完整研究验收。

此前 `981ac04` 的 Core compatibility CI 五个作业与 Windows native CPU launcher 均已通过；Linux SSH StrictModes 临时目录问题已在真实 Linux CI 复验通过。

## 冻结预算 SQLite 与模型边界增量（2026-09-28）

预算扩展 v2 与 run_state/outbox 共用事务权威；23 项预算均有持久字段与准入检查，真实模型边界已接入，合同研究整体仍阻断。146 项定向测试、10 文件 strict mypy 和 4 条依赖合同通过，详情见 `CONTRACT_MODEL_ACCOUNTING.md`、`RESEARCH_BUDGET_LEDGER.md` 与 `evidence/contract-budget-checks.json`。

最终 v2 的真实 GLM-5.3 诊断为 1 次请求/1 次 SDK attempt，34 输入、27 计费输出 token，重复请求发送前拒绝；缺报价故费用未知。早期 v1 的真实诊断独立保留。审查发现并修复部分 usage 下界被预留掩盖、自定义 endpoint query 凭据落盘、失败响应模型身份遗漏；修复包括真实连接拒绝与纯计账回归，未伪造模型/服务成功。精确源指纹和阶段边界随证据保存。
