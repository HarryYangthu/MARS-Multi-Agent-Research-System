# 产品化清理清单

记录日期：2026-09-28。对应 `PRODUCTIZATION_PLAN.md` 工作包 A 与 R26。

本清单记录依赖证据与当前动作；不把发行选择通过当成产品、安装包或科研验收。操作在隔离工作树完成，原桌面工作树及用户研究目录未清理、未覆盖。恢复依据为既有 Git 提交、隔离分支提交和基线清单 `BASELINE.md`；不改写历史。

## 已实施的发行边界

`scripts/release/v30_tree_allowlist.txt` 改为逐文件的精确清单。清单与文件内容都从指定 Git 提交读取；禁止目录通配、负规则、重复条目和失效条目。Next.js 的 `[id]` 是字面路径，提取也使用 Git literal pathspec，避免字符类匹配误带文件。新加入的文件不会自动进入发行包。

无论是否误加到清单，凭据文件、用户状态根目录、依赖缓存、Python 缓存和 `*.tsbuildinfo` 都被拒绝。缺少真实 gitleaks 成功回执时，开发诊断开关也不能生成发行归档。未削弱现有内容、历史、秘密和二进制审计。

`scripts/release/runtime_assets.txt` 是精确清单的资源子集，供新应用数据目录初始化。资源来源是随应用提供的默认值；不是用户现有配置的复制源，也不授权升级覆盖用户修改。Python 模块和合成评估器包需要单独安装。

## 路径与迁移决策

