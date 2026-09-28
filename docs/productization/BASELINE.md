# 产品化基线与共享合同审计

记录日期：2026-09-28。本文记录工作包 A 的起点与实际验证边界，不代表工作包 A 或正式产品已验收。

## 工作现场与来源

- 原工作树分支：`main`；冻结提交：`4d703f710fc8f1f5177577f9ac662978b690fb2d`。
- 检查时远端跟踪引用：`origin/main@136078bf9b0d9483e2da04ce5e3a3ce86fab64c7`。这是本地引用观测，不替代后续网络推送核验。
- 原树已有三个本地提交，依次为 `ebeef7d`（集成 TensorBoard 监控）、`9b2f727`（Idea 重试修复）、`4d703f7`（Windows 原生 CPU 配置和启动）。这些提交不是本轮新增工作。
- 原树已有未提交修改：`backend/app/cli.py`、`backend/app/harness/llm/openai_provider.py`、`backend/tests/unit/test_openai_provider.py`、`configs/agents.yaml`、`configs/idea_focused.yaml`、`frontend/next-env.d.ts`。原始差异统计为 6 个文件、64 行新增、39 行删除；需要单独审阅，不能默认为本轮实现或覆盖。
- 原树还包含产品化规划、draw.io 图及图目录、本地工具配置、`local/` 和绘图脚本等未跟踪内容。保留原地；不把个人工具配置、运行资料或凭据复制到发行包。
- 实施位于独立的 `mars-productization` 托管工作树，基于上述冻结提交。首次检查时该树仅额外有复制的 `docs/PRODUCTIZATION_PLAN.md`。本记录中的“基线”指提交源代码，不包括原树未提交修改。
- 基线检查借用原树 Python 环境，但显式设置隔离树 `PYTHONPATH` 和 `MYPYPATH`；已核验 `app.settings` 从隔离树导入。使用 `env -i` 清空继承环境，不读取原树 `.env`，隔离树不存在 `.env`/`.env.local`。

环境：Darwin 25.5.0 / arm64、Python 3.13.9、pytest 9.1.1、mypy 2.3.1。仓库和 CI 的目标为 Python 3.11；本地环境验证不能替代 Python 3.11 CI 或其他平台验收。

## 初始验证

命令在隔离工作树根目录执行。`PYTHONPATH` 和 `MYPYPATH` 均为本树的 `.:backend:posttrain/src:projects/synthetic_regression/src`，Python 工具来自上述现有环境。

| 检查 | 命令 | 基线结果 |
|---|---|---|
| 后端与合成项目测试 | `python -m pytest backend/tests projects/synthetic_regression/tests -q` | 2408 通过、183 跳过、2 失败；其中 1 项由检查环境 PATH 缺项造成，下述复核已通过 |
| 严格类型检查 | `python -m mypy --strict backend/ scripts/release projects/synthetic_regression/src` | 验证失败：575 个源文件中，2 个测试文件共 4 个错误 |
| 依赖方向 | `lint-imports` | 通过：334 个文件、1095 条依赖；4 条合同均保持 |
| 20 候选真实合成计算 | `python -m scripts.release.run_synthetic_smoke` | 验证失败：干净树没有 `runs/`，工作区解析在首个候选执行前失败 |
| 前端类型检查、生产构建、真实 UI | 由主执行者单独记录 | 不计入上述后端结果 |

类型检查原始问题：

1. `backend/tests/unit/test_external_review.py:37`：`state` 缺类型注解。
2. `backend/tests/unit/test_idea_efficiency_contracts.py:168,176,177`：`IdeaRequirements.performance_requirement` 传入 `dict[str, object]`，类型要求 `PerformanceRequirement | None`。

合成烟测原始错误为 `code_workspace_resolution_failed: trusted runs root is unavailable`。`PersistedCodeWorkspaceResolver` 要求可信的已有运行根目录；脚本在干净树中直接构造带 `candidate_id` 的请求，但未初始化该根目录。这是实际启动前置缺项，不能通过移除解析器、虚构候选结果或删除测试绕过。修复后的检查必须单独记录，保留初始失败。

完整测试的两个失败分别为：

