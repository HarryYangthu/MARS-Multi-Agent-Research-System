# 结果中心与离线报告：当前证据边界

本增量落实 PRODUCTIZATION_PLAN 工作包 H 的通用只读结果投影、回执校验和离线报告包。它不重新调用模型、运行 Agent 或训练，也不把文件存在、退出码 0、schema 合规或流程完成当成科学目标达成。没有新增依赖。

## API 与所有权

| 接口 | 行为 |
| --- | --- |
| `GET /api/results/{run_id}` | 从同一 RunStore 读取 `run_results.v1` 投影，不恢复任务、不修复 approval 事务、不写状态 |
| `POST /api/results/{run_id}/exports` | 在该 run 的 `results/exports/<32 位十六进制 ID>/` 原子发布新的报告 ZIP 和清单；不覆盖既有导出 |
| `GET /api/results/{run_id}/exports/{export_id}/download` | 重新校验 ZIP 哈希、文件集合、逐项大小和 SHA-256，返回同一次校验的字节 |

API 必经 `bridge/results_service.py` 和 `bridge/results_export.py`。未找到任务返回 404；不安全、不可读或损坏的导出返回通用 409，不向客户端回显主机路径或底层异常。文件读取、作业/指标数量、曲线点数和导出大小受 `configs/results.yaml` 限制。错误、缺失与未知是数据状态，不返回成功样例填充。

`state.read_only=true` 指结果视图只读取已保存证据，不表示研究任务已经获得或失去执行权限。启动/恢复必须继续走 owner 的准入接口；合同 run 当前的执行阻塞不能由结果页绕过。`state.authority` 区分 SQLite、未迁移旧 JSON、缺失和损坏；声明 SQLite 后数据库缺失不会回退采用旧 JSON 的成功状态。

每个导出使用一次收集所得的投影生成全部文件，`results.json` 与报告/CSV/清单保持一致。这不是对整个正在运行目录的原子数据库快照；读取期间新产物可能尚未被纳入。每次作业证据链独立验证，变化或缺失导致该项不进入已验证测量。

## 证据与研究结论

目前核验现有 `local_command_request.v1`、`local_command_result.v1`、`local_command_receipt.v1` 的实际格式：

- run、project、experiment、invocation 的归属和请求/结果哈希一致；invocation 与所在目录一致。
- 回执状态为 completed 且真实进程返回码为 0；仍须具有有限数值的研究指标和测量文件。
- 请求声明的测量证据位于该次作业目录内，路径、大小和 SHA-256 一致；stdout、stderr、请求和回执元数据不能替代测量文件。
- 原始测量文件只读校验，不进入报告包。来源索引只有生成的 source ID、种类、原始字节哈希和大小，不暴露文件路径。

`receipt_verified` / `verified_local_receipt` 表示已保存回执的身份与内容一致性；它不是可信硬件签名，不防止持有整个 run 写权限的人重写全部证据，也不认证科学协议有效。其他旧领域/远端 `metrics.json` 记录只作 `unverified` 展示，不按文件名推断执行成功。

指标名、单位和方向不硬编码领域。单位、方向、目标和命令名来自已校验的冻结研究合同，标记为 `declared_contract`；不会重新预检主机源码，也不会把声明的目标、数据路径或执行环境当成实际测量条件。无合同则单位/方向保持未知。原始命令参数、私有路径和合同全文不进入默认导出。

角色仅取请求中明确声明的 baseline / candidate / ablation；其余为 unknown。报告包括种子、请求步数、耗时和配置摘要哈希，不推测样本数量。统计只聚合同一实验、指标、声明配置、步数和命令哈希的已核验作业；均值是描述性数值，标准差仅在至少两次且种子均已记录并不同的情况下显示。`independent_repeats=false` 始终保留，种子不同不证明数据划分或统计独立性。单次标准差为空。

曲线使用运行结果现有的 `loss_curve` 协议，逐点检查有限数值后程序生成 SVG。没有生成假曲线，也未把此字段扩展成已覆盖任意远端评估曲线的承诺。

事实、假设、解释分开。已批准 Idea 文档须通过原 schema 校验，其 hypothesis 只作假设；已批准 Writing 文档存在不证明其中结论。当前只有已提交的失败/阻塞/取消清理状态可产生对应 outcome；其余 outcome 保持 unknown。尚未实施自动目标判定、预算停止原因归一化和独立研究审查，保留的枚举不能视为这些能力已验收。

## 用量与成本

