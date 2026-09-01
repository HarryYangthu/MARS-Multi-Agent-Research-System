# MARS V3.1 Windows 一键运行

这套部署面向 **Windows 10/11 x64 + Docker Desktop Linux Containers**。MARS
仍运行在受控 Linux 容器中，因此不依赖 Windows 原生的文件权限、符号链接和
进程信号语义。第一版默认使用本机 CPU；GPU 是后续独立开关，不会静默回退。

## 你需要准备什么

1. 公司批准安装和使用的 Docker Desktop，启用 WSL2 和 Linux containers。
2. 将两个目录放在同一父目录：

   ```text
   MARS/
   ├─ mars_v2/
   └─ mars_v31_wireless/
   ```

3. 首次在线构建需要访问 Python、Node 和 Docker 镜像源。完全离线环境请使用
   下文的镜像导入方式。

`mars_v31_wireless` 运行时只挂载 `project_packs/` 和 `src/`，且均为只读。
不要把它的 `.git/`、`.venv/`、测试缓存或本机运行产物带入内网包。

## 首次一键启动（CPU）

1. 先启动 Docker Desktop，等状态显示 Running。
2. 双击仓库根目录的 `start-mars-windows.cmd`（或本目录的
   [start-mars.cmd](./start-mars.cmd)）。
3. 首次运行会生成本机私有配置 `.env`，构建镜像并等待健康及任务准入检查。
4. 浏览器会自动打开 `http://127.0.0.1:3001/`。

停止服务双击 [stop-mars.cmd](./stop-mars.cmd)。停止不会删除历史 run、配置或
知识库。查看容器状态可双击 [status-mars.cmd](./status-mars.cmd)。

如果 Overlay 不在默认相邻目录，编辑自动生成的 `.env`：

```dotenv
MARS_V31_OVERLAY_PATH=D:/MARS/mars_v31_wireless
```

Windows 路径可使用 `/`。不要给路径加额外的 Docker 容器路径。

设备开关也在同一个文件中：

```dotenv
MARS_EXECUTION_DEVICE=cpu
```

可选值只有 `cpu` 和 `gpu`。当前交付默认并已验证 CPU；切到 `gpu` 时不会静默
回退到 CPU，启动脚本会检查 SSH GPU readiness，配置不完整就明确失败。等拿到 GPU
主机后，再补齐受控密钥挂载并做真实远端任务验收。

## 配置 DeepSeek

在 `deploy/windows/.env` 中填写本机密钥和可访问的 endpoint：

