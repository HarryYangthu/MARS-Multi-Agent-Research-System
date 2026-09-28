# B：run 内项目文件能力（2026-09-28）

本增量把已冻结合同的项目输入绑定到独立 run 源码快照与候选目录，不改变合同研究的 `execution_admission: blocked`。没有新增研究循环，没有调用模型或训练命令，没有把准备文件或工具修改当作科学实验通过。

## 接口与可信边界

`bridge.research_project_scope.prepare_project_scope(run, *, candidate_id, snapshot_policy, request_extra=None) -> ProjectScope` 重新核验持久化合同身份、当前声明基线和入口文件，复用 `create_snapshot` 与 `materialize_candidate_workspace`。路径落在该 run 的 `context/project_scope/` 下，原仓库不写入、不提交到 MARS，也不加入离线结果导出。

`harness.runtime.project_scope.bind_project_scope(scope)` 仅供可信 owner 在 Agent/工具运行外层绑定。`current_project_scope(project, run_id)` 核对项目与 run 身份；ContextVar 继承到所属异步子任务，并隔离并发兄弟任务。禁止从工具参数、`ToolContext.extra`、模型输出或反序列化收据授予 capability。保存的 `run_project_scope.v1` JSON 是证据，不是授权入口。

`verify_candidate_scope(scope) -> tuple[CandidateChange, ...]` 独立重验只读 snapshot 的完整 manifest、候选控制身份及完整文件集合，再逐文件比较 SHA-256 与可执行位。未授权或保护文件的新增、删除、内容/可执行位修改均拒绝，新增目录也检查范围。允许变化只返回实际路径、变更类型、前后哈希与可执行位。`prepare_project_scope` 已调用该审计；后续 owner 必须在最终候选边界再次调用，不能用某次工具成功或模型自报 files_changed 代替。它不是测试或指标验收。

宿主必须传入 `SnapshotPolicy`，其读取范围和上限独立于合同 `allowed_paths`（后者只管写入）。宿主选择完整代码读取策略时，快照包含基线、入口与其他受准入依赖；任何缺失的声明基线/入口/根 `AGENTS.md` 都阻断准备。实际依赖能否运行仍需真实命令验证，不能从文件完整性推导。快照保留既有硬禁止规则，另排除 `.env*`、环境/依赖目录、控制元数据、声明数据和代码内输出目录；不会通过忽略规则放行快照硬禁止文件。

## 已绑定的入口

- `project_workspace.project_root` 返回 run 内项目元数据；同名全局 folder registry 不参与绑定项目解析。
- `load_project_repo` 和全部 `code.*` 文件工具从 capability 解析候选目录。`project_repo_root` 的参数/extra 不能改道。读取包含基线及依赖；写入只允许合同子树，基线、保护路径和项目规则优先拒绝。保护匹配保守处理大小写与 Unicode NFC 等价形式，避免常见 macOS 文件系统上的别名绕过。
- Gate 5 保留在原 registry dispatch。绑定项目使用其合同保护范围，拒绝越权 diff；不会按 `project_id == pimc` 偷套 `forward(x, stream_label)`。工具自身也重验路径，人工批准不解除基线保护。
- 文本补丁只支持可完整解析的普通 unified diff。重命名、二进制补丁、符号链接模式、带歧义的路径和外部 `patch_path` 明确拒绝。实际 `git apply --no-index` 限定候选目录，避免父 Git 仓库或环境中的 `GIT_WORK_TREE` 改道。
- 回滚只读本 run 的工具回执，核对 run/project、内容指纹与每个写目标；全部目标验证后才开始恢复，保护文件尾项不能造成前项先写。符号链接、硬链接、目录冒充文件及路径穿越都拒绝。
- `BaseAgent` 复用现有项目知识冻结机制，从 run 内元数据读取规则与声明文本；元数据和既有知识快照需与绑定指纹一致。上下文不会加载同名全局 Agent 代码仓库或项目 memory。无声明知识时不会继承 PIMC 知识；缺领域参数仍需现有 Agent 规则明确阻断。

## 知识、数据及执行的未完成边界

声明知识目前只支持 UTF-8 `.md`/`.txt`，按宿主快照文件数/单文件/总字节上限受限读取并保存逐文件指纹。目录包含 PDF、其他未支持格式、符号链接或硬链接时明确失败，需先将用户选择的知识规范化，不会静默略过。根路径和每级相对目录都检查敏感/控制范围；例如 `.ssh/foo.md`、`.env-dir/foo.md` 不能因选择了上层知识目录而获得读取权。源码入场同样拒绝硬链接，防止数据目录里的内容通过别名混入候选。知识指纹是在 scope 准备时建立，不冒充最初合同冻结时已覆盖这些内容。

数据仅保存声明引用；收据明确 `access: not_granted`、`fingerprint: null`。私有数据不会复制进源码候选，也没有授予数据工具读取权。真实数据/协议版本、执行所需挂载、结果证据校验仍待受约束适配器完成。

`code.test_runner`、`code.lint` 与外部 OpenCode 写适配器在绑定合同下明确拒绝执行，避免使用全局命令配置绕过声明命令与预算。其他工具类别、项目 memory/skill 命名空间、远程环境、合同命令与指标绑定、预算 owner、恢复时 capability 重建及最终候选验收不由本增量完成。Bridge owner 尚需同时绑定项目和执行 scope，并在 dispatch 核对 run/project/stage；当前没有解除合同 blocked，也不提供绕过开关。

这是可信宿主内的文件访问约束，不是针对恶意同机进程的 OS sandbox；不声称抵抗外部进程在检查与访问之间竞争替换文件。已有候选允许授权编辑，但重新准备会拒绝改变的基线、项目上下文或不一致快照。恢复不能悄悄选择新的现场源码替代已冻结证据。

## 验证

`backend/tests/unit/test_run_project_scope.py` 使用真实冻结合同、RunStore、临时文件、实际 Git 子进程和实际 ExperimentAgent 上下文构建。覆盖两个同名项目的并发隔离、保留只读依赖、私有数据/凭据排除、保护优先级、Gate 5 实际 dispatch、普通补丁实际应用/回滚、危险补丁拒绝、符号/硬链接、上下文损坏、未支持知识与命令阻断。没有模型调用、工具替身或 monkeypatch 成功。

最终结果：新增文件 **59 项通过**；与文件夹项目、通用提示、公开上下文、上下文运行时、Focused Idea、Gate 5 和候选工作区回归合跑 **136 项通过，8.11 秒**。8 个变更 Python 文件 `mypy --strict` 通过；`PYTHONPATH=.:backend:posttrain/src:projects/synthetic_regression/src lint-imports` 的 4 条依赖方向合同通过；`git diff --check` 通过。范围不包含第二领域完整模型研究、真实训练或 Windows 本机验收。

父任务补充：请求或 SQLite 仍标记冻结合同时，即使合同文件丢失或变成断裂符号链接，也不能回退同名全局项目上下文。三项真实文件/SQLite拒绝回归已补；组合全量与最终定向检查见 `VERIFICATION.md` 和 `evidence/run-project-scope-checks.json`。