- `test_model_registry.py::test_all_enabled_agents_use_the_deepseek_research_profile`：启用的 `idea_author` / `idea_reviewer` 使用 `deepseek-flash`，与测试要求的 v4 profile 不符。统一 GLM 的工作包 C 需要一并迁移配置与契约测试；不能删除断言隐藏角色漏改。
- `test_tools_hardening.py::test_code_tools_follow_repo_link_local_path_end_to_end`：本次清理环境时 PATH 只保留系统目录，真实 `code.lint` 无法找到 `python`。将借用的 Python 环境 `bin` 加入 PATH 后原测试原样通过（1 项，1.23 秒）；这项复核未修改工具实现。

183 个跳过项主要依赖未提供的真实历史回执、外部网络/模型、gitleaks 或 Docker CLI。跳过不是正例验收，不可将整套检查称为全通过。

### 本轮基线修复及复核

- 为 review 测试状态增加准确的 `dict[str, Any]` 注解；通过 `IdeaRequirements.model_validate()` 校验实际字典输入，保留所有约束与反例。
- 合成烟测通过真实 `RunStore` 初始化受信根目录，生成实际 run 身份，将每个真实 adapter 响应与最终汇总原子保存到该 run 的 `execution/`。保留 workspace resolver 与 source-layout adapter，不绕过安全检查。
- 新增集成回归，从不存在的临时运行目录执行所有 20 个合成候选，核验 20 个真实响应、metric envelope、运行身份和持久化汇总；不使用模型/工具/服务替身。

| 修复后检查 | 实际结果 |
|---|---|
| `python -m pytest backend/tests/unit/test_external_review.py backend/tests/unit/test_idea_efficiency_contracts.py backend/tests/integration/test_release_synthetic_smoke.py -q` | 21 项通过 |
| `python -m mypy --strict backend/ scripts/release projects/synthetic_regression/src` | 通过：579 个源文件；包含同工作树并行新增的发布测试 |
| `python -m scripts.release.run_synthetic_smoke` | 通过：20 候选、20 唯一候选 ID、20 唯一 envelope；实际回执保存在本地运行目录 |
| PATH 纠正后的原 code-tools 端到端测试 | 原样通过；真实临时 Git 仓库、补丁写入与 Python 编译 |

后续新增或变更后的全量回归另行记录；这里保留最初基线失败，不把局部修复结果扩张为完整产品验收。

### 通用合同增量的集成复核

新增 `test_research_contract.py` 后，再次运行它与上述 3 个基线修复模块：**201 项通过，3.71 秒**。其中 180 项覆盖通用合同，21 项覆盖类型修复和真实合成烟测。包含真实 `app.main.create_app()` 注册路由与独立 `python -m app.cli project` 子进程：二者产生完全相同的冻结合同/hash；缺项预检退出码为 2；无效配置非零退出；重复导出保留原文件字节不变；没有启动研究。

这一时点完整严格 mypy 为 **584 个文件无错误**；依赖方向 **4 条合同均通过**。这些检查不替代并行其他修改合入后的最终全量回归。

### 模型默认值变更后的 Windows 配置回归

独立检查发现旧向导选择 DeepSeek 且留空模型名会保留新 GLM 名称，旧连通性检查还会强制关闭 GLM 所需 thinking。已将向导默认改为智谱 GLM-5.3，并为显式 DeepSeek 选择提供独立模型默认值；作者、评审、辩论参与者及 focused/CLI 的 thinking 覆盖同步遵守能力规则。真实连通性检查代码保留 GLM thinking，使用 YAML 配置的 2048 输出 token、45 秒、无自动重试上限。

`python -m pytest backend/tests/unit/test_windows_native_setup.py -o addopts='' -q`：**19 项通过，2.31 秒**。新增用例只检查真实配置的纯变换及 probe 参数；已有用例实际启动并关闭后端。此次检查未调用模型或提示输入凭据。完整严格 mypy 加上 `deploy/windows-native/configure_api.py`、`deploy/windows-native/test_api.py`：**588 个文件无错误**。这不是 Windows 实机安装或真实 GLM 调用验收。

随后修正本地 PIMC 路径接入：只有显式代码、数据、解释器均存在且解释器可执行才启用；缺数据时清除旧数据路径并保持禁用；任意无效输入均在写入前失败。该模块复跑为 **22 项通过，2.13 秒**；同一扩展严格 mypy 命令在最终检查时为 **589 个源文件无错误**。只核验路径和配置，不把文件存在当作数据格式、依赖或训练通过。

