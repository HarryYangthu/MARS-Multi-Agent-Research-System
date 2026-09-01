# Windows 内网部署验收记录

更新：2026-08-31。

当前结论：部署代码和本地回归已收尾，但**尚未完成最新 x64 离线包交付与 Windows
实机验收**。不能把以下代码检查、测试替身或先前 ARM64 容器结果当成 Windows 已实测。

## 本轮变更

- 一键入口覆盖在线、离线、生产、离线生产、状态和停止；批处理保留失败退出码。
- CPU 是默认设备；GPU 为显式 SSH 远端选项，不作静默回退。
- 启动成功要求前后端响应且任务准入检查通过；缺模型、模式不一致、生产使用 mock
  等情况均明确失败。失败不清除已有容器、run 或配置。
- 容器专用 `repo_link` 使用 Linux 内部路径，不再读取开发机 Mac 路径；生产仓库、
  数据和描述文件均只读，baseline 保护字段与项目原配置保持一致。
- 离线包强制 SHA256、默认端口和 `linux/amd64` 检查，禁止联网拉取与构建；导出
  使用临时文件，同名成品需显式 `-Force` 才覆盖。
- 默认不注入空密钥，避免覆盖前端持久化值；构建上下文排除各层 `.env`、密钥
  volume、镜像包、虚拟环境和研究运行数据。
- 合成项目端到端测试使用产品实际注册的 Adapter，保留受控模块路径和子进程隔离，
  不再另造一个依赖环境变量或 editable 安装的旧入口。

## 本轮可复核结果

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| 后端单元测试 | 540 通过，1 跳过 | 跳过项为需显式启用的外网工具 smoke |
| Windows 部署专项 | 12 通过，包含在上述单元测试中 | 含 Compose 实际解析与覆盖合并；不连接 Docker daemon |
| 集成及端到端 | 51 项通过 | 含实际 CPU 子进程的 20 候选合成闭环；不代表私有 PIMC 或 GPU |
| 严格类型检查 | 387 个源文件通过 | `mypy --strict` |
| 架构依赖边界 | 4 条均通过 | Harness 依赖方向未放宽 |
| 前端 TypeScript | 通过 | 不等价于最新 Docker 前端镜像验收 |
| 运行期密钥 | 符号链接写入及全新 Settings 实例读取测试通过 | 测试使用假密钥；本轮未做容器重启后的密钥验收 |
| PowerShell 5.1 / 7 | 自测脚本与 Windows CI 双运行时任务已添加 | 本轮没有可用 PowerShell，未执行该 Windows CI，不能标为通过 |

后端本地复核命令（在仓库根目录执行）：

```sh
PYTHONPATH=backend:posttrain/src:projects/synthetic_regression/src:. \
  .venv/bin/python -B -m pytest -o addopts='' -q backend/tests/unit
PYTHONPATH=backend:posttrain/src:projects/synthetic_regression/src:. \
  .venv/bin/python -B -m pytest -o addopts='' -q backend/tests/integration backend/tests/e2e
PYTHONPATH=backend:posttrain/src:projects/synthetic_regression/src:. \
  MYPYPATH=backend:posttrain/src:projects/synthetic_regression/src:. \
  .venv/bin/mypy --strict backend/ scripts/release projects/synthetic_regression/src
PYTHONPATH=backend:posttrain/src:projects/synthetic_regression/src:. .venv/bin/lint-imports
```

Windows 脚本自测不需要 Docker 或网络，在已批准的 PowerShell 中执行：

```powershell
.\deploy\windows\tests\Test-DeploymentScripts.ps1
```

这个自测中的 Docker 响应全部是测试替身，只检查脚本控制流，不会启动容器、调用模型
或读写真实部署配置。

## 之前的容器记录（本轮未复验）

2026-08-30 的 ARM64 容器验证曾完成前后端及 Redis 健康检查、非 root/只读根文件
系统与 volume 检查，以及以下任务：

- 合成闭环：`2026-08-30T1352_windows_container_e2e_20260830`，3 个候选完成；
  重启后任务和选中候选仍在。
- StaticPIMC Alpha CPU：`2026-08-30T1356_windows_static_pimc_cpu_e2e`，1 个候选完成，
  保存了模型、指标和图。该 frozen fixture 仅证明工程链路，不是正式科研结论。
- amd64 前端镜像曾完成独立 HTTP 200 和容器健康检查。

这些记录早于本轮脚本、Compose 和构建上下文调整，不构成最新修订的完整 x64 放行
证据，也不构成 Windows 主机验收。

## 尚未通过的交付门槛

1. 当前会话无法访问 Docker socket，因此无法完成或复核最新后端 x64 构建、整栈
   启动及离线 `docker save/load`。当前尚无可移交的 `images/mars-windows-amd64.tar`
   和对应 `.sha256`。
2. 需要公司批准的 Windows x64 + Docker Desktop Linux containers 测试机，验证
   PowerShell 5.1 双击入口、真实离线启动、数据持久化及错误提示。
3. 内网模型网关与真实 SSH GPU 需要对应环境，仍未验收。GPU 不影响 CPU 演示包先行
   交付，但不能标成已支持该具体设备上的真实任务。

## Windows 实机验收顺序

1. 在获准联网的构建机执行 `deploy/windows/Export-MarsImages.ps1`；保留成功输出的
   x64 镜像 tar 与 SHA256。带入内网前按公司流程审查来源和文件内容。
2. 将两个干净发布目录及镜像包放在 README 指定位置，启动 Docker Desktop，双击
   `start-mars-windows-offline.cmd`；确认无需公网下载，浏览器打开前端。
3. 在 `deploy/windows` 下运行 `Test-Mars.ps1 -Offline -Running`。创建全新演示任务，
   按前端提示完成 review，核对事件、图和报告下载；不能只看健康接口。
4. 停止后重新离线启动，确认原任务、产物、手工配置仍在。用前端管理密钥时保持
   `.env` 密钥行注释；使用测试密钥验证重启后仍显示已配置，日志不得输出密钥值。
5. 真实 PIMC 场景填好只读挂载及内网模型配置，再运行
   `start-mars-windows-production-offline.cmd` 和
   `Test-Mars.ps1 -Production -Offline -Running`。真实评测仍须核对 Adapter 清单、
   run 日志及结果来源，不能将 mock / Alpha fixture 当成正式结果。

只有前四步在实际 Windows 上通过，才可标记“Windows CPU 离线一键包交付完成”。