| 路径 | 功能与依赖证据 | 动作与理由 | 替代入口／恢复依据 | 当前验证与剩余工作 |
|---|---|---|---|---|
| `backend/app/**/*.py` | API、Bridge、Agent、Harness、存储与执行代码；存在相互导入 | 保留，清单逐项列出；不按旧文件名删除 | 既有调用路径；Git 可恢复 | 精确选择验证通过；领域实现拆分仍待 B/C |
| `backend/app/harness/schema/schemas/*.json` | `schema/validator.py` 与 `agents/base.py` 依 schema ID 动态读取 | 保留全部已存在 schema | 原路径，进入运行资源清单 | 资源闭包测试核对存在及发行覆盖 |
| `backend/app/agents/*/{prompts,docs,examples,evals}/*`、`backend/app/bridge/{prompts,docs}/*` | `storage/agent_context_store.py` 遍历 prompts/examples/evals；docs 仍作为工作台资料保留 | 保留；Experiment/Coding/Execution/Writing 默认 prompt 与工作原则已通用化 | PIMC 领域知识保留项目内，经现有 knowledge_file 快照加载 | 四阶段真实消息隔离与快照测试通过；其他领域资源仍待审查，见 `AGENT_RESOURCE_MIGRATION.md` |
| `configs/*.yaml`、`configs/agent_contexts/*` | 配置加载器、模型注册、调度、预算、执行、Windows 启动 | 保留必要运行默认配置；迁移个人路径及领域假设 | 应用用户配置目录与通用项目合同 | `execution.yaml` 的开发者绝对路径已由主工作包清理；预算/模型仍需独立验证 |
| `configs/skills.yaml`、`configs/skills/*.md` | `harness/skills/registry.py` 按 `instructions` 动态加载 | 保留配置及指令文件 | 原 registry 与相对路径 | 测试读取真实 YAML 并核对每个指令均在资源清单 |
| `configs/evaluation_rubrics/*`、`configs/evaluation_suites/*` | artifact evaluator、默认 replay suite 与 mutation contract 调用 | 保留调用依赖；领域 suite 待迁移 | 通用 suite；不以删评测消除失败 | 已保留，当前部分领域标记阻塞公开发行 |
| `configs/evaluation/*`、`configs/evaluation_datasets/*` | 历次开发运行输入与示例，不是默认服务依赖 | 从发行排除；主仓归档候选 | 开发证据索引；先核对脚本与测试消费者 | 未删除，避免破坏回归资料 |
| `templates/artifacts/*.md` | `context/engine.py`、`compiler.py`、`api/templates.py` 按 schema 读取 | 保留；四个下游模板已移除 PIMC 默认值与预填成功记录 | 同一 schema 入口；明确占位与未执行语义 | 模板 schema 与实际消息测试通过；其余模板继续逐项审查 |
| `templates/code_rules/pimc_python.md` | 项目专属代码规则示例；当前运行读取项目 `AGENTS.md` | 从通用发行排除，源码保留 | 项目规则／后续项目模板 | 未改动受保护项目规则 |
| `frontend/src/*`、构建配置与 npm lock | Next.js 编译与当前全部 UI 路由 | 保留逐文件清单；旧入口先迁移调用方 | 唯一项目／任务入口，工作包 G | 精确选择已验证；UI 收敛未验收 |
| `frontend/scripts/clean-next-types.cjs` | `npm run typecheck` 与 Windows 安装直接调用 | 保留 | 同一命令 | 依赖闭包测试覆盖 |
| `frontend/scripts/context-workbench-smoke.ts` | `package.json` 的 `test:context` 命令引用 | 暂保留，避免包内命令指向缺失文件 | 后续分离开发 package scripts 后再排除 | 未把测试入口迁移完成前删除 |
| `frontend/src/app/personal/page.tsx`、`frontend/public/personal/*.png` | 全仓前端引用搜索显示图片只被该个人页面消费，无产品导航入口 | 已删除页面及 4 张图片，且从精确清单移除 | 删除前 Git 提交可恢复；产品首页维持既有入口 | 删除后 `rg` 无剩余 `/personal` 引用；新生产构建由桌面工作包核验 |
| `frontend/tsconfig.tsbuildinfo` | TypeScript 自动生成缓存；编译可重建 | 已从发行排除；主工作包移出 Git 跟踪，保留本地构建缓存 | `tsc` 可重建，Git 可恢复 | 显式清单加入该文件也会失败 |
| `MARS-Windows.cmd`、原生安装/配置/启动/停止 `.cmd` 与 `deploy/windows-native/*` 运行脚本 | `.cmd` → `Mars.ps1` → `Common.ps1`、Python helpers、lock/config | 保留兼容链，逐文件列出；不保留整个 deploy 树 | 新桌面壳成熟后给出迁移提示 | 直接依赖覆盖已验；Windows 实机安装未由本项验收 |
| `deploy/windows-native/configure_api.py` 的项目向导 | 可选输入研究路径时仍读取专属 `projects/pimc/repo_link.yaml` | 迁移；不能将个人 repo link 塞入通用包补洞 | 工作包 B 的通用项目接入 | API 配置脚本保留；专属项目导入分支不作为通用发行已支持功能 |
| `deploy/windows/*`、旧 Docker `.cmd`、Dockerfiles/compose/nginx | 独立容器部署路径，仍有部署回归测试 | 通用桌面源包排除，源码保留；等待产品入口收敛 | 桌面应用；Linux 远端 runner 单独验证 | 不删除仍有测试的部署实现 |
| `scripts/evaluators/agent_mutation_task.py` | `storage/self_evolution_store.py` 校验 evaluator 文件指纹 | 保留该脚本，不能按整个 scripts 清空 | 原受控评估路径 | 精确清单包含；不宣称模型评测通过 |
| 其他 `scripts/*`、`.github/workflows/*`、`backend/tests/*` | 开发、诊断与 CI；源码回归仍需要 | 发行排除、开发仓保留 | 开发仓与 CI；新版本显式审查新增运行脚本 | 未删除测试或放松测试要求 |
| `docs/validation/*`、图表、截图、阶段报告、设计文档 | 过程证据／设计资料 | 发行排除；之后逐消费者核对再归档 | 开发资料索引，不进安装包 | 整个 docs 不再自动导出；未做大批删除 |
| `projects/synthetic_regression/*` | public project pack 用 metadata 和独立真实合成 evaluator | 保留 metadata、adapter 源码、合成资源，排除 tests | 可检查的合成验收样例 | 不是私人训练数据，也不是伪造模型／实验结果 |
| `projects/pimc/*`、`workspace/*`、`runs/*`、`knowledge/*`、`local/*`、`.env*` | 本机项目接入、真实数据／历史／凭据 | 排除通用发行；用户目录不删除 | 用户本机配置与原有资料；凭据不入开发归档 | 路径门禁测试覆盖；真实研究与旧任务兼容仍需后续验证 |
| `posttrain/*` | 目前无 backend 运行 import；训练为后续边界 | 源包排除，源码保留 | 加载已训练模型的 backend 路径保留 | 未以清理名义新增训练流水线 |

