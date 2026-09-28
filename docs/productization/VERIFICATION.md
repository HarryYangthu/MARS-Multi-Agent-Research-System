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

## run 独立项目文件能力与组合回归（2026-09-28）

真实合同准备会保留基线/入口/依赖的只读快照，在本 run 的候选目录修改；Gate 5、代码文件工具和上下文均绑定同一项目能力，原仓库不修改。新增独立全目录 diff 审计、数据排除、知识范围与符号/硬链接拒绝。独立审查实际复现了源文件硬链接混入私有数据、知识硬链接绕路径范围，修复后真实复现均拒绝；详情见 `RUN_PROJECT_SCOPE.md`。

预算与项目能力组合全量：**3,148 passed、178 skipped，161.472 秒**；617 个 Python 文件 strict mypy 通过。跳过项仍要求外部模型/工具/来源环境，不能计为成功。随后补合同文件丢失但请求/SQLite仍有标记时的上下文拒绝检查，以及已有 provider 型号拒绝时的 usage 下界保留，最终相关 142 项及 10 文件 strict mypy 通过。完整日志摘要和源指纹见 `evidence/run-project-scope-checks.json`。

合同 owner 尚未接通全部执行能力；文件能力及完整 diff 通过不能替代命令、训练、指标或科学结论验证。

## 文件工具与合同 CPU 作业事务接线（2026-09-28）

五个原生文件工具在 Gate 5/审批后、真实 handler 前预留全局工具次数；首次修改候选同时原子预留实现额度。实际文件 hash、Git apply/rollback 与 SQLite 结算互相核对，重复语义操作不会因随机调用标签再次执行，未知副作用保留额度。

合同 CPU 作业保存 command/config/environment/候选快照与 job 身份，SQLite 预算提交后才启动独立 supervisor。已完成作业按真实结束时刻结算活动时间；停止依据既有 SQL 所有权，源文件或外部冻结声明损坏不阻止停止所属进程。原始 runner 协议仍兼容没有新增绝对截止字段的历史回执。

父任务最终组合 **291 项通过、13 个文件 strict mypy 通过**。作业负责人定向 225 项、工具负责人定向 100 项与独立审查 4 项为各自范围，不能把重叠用例相加成新的覆盖率。真实 SIGKILL、跨进程并发预留、时钟回退、SQLite trigger、截止与停止均有实际执行；未使用成功替身。独立审查先复现实验标签绕重复指纹、冻结输入丢失阻断停止两个问题，修复后复验通过。证据见 `evidence/contract-tools-jobs-checks.json`。

该作业切片仅接受可信本地 CPU、当前 Python、冻结声明的单一脚本、无外部数据路径；没有 OS 沙箱或 GPU 验收。全局合同 start 仍阻断。新增真实 GLM/数值实验组合诊断脚本的纯数值与语法拒绝检查已通过，真实 API 组合运行另行记录，不能由这些用例推导已经执行。

## 四步通用研究向导（2026-09-28）

默认 `/runs/new` 采用目标、项目路径、命令/指标、预算/预检四步表单，旧领域表单归入高级入口。23 项默认预算来自实际后端 YAML，语义字段修改会废弃旧预检/冻结。生产构建、TypeScript、定向 ESLint 与读取真实 YAML 的表单检查均通过。

真实浏览器以 1280×720、1440×900 验证缺项汇总与键盘聚焦、真实文件缺失、断网、预算改动、冻结和保存。暂停本测试持有的 Next 进程，实际请求在 15 秒后超时；恢复进程后后端仍可完成此前受理的保存。界面保持“待核对”，刷新后也不自动重发；这不是后端持久幂等或安全恢复已完成的证明。

四次独立显式测试请求生成四条实际待执行记录，均为 `created`、五阶段 pending、只读、无可执行动作，无模型/工具/作业回执，真实入口脚本哨兵未执行。最终构建明确“已保存 · 尚未启动”。父任务重新核对全部记录中的源码、截图与网络证据哈希，并查看最终页面。详见 `RESEARCH_WIZARD.md` 与 `evidence/research-wizard-ui-review.json`。完整配置跨次复用、五名用户、高分屏、磁盘不足/SSH 断连与整体研究流程仍待完成。

## 合同结果用量与真实组合诊断（2026-09-28）

结果服务从同一 SQLite 只读快照读取合同账本，将保守扣减与可核验的实际 SDK/token 分开。新结果模块 24 项真实文件/SQL 用例、原结果模块 40 项及诊断纯数值检查 12 项联合 **76 项通过**；读取不补建数据库、不推进计时、不结算或退款，证据丢失时不从 legacy JSON 补造用量。

真实 GLM-5.3 原生循环读取并修改隔离候选，随后完成六个 CPU 作业和一次基于测量结果的模型解释。共享 SQL 与结果页摘要一致：4 次实际 SDK 尝试、5395 输入 token、1117 计费输出 token、3 次工具执行、1 个进入实现的候选，费用未知。独立只读检查逐行重算预测/MSE、核对八个 ZIP payload 哈希，原始源目录保持不变。公开指纹与完整边界见 [诊断报告](CONTRACT_CPU_DIAGNOSTIC.md) 和 [回执](evidence/contract-cpu-real.json)。

原始模型解释误把噪声标准差 0.04 称为方差；实际方差为 0.0016。原始输出保留并单列更正，不能把模型解释当已核验科学结论。该合成诊断直接调用受约束的主机接口，完整产品 start 仍阻断；没有论文研究、独立环境复跑或发布矩阵通过结论。

## 持久创建身份与保存恢复（2026-09-28）