### 模型身份证据修复

独立审查发现旧 `Completion.model` 回填请求配置，导致 probe 的 `actual_response_models` 不能证明响应型号。OpenAI 兼容解析器现将请求型号与 SDK `response.model` / GLM SSE `chunk.model` 分开保存：公开回执只记录有长度和字符范围限制的型号标识，不保留私有 reasoning 字段。响应型号缺失、非法、流中不一致以及部分 chunk 缺失分别标记；绝不使用请求型号补全缺失证据。空内容或截断的结构化错误也保留已观察的响应型号。

Native trace 的 `model_request.model` 仍表示请求配置，`model_response` 新增独立的 `requested_model`、`response_models` 与完整性字段。probe 只从响应身份字段汇总 `actual_response_models`；如果工具闭环通过但响应身份不完整或与请求型号不符，保留 `transport_status=passed`，同时将验收 `status` 标为 `model_identity_unverified`。这只证明服务端所报告的模型标识，不证明模型权重或提供商内部路由。

`python -m pytest backend/tests/unit/test_openai_provider.py -q`：**27 项通过**。新增用例是手工输入真实 SDK 数据类型后的纯解析测试，包含型号变更、缺失、非法字段、混合 SSE 型号以及失败回执；没有用 provider 替身制造执行成功。对 provider、基础错误类型、native executor、解析测试和真实 probe 脚本运行 `mypy --strict`：**5 个文件无错误**。全量回归与身份修复后的真实 GLM 回执由主执行者另行记录；旧回执不可冒充修复后的验证。

以上测试没有发起外部 LLM 研究请求。测试中的合成 CPU 数值计算属于真实、有限步数的工程验证，不证明 PIMC 数据实验、真实文献研究或用户研究目标达成。依赖缺失导致的 skip 与通过必须分别统计。

### 历史任务只读恢复与显式迁移

发现 `_recover_session()` 在缺少状态快照时会根据产物猜测图，却仍把 `pipeline` / Agent entrypoint 当作可执行任务。现所有无快照恢复均为只读；图中的 `done` / `waiting_review` 仅供历史产物展示，不证明原任务真实完成。`GET /api/runs/{id}` 返回 `read_only=true`、`read_only_reason=missing_persisted_state` 和空操作列表；任务 `status` 保持 `null`。真实 schema 产物的版本列表、正文读取和已有文件下载仍可用。

启动、恢复、审批后推进、重试、反馈循环以及直接调用 orchestrator driver 均拒绝这类图。产物编辑/审批/评论和 patch 审批在写文件或执行补丁前拒绝；不能通过“先应用补丁、再拒绝恢复”绕过只读边界。

完整旧 JSON 状态走不同路径：显示 `legacy_state_migration_required` 与 `available_actions: ["migrate_state"]`。用户可显式调用 `POST /api/runs/{id}/migrate-state`；bridge 调用存储层的受锁校验/迁移，成功后丢弃旧缓存，但不启动研究，响应固定包含 `research_started=false`。原 JSON 原文保存在 `run_state.legacy.json`，authority 记录其 SHA-256；`run_state.json` 随后为 SQLite 状态的可再生投影。缺失状态、损坏/身份不符状态、其他服务拥有的任务及活跃 driver 锁均拒绝迁移。迁移不补造模型、预算或实验回执。

新增 12 项真实临时文件/API 回归，与既有 external projection、API lifecycle、owned cancellation 模块合跑：**58 项通过、1 项跳过，9.37 秒**。覆盖 6 种无状态历史场景、真实 HTTP 读/下载及 11 个拒绝入口、迁移前只读、真实 driver 锁竞争、显式迁移后的图/原文/哈希、幂等迁移和无自动任务。跳过项需要用户提供真实研究 checkpoint，未伪造替代。

随后增加显式 `POST /api/runs/{id}/replay-state-events`。只有具备有效 SQLite authority 的任务可请求补投已提交 outbox；响应列出已投递/待投递数量以及 `research_started=false`。缺少 authority 的纯产物/旧 JSON 历史直接 409，不创建锁文件。数据库丢失或损坏也明确 409，只返回安全错误类型。新增 6 项真实 API 回归，包括实际本地 EventBus 收到已提交事件、空 outbox、重复请求及异常状态；核验图、revision、状态投影均未推进。该模块现为 **18 项通过**，与 22 项真实 state journal 测试合跑为 **40 项通过，2.99 秒**。没有运行 Agent、模型或实验。

