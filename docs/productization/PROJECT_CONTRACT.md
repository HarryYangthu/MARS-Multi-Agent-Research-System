# 通用项目与有限任务合同

工作包 B 的首个增量提供严格解析、真实本地文件预检和可比较的任务指纹。它不创建新的研究循环，也不表示旧 UI/CLI 执行循环已经合并。全局预算的预留/结算/停止属于 D；远程环境预检与执行属于 F。

## 共享调用接口

`backend/app/bridge/research_contract_service.py` 是唯一服务入口：

- `load_project_contract(path)` / `parse_project_contract(value)`：YAML 文件和 JSON 请求使用同一个 `research_project.v1` 模型。
- `default_research_budget()`：读取 `configs/research_defaults.yaml` 中的全部有限预算。默认值是产品政策，不代表费用已经准确计量或硬限制已经在运行时执行。
- `preflight_project(project)`：只读检查路径、命令解释器、声明的源文件、修改范围和输出位置；一次返回所有文件缺项。不会执行用户命令，不调用模型或网络。
- `freeze_research_task(project, goal=..., mode=..., budget=...)`：重新预检并生成不可变 `research_task.v1` 与 `task_sha256`。预算必须完整显式提供；没有自动补齐缺字段、null 或无限预算的路径。

API 适配器为 `backend/app/api/research_contracts.py`，已由 `app.main.create_app()` 注册。路由提供 `GET /api/research-contracts/defaults`、`POST /api/research-contracts/preflight` 和 `POST /api/research-contracts/prepare`。prepare 请求为 `{project, goal, mode, budget}`；响应为 `{task, task_sha256}`。

CLI 使用相同 bridge 服务：

```sh
mars project defaults
mars project preflight --config /absolute/project-contract.yaml
mars project freeze --config /absolute/project-contract.yaml --goal "比较声明的候选与基线" --mode manual --output /absolute/new-task.json
```

freeze 可通过 `--budget /absolute/budget.yaml` 提供完整预算；省略时显式读取默认配置后冻结。输出文件必须不存在；重复导出不会覆盖。返回 `research_started: false`，合同准备与实际研究启动分开。缺项预检返回退出码 2，格式无效的配置非零退出。已有 `research` 命令仍是旧领域循环，未宣称已迁入通用执行器。

任务模式为 `manual` 或 `bounded_auto`。合同中的模式声明不是执行授权检查的替代品。预算保存全部模型请求、工具执行、研究活动时间、token、费用、训练进程时间和 GPU 时间上限；每操作重试与重复错误上限不超过 2。所有数值必须有限，时间/token/模型请求等必需上限必须为正；自动迭代次数、操作重试次数、GPU 张数可用 0 明确禁用。计数拒绝布尔值及数值字符串。单次/子集额度不得超过总额。

## 项目文件示例

以下是通用配置结构，路径、命令与指标由接入者填写。示例不是已运行的项目，也不携带凭据。

```yaml
schema_id: research_project.v1
project_id: example_regression
display_name: 示例回归项目
paths:
  code: /absolute/project
  knowledge: [/absolute/references]
  data: [/absolute/data/samples.json]
  output: /absolute/results
commands:
  - name: check
    purpose: check
    executable: /absolute/environment/bin/python
    arguments: [-m, compileall, src]
    cwd: .
    entrypoint_files: [src/model.py]
  - name: train
    purpose: train
    executable: /absolute/environment/bin/python
    arguments: [train.py, --config, configs/baseline.yaml]
    cwd: .
    entrypoint_files: [train.py, configs/baseline.yaml]
  - name: evaluate
    purpose: evaluate
    executable: /absolute/environment/bin/python
    arguments: [evaluate.py]
    cwd: .
    entrypoint_files: [evaluate.py]
metrics:
  - name: MSE
    unit: unitless
    direction: minimize
    target: 0.01
    tolerance: 0.001
baseline_files: [configs/baseline.yaml]
allowed_paths: [src]
protected_paths: [src/reference]
execution:
  kind: local
  device: cpu
```

绝对路径用于明确用户选择的外部资源，无需搬动原目录；非 Git 目录同样可以预检。`knowledge` 和 `data` 可显式声明空列表，例如代码生成合成样本的项目；这不等于存在真实训练数据。相对代码范围为字面文件/目录，不接受 `..`、盘符、反斜杠或 glob。其子树匹配有路径分隔符边界。所有 `baseline_files` 自动保护，`protected_paths` 始终优先于 `allowed_paths`。入口文件与范围不能经过符号链接；输出路径需使用不含符号链接的规范路径，且不得覆盖代码根目录或与保护范围重叠。

`source_write_allowed(project, relative_path)` 提供同一保护规则的确定性查询，但尚未接入实际写入工具和最终 diff 校验。命令是 executable 与 arguments 的结构化数组；预检只核对声明文件与可执行文件存在，不证明参数正确、依赖完整或命令已在隔离环境运行。此处没有 shell、环境安装或虚构执行成功。

## 指纹及证据边界

项目指纹基于完整规范 JSON（排序键、固定分隔符、UTF-8），不受 YAML 键顺序或 JSON 传输格式影响。任务指纹还包括目标、模式、完整预算，以及声明基线/入口文件的 SHA-256。后续修改预算、项目配置或这些源文件会生成不同任务指纹；已经返回的合同保持原值。

该指纹没有扫描全部源代码、数据内容或外部知识文件，不能充当执行快照。运行前仍需建立完整授权代码快照、数据/协议指纹，并核对合同没有过期。没有在此增量中执行迁移、持久化状态或作业；旧任务保持原有读取路径。

SSH 配置只接受已保存连接的 `connection_ref`，没有密码或私钥内容字段。任何 SSH 或 GPU 项目预检都会明确返回尚未完成的环境验证，禁止把本地文件检查当作远程连接、CUDA 或真实训练通过。

`ResearchIdentity` 定义 run/stage/attempt/job 标识；`ResearchResultStatus` 分开表示流程状态与研究目标结果。这些为后续存储迁移提供合同，不把 completed 自动等同于 goal_met。

当前回归使用真实临时目录、真实文件哈希、真实 ASGI 路由，以及标为 PIMC/回归的两种用户配置输入。二者通过同一解析和预检，证明没有领域专用字段要求；不证明真实 PIMC 数据/第二领域研究闭环通过。测试也覆盖全部预算非法值、路径穿越、符号链接、保护优先级、缺项汇总和 API/服务合同指纹一致。

实际验证：`test_research_contract.py` 的 180 项通过；与基线修复回归合跑为 201 项、3.71 秒；完整严格 mypy 在该时点检查 584 个源文件通过，4 条 import 合同通过。API/CLI 一致性使用真实应用路由与独立 CLI 子进程，验证相同合同、缺项退出和已存在输出保护。测试期间未调用 LLM、未执行声明的训练脚本，未使用成功替身。