同一 runtime 中的创建请求号绑定冻结合同和规范化名称，先持久提交意图，再分配真实 run。并发相同请求及后端重启只核对原 run；创建前明确预检拒绝才允许用户修改后换新请求号，分配后的未知结果不自动重建。catalog 仅记录创建关系，任务状态与预算仍以各 run 的 SQLite 为权威。

真实浏览器在暂停 Next 的情况下发出保存请求，15 秒后超时；恢复 Next 后后端完成保存，再真实重启后端。页面刷新只发一次 GET，核对原请求号、合同哈希及真实回执后显示原任务。整个 trace 为 **1 POST、1 GET、0 start**，同名测试任务恰一条。实际源文件变化获得 rejected 且零新增 run；404 与历史标记保持未知且不重发。父任务核对全部源文件、截图及网络证据哈希，并查看最终页面。[UI 回执](evidence/research-save-reconciliation-ui.json) 固定实际生产构建与当时源指纹。

全量后端与第二领域测试 **3273 通过、181 跳过，182.95 秒**，严格类型检查 **628 个源文件通过**，**4 条依赖合同通过**。新保存模块包含真实 HTTP 并发、四个 SIGKILL 边界、实际 ReadTimeout 及重启；跳过项仍不计成功。之后独立审查实证初始化锁硬链接可截断外部测试文件，补普通单链接文件检查后新模块 **28 项通过**，strict 2 文件通过；未放宽主机/会话鉴权。精确日志和最终范围见 [集成回执](evidence/contract-results-recovery-checks.json)。

原目录分配与绑定 callback 之间仍可能留下未绑定孤儿目录，查询明确 unknown；当前没有自动认领或重建。浏览器 sessionStorage 标记只覆盖当前标签页会话。无请求号的兼容客户端仍明确非幂等；这些边界与完整研究的暂停、恢复和执行幂等分别验收。

## 已封存项目文件能力恢复（2026-09-28）

新增主机内部 SQL 封存与只读恢复函数，原源码移走时仍核验已保存快照、候选、metadata 与冻结权限；不重建源码，不清预算，不授予执行权限。26 项新检查与既有项目 scope 联合 **88 项通过**，两个文件 strict mypy 通过。独立实测发现同字节 snapshot/manifest 硬链接被旧 snapshot 验证接受的问题，补单链接检查后两个原复现均拒绝；实际 Git 失败的 unknown 预留和 SQLite INSERT 失败的零封存行也已独立复验。来源、完整范围及日志见 [恢复文档](RESEARCH_SCOPE_RECOVERY.md)。该切片不等于阶段或完整研究恢复已完成。

## CLI 保存核对与损坏日志拒绝（2026-09-28）

CLI 明确保留请求号和冻结哈希，保存超时后仅通过显式 GET 核对原任务；pending/unknown/rejected 均非零退出，只有身份一致的 created 才确认保存。原 CLI 模块 79 项、创建服务模块 38 项通过；各自 strict 3/2 文件通过。独立真实 CLI/uvicorn 三项检查通过，日志指纹见 [回执](evidence/cli-creation-reconciliation.json)。

独立检查先实际发现删除 state_events 后仍误报 created；修复后只读检查核心 SQL 表与列结构，原复现返回 unknown，不初始化、修复或重新分配任务。缺数据库、无效数据库、无请求号重复保存和真实 422 凭据脱敏也已独立检查。该增量确认保存关系，不证明完整研究的执行、暂停或恢复。

## 阶段调用与逐项作业控制（2026-09-28）

阶段 TaskEnvelope 与同一 SQL 的图节点、冻结输入、审批证据和配置指纹绑定，重复/恢复沿用同一 invocation。新增 59 项，相关 97 项通过；父任务实际删除 outbox 的复现推动共享 scope 恢复补核心 schema 拒绝。作业控制新增 15 项、相关 100 项通过；真实双 worker 验证坏 submission、非法目录行和忙锁不阻止其余合法作业停止。两组 strict 分别 3/4 文件通过，精确日志见 [回执](evidence/research-owner-primitives.json)。

查询阶段绑定不等于允许重放，批量停止的 run_stop_confirmed 始终为 false。新接口未替换主编排器，没有用局部终态推断整 run 停止；完整研究 start、执行暂停/恢复和发布门槛仍未验收。

## 只读能力目录（2026-09-28）

新目录模块 13 项、相关注册/技能联合 25 项通过（4.39 秒）；strict 3 文件、4 条依赖边界通过。真实 HTTP 会话验证、配置漂移、MCP 注册闭包及未执行的脚本 marker、技能文件损坏均已检查。首次父检查命令误用不存在的测试文件（exit 4/no tests），修正后才取得 25 项结果；保留日志见 [回执](evidence/capability-catalog-checks.json)。所有依赖仍 unknown，所有认证仍 not_run；这些检查不代表真实工具链认证。

## 配置复用与能力设置页集成（2026-09-28）

同一生产构建分别完成真实项目设置复用和只读能力目录验收。设置包含4命令（空/换行/首尾空白参数）、23预算、新目标与新任务哈希；加载只GET，重新预检/冻结后显式保存，任务仍created。真实SQLite读锁期间字段和导航disabled、409后可重试读取。WAL读取旁文件问题经独立复现后改为明确拒绝。能力页检查44工具、2技能、筛选/高级详情、真实断网恢复和390px布局，认证仍未完成。

全量后端与第二领域 **3434 passed、181 skipped / 198.08s**，strict mypy **638文件**、依赖边界**4/4**通过。父任务重新核对本批源hash和构建ID，并查看两页面最终截图；[集成回执](evidence/catalog-settings-integration.json)与两项UI回执记录证据边界。该轮不解除完整研究start，不证明安装升级或发布验收。
