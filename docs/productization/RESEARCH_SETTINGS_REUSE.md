# 已保存研究设置的完整复用

本切片允许从已保存研究读取项目声明、23 项预算和模式，填入新建研究向导。它不创建第二套项目状态 catalog，不复制研究代码，不修改来源 run，也不继承来源任务的目标、输入指纹、预检通过状态或执行权限。

## 数据来源与只读接口

- `GET /api/research-templates?cursor=<run_id>&limit=<page_size>` 返回可核验设置的来源摘要、下一页 cursor、当前页不可核验记录数量。列表按 run ID 排序，分页扫描；同一项目的多次保存保持各自身份。
- `GET /api/research-templates/{run_id}` 返回 `research_settings.v1`：`source_run_id`、完整 `project`、`budget`、`mode`、`requires_preflight:true` 和 `research_started:false`。不返回旧的 goal、name、task hash 或 input fingerprints 供新建任务复用。
- bridge `research_templates.py` 校验本 run 的普通文件/路径、metadata、authority、SQLite 核心表结构与 identity、单一 SQL snapshot 中的 request 以及冻结合同绑定。读取使用 `mode=ro`、`query_only` 和一个只读事务；不调用 Orchestrator 恢复、JSON 投影修复、预算初始化、账本结算或模型/工具/作业。
- 缺失来源或无合同的 legacy run 返回 404；坏 authority、错绑定、符号/硬链接、不完整证据等返回 409；不会退回 `run_state.json`。原代码或数据路径已移动不影响读取保存声明，但重新预检必须检查当前真实路径。
- `configs/research_templates.yaml` 配置默认页长 20、最大页长 50、单记录 1 MiB 与 SQLite 读取等待 2 秒。响应和前端载入另受既有客户端 deadline/字节限制；过大配置明确拒绝，不截断成可用配置。

当前只支持完整 rollback-journal SQLite。SQLite 的 `mode=ro` 在 WAL 模式仍可能创建 `-wal`/`-shm` 文件，因此在连接前检查文件头，并拒绝 WAL 或已有 `-wal`/`-shm`/`-journal` sidecar。没有使用 `immutable=1` 忽略活动 WAL，也不对数据库做模式转换。此限制可能让正在持有 rollback journal 的 run 暂时不可复用。

## 向导行为与无损边界

新建研究首步可读取、选择并载入已保存设置。载入保留用户当前填写的新名称与新目标，只替换项目、预算与模式，并使本地 preflight/frozen 失效。载入不生成 request ID，不发送 POST，不清除已有 pending/unknown 保存标记；保存 pending/busy/created 时禁止复用。

读取过程中用原生 disabled fieldset 和步骤按钮锁住编辑，并在离页或外部保存状态变化时取消请求，避免迟到的旧响应覆盖新编辑。

普通表单只在 `project → draft → project` 完全相等时接收设置。命令名称不同于用途、同用途多个命令、空参数、参数内换行/首尾空白、无法由原生输入框保留的 CR/LF 等情况会转入完整项目 JSON 编辑器。该编辑器保留全部命令和参数数组、路径、指标、基线、允许/保护范围、执行位置；预算仍通过现有 23 字段编辑。JSON 结构未知字段或旧冻结任务封套不能作为项目声明静默丢弃。返回普通表单仍须无损检查。

高级 JSON 只提供结构解析，后端继续执行完整 schema、路径、保护范围、环境与真实文件预检。新任务依次经过显式预检、重新冻结和显式保存；旧输入 fingerprint 不用作新任务准入。共享 `CommandContract.arguments` 已在独立修复 `4ff32c4` 保留精确字符串，包括首尾空白。

## 验证与真实证据

`backend/tests/unit/test_research_templates.py` **18 passed / 1.56 s**，strict mypy 3 文件通过。覆盖真实保存/读取、所有 project/budget/mode 字段、来源移动、缺/坏 DB、outbox 丢表、identity/request/frozen/metadata/authority 损坏、链接、legacy、分页、字节限额、关闭及活动 WAL 拒绝，以及真实 API 重新预检/冻结后显式保存不同任务。纯前端转换脚本验证普通完整往返、任意命令与参数无损兜底、23 字段预算与拒绝错误结构；它不是执行结果证据。

最终 production build 为 `ff-frkrcV1rRSLgTrUJ47`，共享构建包含同时冻结的能力目录页面，本文件只验收设置复用。构建通过，保留原有 8 条 lint warning；TypeScript 检查通过。

真实 Chrome → production Next.js → FastAPI → SQLite 验收：

- 普通已保存配置填入表单，手写名称/目标保留。
- 真实冻结来源声明含 4 条命令、两条同用途命令、空参数、内嵌换行和首尾空白参数、2 个指标及保护范围；高级编辑器与来源 project 逐字段完全相等，23 预算全等。损失信息的普通表单转换被明确拒绝。
- 来源代码目录真实移动后，设置 GET 仍成功，真实 preflight 报目录不可访问、冻结不可用；恢复目录后可以重新预检/冻结。
- 真实 SQLite `BEGIN EXCLUSIVE` 无数据写入地占用读取：页面实际名称/目标/项目字段及全部步骤按钮为 disabled；接口实际 409 后表单恢复，随后显式重新载入成功。没有响应替身或网络成功拦截。
- 最终构建中再次完成 preflight 200、prepare 200、用户操作的 save 201；新 run `2026-09-28T0204_task` 为 created、各节点 pending、0 state events、未初始化研究预算、无模型 traces 或 job resources。新目标、完整项目、预算与模式匹配，任务哈希与来源不同。
- 加载仅 GET；最终浏览器会话仅在三个显式操作后产生 preflight/prepare/save POST，没有 start/resume。浏览器 JavaScript errors 为空。

来源 run 的 10 个文件字节全部不变，无增删；完整页面的既有全局 run loader 曾触碰空 `.state-artifacts.lock` 的 mtime，其余 mtime 不变。单独 settings GET 的全部文件字节与 mtime 均不变。这两类观察不混为“整个页面绝无文件时间变化”。

独立复核曾发现关闭 WAL 后 `mode=ro` 新建 sidecar 的 P2，修复后的原始探针明确拒绝、无新增/删除/修改文件；18 项测试和无损转换脚本也经独立复验。中间真实失败另外保留：临时 `/tmp` 输出路径经 macOS 符号链接被预检拒绝，改用 canonical 路径后通过；纯转换测试发现 textarea 的 CR 归一化风险，增加完整 JSON 兜底后通过。没有放松运行时校验。

公开摘要与源文件哈希见 [research-settings-ui.json](evidence/research-settings-ui.json)。原始截图、DOM、网络请求、真实锁定状态、构建日志、SQLite/文件核对存于 ignored `release-evidence/productization/20260928/research-settings-ui/`。本批验证通用设置复用与保存，不声称研究闭环、模型/工具/GPU 实验、SSH、Electron gateway 或安装包验收。
