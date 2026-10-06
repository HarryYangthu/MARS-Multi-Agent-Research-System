# ZCode 接入验收记录

验证日期：2026-10-06。本机 macOS，真实官方 ZCode，编码模型 GLM-5.3，使用 MARS 已配置凭据。所有模型、代码工具、Git 分支和测试均实际执行，无替身。

## 验收范围

- 实际后台协议握手和进程关闭。
- 编码 Agent → ZCode → MARS 模型预算网关 → 真实模型 → MARS MCP → ToolRegistry → Observation → 再次模型响应 → Schema 验收。
- 真实 Git 合成项目中修正加法函数，保留基线提交和受保护测试；只修改 `main.py`，实际测试返回 0。
- 产品 Orchestrator 写入编码产物、注册人工审核，编码节点处于 `waiting_review`，没有人工批准或启动实验。
- 在真实工具返回后中断，恢复原 ZCode 会话，再完成读写、测试和结构化提交；计数、活动用时与用量账本连续。
- 未授权 HTTP 请求被拒绝；超出允许路径的写入被真实工具拒绝；预算耗尽前不请求模型；停止后写入请求被拒绝。
- 设置页加载本机安装状态、保存 ZCode 选择，并显示「ZCode · 当前」及保存成功消息。模型连接保持编码 Agent 原配置。

## 原始证据

| 验证 | 证据位置 |
| --- | --- |
| 产品编排到人工审核 | `runs/2026-10-06T081415283061_zcode_acceptance/product_runs/2026-10-06T0814_zcode_bridge_acceptance/zcode_acceptance.json` |
| 同一会话中断与恢复 | `runs/2026-10-06T081416459994_zcode_acceptance/zcode_acceptance.json` |
| 设置页截图 | `runs/zcode-ui-verification/settings.jpg` |

每个验收 JSON 关联真实工具输出、Trace、Schema 状态、基线保护和 `resources/model_budget.v1.json`。运行数据保留本地，不提交用户 API 凭据或研究源代码。

最终产品编排验证使用 4 次模型请求和 4 次工具调用，活动用时 21.91 秒、累计 32,653 tokens；中断恢复验证使用 6 次模型请求和 5 次工具调用，累计活动用时 29.69 秒、累计 40,306 tokens。小任务结果不能推算复杂项目的速度或成本。

## 回归检查

针对 ZCode、Git 任务分支、原生工具协议、Provider、进程生命周期、停止和恢复的 88 项检查中，86 项通过，2 项因 Linux `/proc` 条件在 macOS 跳过。新增及关联的 13 个 Python 源文件通过严格类型检查，前端 TypeScript 检查通过。

导入边界检查中，新增 ZCode 代码通过 Harness 上层依赖禁令、Agent 依赖禁令和分层检查。完整仓库仍有一项既有问题：`app.bridge.literature_evidence` 直接依赖 `app.agents.idea.focused_runtime` 与 `app.agents.idea.literature_quality`。本次没有将该问题列为已修复。

新增依赖 `aiohttp` 用于仅监听本机回环地址的临时模型/MCP 网关。

## 结论边界

已验证的是本机 ZCode 编码链路及审核衔接。未宣称真实 StaticPIMC 实验取得 2 dB 改善，未自动批准用户当前任务，也未执行 GPU 实验；跨系统安装和操作系统隔离仍需各自验证。