```dotenv
DEEPSEEK_API_KEY=你的内网可用密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

在 `.env` 设置的密钥由 Docker Compose 注入后端容器；`.env` 不会进入镜像。若你在前端配置
工作台修改密钥，运行期副本会写入专用的 `mars_runtime` Linux volume，不会写回
Windows 源码目录。若内网不能直连公网 DeepSeek，将 `DEEPSEEK_BASE_URL` 改为
公司批准的兼容网关。不要把真实密钥写进 `windows.env.example`、YAML 或镜像
构建参数。

密钥有两种管理方式，选一种即可：

- 用 `.env` 管理：取消示例文件中密钥行的注释并填写值；启动环境优先于前端保存值。
- 用前端管理：保持 `.env` 中的密钥行注释或删除该行，前端保存的密钥才能在重启后
  从 `mars_runtime` 读取。不要留下 `DEEPSEEK_API_KEY=` 这样的空赋值；它也会覆盖
  持久化值。旧版本生成过 `.env` 的用户需要手动删除这条空赋值。

默认 `MARS_MOCK_MODE=auto`：没有可用模型时仍可完成演示。需要验证真实模型时，
先确认网络和密钥，再改为：

```dotenv
MARS_RUNTIME_MODE=staging
MARS_MOCK_MODE=never
```

## 完全离线导入

在一台可联网、已安装 Docker Desktop 的机器上运行：

```powershell
cd deploy\windows
.\Export-MarsImages.ps1
```

它会强制构建并复核 `linux/amd64` 架构；在 Apple Silicon 等非 amd64
构建机上需要 Docker Buildx。随后生成：

```text
deploy/windows/images/mars-windows-amd64.tar
deploy/windows/images/mars-windows-amd64.tar.sha256
```

导出和导入均要求默认端口 `3001/8000`，避免前端编译地址与内网启动端口不一致。
导出先写临时文件，成功后才替换正式包；已有同名包默认不覆盖，确认更新时使用
`Export-MarsImages.ps1 -Force`。SHA256 用于发现拷贝损坏，不代替可信来源审查。

将以下内容通过公司批准的介质和审查流程带入内网：

- 干净的 `mars_v2` 发布目录；
- 仅含受控源码和 Pack 的干净 `mars_v31_wireless` 发布目录；
- 上述 `.tar` 和 `.sha256`。

在内网 Windows 上启动 Docker Desktop，然后双击
[start-mars-offline.cmd](./start-mars-offline.cmd)。启动脚本会先强制校验 SHA256，
再导入并复核三个镜像的 OS/架构；离线模式禁止 pull 和 build。

首次导入后仍需保留 `.tar` 和 `.sha256`；重复运行离线入口仍会校验并导入此包。

## 真实 PIMC 生产模式

真实研究代码与数据绝不打进 MARS 镜像。在 `.env` 中设置两个绝对路径：

```dotenv
PIMC_REPO_HOST_PATH=D:/PIMC/code
PIMC_DATA_HOST_PATH=E:/PIMC/data
```

仓库必须包含：

- `tools/mars_adapter_entry.py`
- `mars_baseline_manifest.json`

数据目录必须包含 `mars_data_manifest.json`。准备好真实模型配置后：

- 可联网构建：双击 [start-mars-production.cmd](./start-mars-production.cmd)。
- 内网离线：双击仓库根目录的 `start-mars-windows-production-offline.cmd`，或本目录
  [start-mars-production-offline.cmd](./start-mars-production-offline.cmd)。

生产模式强制 `MARS_MOCK_MODE=never` 和非 mock 执行配置；CPU 的 Project Adapter
仍走本地子进程。两棵目录只读挂载，候选输出仍只写入 MARS 自己的 Linux volume。
容器专用的 `repo_link.production.yaml` 把旧 Core 读取路径映射到同一个只读仓库，
不修改本机 `projects/pimc/repo_link.yaml` 或 baseline 保护规则。

启动会核对实际设备、运行模式和 `/api/readiness`；缺模型配置、mock 执行器等阻塞
项不会被报成“启动成功”。失败后保留容器和数据，便于修正配置再启动。

注意：生产启动通过只说明挂载、服务和任务准入配置通过；真实清单完整性会在
Adapter 运行前继续校验。它不等于模型请求、GPU、真实数据评测或 holdout 结果已经
验证。科研结论仍要以 run 内持久化的任务、日志、资源账本和评测产物为准。

## 数据存在哪里

默认使用六个 Docker named volumes：

- `mars_runs`：每次研究 run 的完整沉淀；
- `mars_workspace`：上传、候选和受控工作区；
- `mars_knowledge`：Chroma 本地知识库；
- `mars_configs`：前端配置工作台修改后的 YAML；
- `mars_runtime`：前端配置工作台写入的运行期密钥；
- `mars_redis`：Redis AOF。

普通停止和升级不会删除它们。不要执行 `docker compose down -v`，除非已经备份且
明确要清空全部 MARS 数据。

## 常见问题

### 提示不是 Linux Containers

在 Docker Desktop 菜单切换到 Linux containers。MARS 不支持直接跑在 Windows
container 或原生 Windows Python 中。

### 端口被占用

编辑 `.env`，例如：

```dotenv
MARS_FRONTEND_PORT=3101
MARS_BACKEND_PORT=8100
```

修改端口后需重新运行在线启动以重建前端；离线镜像使用默认的 `3001/8000`。

### 公司策略禁止 PowerShell 脚本

这些 `.cmd` 不绕过 ExecutionPolicy。请按公司 IT 策略为本地签名脚本放行，或在
已批准的 PowerShell 终端直接运行 `Start-Mars.ps1`。不要自行关闭公司安全策略。

### 启动失败但不知道原因

在 PowerShell 中运行：

```powershell
cd deploy\windows
.\Test-Mars.ps1
docker compose --project-directory . --env-file .env -f compose.yaml logs --tail 100
```

服务已启动时用 `Test-Mars.ps1 -Running` 检查页面、后端及任务准入；离线部署可再加
`-Offline` 检查镜像包校验和，生产部署需同时加 `-Production`。

维护者可离线执行 `tests/Test-DeploymentScripts.ps1`，验证 PowerShell 语法、默认
配置、错误分支、就绪检查和校验和处理。它使用 Docker 测试替身，不启动容器，
不等价于 Windows Docker 验收；CI 已配置 Windows PowerShell 5.1 / PowerShell 7
双运行时执行此测试。

健康地址：

- 后端：`http://127.0.0.1:8000/health`
- 前端：`http://127.0.0.1:3001/`

## 当前边界

- 已实现：面向 Windows x64 / Linux containers 的本地 CPU、在线构建、离线镜像
  导入、V3.1 Overlay、持久化 volume、只读真实 PIMC mounts 与一键入口。
- 实测范围与未验收项见 [VALIDATION.md](./VALIDATION.md)。目标平台支持代码已
  实现，不代表当前修订已在真实 Windows 主机验收；完整 x64 离线包也必须实际导出
  并通过校验后才能移交，不能只复制这些脚本就宣称离线部署完成。
- 未宣称：Windows 原生运行、Windows containers、真实 SSH GPU 已验证、内网模型
  网关已联通。
- GPU 开关：已通过显式 `cpu/gpu` 设备选项接入 SSH GPU runner；拿到 GPU 主机
  信息后，再做真实 job、日志、产物和资源账本验收。