## 当前真实入口和持久化边界

| 范围 | 当前实现 | 产品化缺口 |
|---|---|---|
| UI 任务创建 | `api/runs.py` → `bridge/orchestrator.py` → `workflow_service.py` | 请求含项目名、松散 `project_inputs` 与角色选项；没有统一冻结的通用项目/任务合同 |
| CLI | `cli.py` → `bridge/cli_research_service.py`，通过 `cli_composition.py` 组合 Agent | 另有研究循环；parser 固定静态 PIMC、`.pth`、DeepSeek 型号与 RES/参数量目标 |
| CLI 实验 | `harness/research_trial.py`、`execution/research_process.py`、`configs/cli_research.yaml` | `channels=16`、RES dB、候选文件与静态数据协议仍在通用路径中 |
| 项目接入 | `harness/project_workspace.py`、`api/projects.py` | 可原地接入目录并保存 `.mars` 身份；缺少命令、数据/输出、指标、执行环境和预算的一体合同 |
| 项目扩展 | `harness/project_packs/`、`bridge/extension_runtime.py` | 已有 adapter 声明及真实进程协议，可复用；需与目录项目使用同一接入合同 |
| UI 运行状态 | `storage/run_state_store.py` + `harness/runtime/state_journal.py` | 新 RunGraph snapshot 与 agent_state outbox 由 SQLite 原子提交；旧 JSON 显式迁移、保留原件；预算/作业/批准回执仍未统一事务 |
| CLI 运行状态 | 独立 `state.json`、`input/manifest.json` 与 trial 文件 | 不是 UI 的 `run_state.json`，不能把“两者使用同一个 native tool loop”误称为同一研究状态机 |
| 任务生命周期 | Orchestrator 的 owned tasks、start/resume/stop/shutdown | 可复用已有归属与恢复保护；仍需完整暂停状态、全局预算与远程待核对语义 |
| 事件 | `Orchestrator._transition()` 先提交状态与 agent_state，再发送 outbox | 稳定 event_id 支持至少一次投递与重投；其他生命周期事件、预算和 artifact 引用仍未加入同一事务 |

动态依赖包括 YAML 配置、schema、prompts、Project Pack 文件、adapter argv、项目规则以及源码快照。静态 import 未引用某个文件不构成删除依据。

## 工作包 B 的最小完整实现建议

1. 在 agent-agnostic 的 harness 层定义严格、版本化的 `ProjectContract` 与 `ResearchTaskContract`。明确资料/代码/数据/输出路径、命令 argv/cwd、指标名称/单位/方向、基线、允许和保护范围、执行环境，以及不可为空的有限预算；拒绝未知字段、无穷/负数和非法路径。
2. 在 bridge 提供唯一的解析、项目扫描、预检和合同冻结服务。API 和 CLI 都调用它，并保留内容哈希、配置版本与聚合缺项；缺必要条件时在调用模型之前失败。只增加 CLI 可选参数不能达到这一要求。
3. 将 PIMC 的 channels/RES/数据协议和代码工厂移入明确的项目 adapter。以 PIMC 与 `synthetic_regression` 两个配置通过同一预检作为 B 的合同证据，真实跨领域研究闭环仍属于后续验收。
4. 将 CLI 执行迁入已有 Orchestrator 的任务归属与恢复机制，避免增加第三套循环。为旧状态保留只读兼容/显式迁移入口；不能删除历史成果或以新运行覆盖旧状态。
5. B 冻结 run/stage/attempt/job/结果标识和状态映射；D 以事务化存储使状态迁移、事件序号、预算预留与结算、作业映射和产物引用共同提交。现有文件锁和原子写可复用为 artifact/导出机制，但不足以证明跨文件事务。

迁移验收至少包括：同一输入经 CLI 与 API 得到相同合同/hash；非 Git 目录可接入；缺项一次性列出且无 API 消耗；越界路径被拒；旧任务仍可读；PIMC 与第二领域无需修改 core 即通过预检。仅合同测试通过，不得标记研究循环或发布已通过。
