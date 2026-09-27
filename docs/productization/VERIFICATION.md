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
