# H：合同 SQL 用量的只读结果展示

`ResultReader.resources()` 现在先调用 `bridge/research_results_usage.py`。只有明确不存在合同标记时才使用原 legacy JSON 账本。run metadata、request options、冻结合同、SQLite request 或预算扩展任一位置仍标记合同，缺失/损坏的 SQL、未初始化的预算扩展、冻结身份不一致都返回不可核验，不能用旧 JSON 补成有效用量，也不能填零。

读取过程使用一个 SQLite `BEGIN` 快照并启用 `query_only`。现有 ResearchBudgetLedger 验证冻结政策、全部 reservation/settlement/hash、活动区间与资源租约。读取不初始化、不迁移、不结算、不关闭活动区间、不推进时钟 high-watermark，不改运行状态。有效 SQL 不依赖可再生的 `run_state.json` 投影。

## 展示含义

- `model_requests` 保持旧界面的逻辑记录数，等于 `logical_records`，`request_count_scope=logical_records`。真正 SDK 尝试另用 `observed_sdk_attempts`，不能把逻辑调用当实际请求数。
- `charged_sdk_attempts`、`charged_input_tokens`、`charged_billed_output_tokens` 和 `charged_quantities` 来自 SQL 的保守计费量，包括 reserved/unknown/retained 及已观察下界。它们不是模型实际消耗。`reserved_sdk_attempts` 是各记录最初预留次数总和，不是“当前仍在执行”次数。
- 实际 SDK/token 数据只接受 SQL settlement 指定的原生模型回执：安全路径、run/task/reservation/operation 身份、canonical 回执 hash、返回模型/provider 身份、attempts_complete 及对应用量字段全部一致。完整 token 还必须 `usage_complete=True`，数值与 settlement 相等。
- 一条模型记录缺失完整依据，实际 token 全量合计就是 `None`；已核验小计单独列出，不能冒充全量。unknown 的独立 JSON 回执即使声称完整，也不能替代缺少 SQL evidence hash 的结算。
- 若 JSON 回执丢失或损坏，但 SQL 自身完整，保守扣减仍可展示；该记录不再支持实际消耗结论。SQL 自身损坏则用量字段全部未知。
- 费用始终 `None`。预算里的金额上限、没有核验的价格引用或预留不能生成实际费用；也不表示发票金额。
- 活动已用与剩余额度来自同一 ledger snapshot，明天重新打开时仍使用原活动区间。未知操作、开放活动及时钟异常继续使用保守额度，读取不会替用户执行恢复或退款。这里没有宣称所有暂停/恢复策略已验收。

前端结果页把“用量记录”与“合同预算记账”分开，后者显示工具扣减、进入实现的候选扣减、活动时长和未知预留。进入实现不代表实验完成或目标达成。离线报告继续包含同一公开资源摘要，不导出数据库、模型输入/输出或端点凭据。sources 中 `derived_contract_resource_summary` 明确是脱敏派生摘要的 hash，不是原始 SQL 文件 hash。

## 验证边界

新增 `backend/tests/unit/test_research_results_usage.py` 的 24 项真实文件/SQLite 检查，与原结果中心 40 项用例联合 **64 passed**。覆盖未初始化合同、坏数据库、缺表/列、SQL 标记禁止 legacy 回退、过时 JSON 投影、回执缺失/篡改/软硬链接、实际与预留数值分离、文件 bytes/mtime 在读取前后完全不变。

其中完整 usage 的数字是明确标注的 **authored parser/accounting 输入**，没有运行模型、构造 provider 替身或宣称 API 成功。另有真实 SDK 对本机已 bind 但未 listen TCP 端口的连接拒绝：产生实际失败回执、保留 SQL 预留，结果页实际 token/费用仍未知。这不算真实成功模型或完整研究验收。

新模块、ResultReader 接缝与新测试文件的 `mypy --strict` 已通过。前端 `tsc --noEmit --incremental false` 已通过；本次未覆盖另一个任务正在使用的 `.next` 构建，也未宣称新展示的浏览器验收完成。最终生产构建和真实 UI 验证由共享集成批次进行。

随后父任务联合原结果检查与诊断脚本纯数值检查，共 **76 项通过**。另一次实际 GLM → 注册文件工具 → 六个真实 CPU 作业 → 结果导出已核对：SQL 与结果资源摘要均记录 4 次实际 SDK 尝试、5395 输入及 1117 计费输出 token，费用未知；不存在活动作业或未知预留。详见 [组合诊断及解释更正](CONTRACT_CPU_DIAGNOSTIC.md)。独立只读复核验证 152 个证据文件未被读取过程修改；这与 authored accounting 输入检查分别记录，不能互相冒充真实模型验收。

## 生产页面与离线阅读验收（2026-09-28）

共享生产构建 `PbeDgd66I0IkpDFeGZhzf` 已通过真实浏览器验收。后端临时使用原 `contract-cpu-real-a/runs`，仅在其父目录补齐明确清单内的运行资源；没有复制或迁移 run/SQLite，也没有模型凭据、新模型调用或训练提交。结果页显示 **4 次已记录 SDK、5395 输入 token、1117 计费输出 token、3 次工具扣减、费用未知、6/6 作业回执已核验**，并保留“目标尚不能判定”与独立复现未通过的说明。已实际查看截图，未发现页面 JavaScript 错误；网络记录保留了不影响本页功能的 `/favicon.ico` 404。

通过当前 Next 生产服务的同源 rewrite 获取原有导出 ZIP，返回 HTTP 200、14,741 字节、SHA-256 `3e59e7b03f76478f3eb7586641a65e228eb667a39fd66d79d7cbd813bd494b29`；8 个清单内容文件的大小与哈希全部复验一致。本轮保持只读，没有点击会产生新导出的 POST，也没有把这一 GET 检查描述为 Electron gateway 下载验收。

解压后的原 `offline/report.html` 使用 `file://` 打开，并在浏览器禁用网络后重新加载成功；实际点击相对 `results.json` 链接可打开记录，8 个相对文件路径均有对应且哈希一致的文件。工具曾真实出现 `open` 返回标题、下次 snapshot 却变为 `about:blank`：原因是后续调用未继续带 `--allow-file-access`；在同会话每次保留该选项后恢复正常。保留该失败经过，不将第一次导航返回值算作成功。离线可审阅不等于独立环境复跑通过。

文件不变性的两个证据集必须区分：前述独立磁盘审计验证的是 **152 个证据文件 bytes 和 mtime 均不变**；本次生产 UI/API 加载验证的是 **原 run 内 125 个文件的 bytes 均不变、无新增或删除、SQLite 不变**，只有已有空 `.state-artifacts.lock` 的 mtime 被触碰。浏览器结束后关闭本次专用会话，并恢复原 8012 测试 runtime；3012 未重建或重启。

公开回执见 [contract-results-ui.json](evidence/contract-results-ui.json)。本地截图、网络记录、下载 ZIP 与校验材料保存在 ignored `release-evidence/productization/20260928/contract-results-ui/`；它们不进入用户发行包。