## 实际验证

在 2026-09-28 删除个人页面、加入桌面运行文件、移除开发者执行路径后的工作源码快照中，真实临时 Git 仓提交了清单指定文件，再由 `git archive` 提取：542 个文件被选择、542 个文件落盘、共 5,688,686 字节。此临时提交只为核验归档行为，不是共享分支发布提交；后续源码变化须在最终共享提交重新审计。

该快照的内容扫描仅剩 146 条现有领域标记阻塞，无绝对用户路径或二进制文件命中。没有生成可发布归档。保留领域加载依赖并报告阻塞，不通过删除依赖或放松规则制造通过。

从 [gitleaks 官方 8.30.1 发行页](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) 下载 macOS ARM64 开发检查工具到仓外临时目录，并核对官方 SHA256 `b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5`。真实共享分支 `HEAD` 历史检查返回 4 条 `generic-api-key` 候选，涉及 `.env.example`、`docs/evaluation/idea_quality_20260908/api_verification_20260909.json`、`frontend/src/features/discovery/smoke.ts`。这些是待分类的历史扫描命中，不自动代表当前有效凭据；不在本文记录命中值，不改写历史或自动忽略命中，公开发行仍被阻塞。

实际执行命令（使用已有 Python 环境，不修改系统依赖）：

```sh
PYTHONPATH=backend:. python -m pytest backend/tests/unit/test_release_manifest.py backend/tests/e2e/discovery/test_release_export_security.py -q
PYTHONPATH=backend:. python -m mypy --strict scripts/release/export_v30.py backend/tests/unit/test_release_manifest.py backend/tests/e2e/discovery/test_release_export_security.py
```

结果：使用上述真实 gitleaks 后 28 项全部通过，无跳过；3 个文件 strict mypy 通过。测试使用真实临时文件、Git 提交和 tar 归档，验证脏工作树隔离、字面动态路由、未审查新增文件排除、动态资源保留、危险路径拒绝、失效／重复清单拒绝与不得覆盖旧归档；不使用模型、工具或服务成功替身。

## 退出条件与未完成事项

本项只完成精确选择与依赖清单基础。R26 仍未验收：需要通用化领域默认值，保持发行内容无个人路径，收敛旧产品入口，分类历史秘密扫描候选，补空项目模板和第三方许可清单，完成真实 GLM 工具闭环、旧任务读取与恢复、Windows/macOS 实机安装升级、最终签名安装包审计。任何资源移除必须同时更新调用方、两份清单和相关回归。

### 历史秘密扫描复核

随后按 Git 对象核查全部七条命中：原四条分别是可重算且与父提交一致的 README SHA256、两处同一幂等标识和空 API 环境变量；本轮三条是公开 WebSocket 握手 nonce 及两个与同提交源码字节匹配的 SHA256。详见 `evidence/secret-scan-review.json`。`.gitleaksignore` 只列精确 `commit:path:rule:line` 指纹，无路径/规则/值通配，无历史改写。真实 gitleaks 8.30.1 重扫 `HEAD` 返回 0；新命中仍阻断。先前原始扫描失败保留，不把误报当实际凭据泄漏。领域内容、资源收敛和正式包验收仍未完成。