结果视图复用模型账本的 token 与 attempts 校验：

- `model_requests` 仅为兼容字段，等于 `logical_records`，`request_count_scope=logical_records`。一条逻辑请求可能有多次 SDK 尝试。
- `observed_sdk_attempts` 为所有记录都有观察计数时的合计，否则 null；是否完整还必须看 `observed_attempts_complete`。`calls_with_unknown_attempt_count` 指完整次数未知的逻辑记录数。
- `charged_sdk_attempts` 与 `reserved_sdk_attempts` 是额度账本计数，可能保守保留重试上限，不能当实际调用次数。
- 完整 token 用量下输入为 prompt，计费输出为 `total_tokens - prompt_tokens`，包括账本保留的推理/未解释 residual。例如 prompt=300、completion=100、total=450，计费输出是 150。
- 旧账本缺少分桶时，只能从完整有效 usage 恢复实际分桶，不能把保守 reservation 当消耗。任一记录用量未知，完整 token 和费用合计保持 null。
- 费用来自真实账本字段 `charged_cost`，仅当全部 usage 完整且费用存在时合计；`cost_scope=recorded_estimate_not_invoice`，不是供应商账单。缺失价格仍为未知。

只读报告不协调或释放预算预留，也不回写账本。

## 默认导出内容和安全边界

ZIP 包含 `report.html`、`report.md`、`results.json`、`metrics.csv`、`experiments.csv`、`statistics.csv`、程序生成的 `curves/*.svg`、`evidence/sources.json`、`reproduction.json` 和 `manifest.json`。清单记录生成文件的相对路径、字节数和 SHA-256；ZIP 有额外哈希校验。清单自身不自我哈希，外层 ZIP 哈希覆盖它。

HTML/Markdown 对输入转义，HTML 无脚本并设 CSP；链接与文件名完全由宿主生成。CSV 对可能触发电子表格公式的字符串加前缀保护，数值保留数值语义。JSON 投影阶段已过滤已知环境凭据、常见凭据赋值、私钥块、URL 和绝对主机路径，不能只依赖 HTML 转义。默认包不复制原始日志、上下文、源码、数据、完整合同或模型回答。敏感项目仍须审阅主动写入研究问题/假设/指标名称的自由文本；任意未标记秘密或私有事实不是可由正则证明全部识别的内容。

证据路径拒绝绝对外部引用、父目录穿越和符号链接；历史回执的绝对证据路径只接受当前原 run 内同一次作业。文件读取有大小上限且不跟随最终文件符号链接，下载同样限制压缩及展开总大小。并发恶意本机进程改写整个目录的对抗隔离不属于本次验收。

HTML 可由浏览器打印另存 PDF；没有新增 PDF 引擎，也未实现服务器自动生成 PDF。离线包始终标记 `reviewable_only` / `independent_rerun_verified=false`。授权代码、原始数据与划分、冻结配置、环境和命令参数须由项目持有人另行提供；本增量没有自动打包候选 patch、私有必要文件或完成另一环境的实验重跑。论文证据摘录、自动配对基线/消融差异与统计检验、通用远端回执/TensorBoard、可执行复现包仍须后续受约束实现，不能宣称完整 H/R19 已通过。

## 可重复验证

`backend/tests/unit/test_results_center.py::create_measured_run(root, repeats=2)` 在传入的隔离临时目录创建真实 CPU 验收数据：标准库线性回归实际计算 12 步，经过现有 `run_local_command` 写入请求、测量文件、结果与回执。运行图保持 created，测试不伪造模型、工具或服务成功，不将数学进程当成完整研究。

检查命令：

```bash
python -m pytest backend/tests/unit/test_results_center.py -q
python -m mypy --strict backend/app/bridge/results_service.py backend/app/bridge/results_export.py backend/app/api/results.py backend/tests/unit/test_results_center.py
```

本轮定向测试 40 项通过：实际 CPU 测量、只读 SQLite 与历史拒绝回退、审批事务不恢复、冻结合同离线读取、单次统计、请求/结果/测量文件变更或缺失、symlink 与读取上限、资源分桶/residual/旧账本/未知重试、JSON 脱敏、HTML 与 CSV 注入、提取至不同目录后的相对链接和哈希、外层校验与清单成员损坏、真实 API 路由。算术账本输入与文本/schema 安全输入是明确的测试数据，不是伪造 provider 返回或实验结果。真实浏览器、服务、离线打开和打印验收由整合任务另行留证。
