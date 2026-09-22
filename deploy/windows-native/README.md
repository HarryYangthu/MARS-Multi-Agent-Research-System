# MARS：Windows 原生 CPU 一键安装与启动

适用：Windows 10/11 **x64**，可联网安装，CPU 本地调试。使用当前 MARS 代码，
无需另备旧 Overlay 项目或 Docker。建议解压到 `D:\MARS` 这样的短路径。

## 最短操作步骤

1. 把完整代码包解压到 Windows；不要在压缩包预览窗口里直接运行。
2. 双击根目录 **`MARS-Windows.cmd`**，按回车选择“启动”。首次自动安装全部依赖。
3. 按向导填写 API 地址、模型名称和 Key。Key 输入不显示。
4. 安装与健康检查完成后，自动打开 **http://127.0.0.1:3001/**。

之后仍双击 `MARS-Windows.cmd`，按回车即可启动。启动完成后可以关闭启动窗口，
服务在后台运行。停止时双击 `stop-mars-windows-native.cmd`。

也提供独立入口：

| 文件 | 用途 |
| --- | --- |
| `install-mars-windows.cmd` | 一键安装或修复依赖，不启动服务 |
| `start-mars-windows-native.cmd` | 一键启动前后端，缺依赖时自动安装 |
| `configure-mars-api.cmd` | 配置云端/本地模型 API；更改前先停止服务 |
| `stop-mars-windows-native.cmd` | 停止本目录启动的服务，保留任务、配置和数据 |
| `MARS-Windows.cmd` 菜单 5 / 6 | 查看状态 / 发出小型真实模型请求检查 API |

原有 `start-mars-windows.cmd` 是旧 **Docker** 入口，本方案使用带 `native` 的启动入口。

## 自动安装的内容

- Python 3.11.14、uv、Node.js 22.23.2、npm、MinGit。
- 后端依赖和开发检查工具：按照 `uv.lock` 导出的版本安装。
- PIMC CPU 依赖：PyTorch CPU、SciPy、Matplotlib，以及 TensorBoard。
- 前端：按照 `frontend/package-lock.json` 安装，随后执行类型检查。
- 缺少 Microsoft C++ 运行库时，从微软下载并检查数字签名后安装；Windows 可能要求管理员确认。

工具、虚拟环境和下载缓存放在 `local/windows/`，不会复用拷贝过来的 Mac `.venv`，
不会修改全局 Python/Node 或系统 PATH。初次安装需要访问 GitHub、nodejs.org、
PyPI、npm、download.pytorch.org；不是离线安装包。重装会重建这个专用虚拟环境。

启动时将 Python 指向 Windows 虚拟环境，同时统一前后端地址、Socket.IO 和 CORS。
单机使用一个后端进程，事件总线可使用内置进程内实现，无需另装 Redis 服务。
脚本不会下载模型权重、自动充值 API 账号或生成示例研究结果。

运行时版本、端口、下载地址与 SHA256 在 `configs/windows_native.yaml`。启动前校验
工具压缩包，校验失败会停止；不会关闭 TLS 校验。发行来源：
[uv 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)、
[Node.js 官方发行文件](https://nodejs.org/dist/v22.23.2/SHASUMS256.txt)、
[Git for Windows 官方发行及校验和](https://github.com/git-for-windows/git/releases/tag/v2.55.0.windows.5)。

## API 配置

向导会同时配置 Idea 作者、Idea 独立评审者和其他 Agent，避免只改界面的 `idea`
一行而漏掉正式执行所用的 `idea_author`、`idea_reviewer`。

**DeepSeek 官方**：选择 1，地址默认 `https://api.deepseek.com/v1`，输入自己的 Key。
模型留空会保留代码中各 Agent 的原模型；填写模型名称则统一使用该名称。

**公司网关或其他 OpenAI 兼容 API**：选择 2，填写实际 Base URL（通常以 `/v1`
结尾）、网关支持的模型名称和 Key。

**本机模型服务**：选择 3。例如 LM Studio 已加载模型并开启兼容服务后，可填
`http://127.0.0.1:1234/v1`；Ollama 兼容服务常用 `http://127.0.0.1:11434/v1`。
模型名称必须与服务实际加载的名称一致。无鉴权本机服务可以留空 Key。
本脚本负责 MARS 对接；模型服务需要先由你安装、加载模型并启动。
Idea 所选模型还需支持当前原生工具调用与结构化输出协议。

Key 只写入 Git 忽略的根目录 `.env.local`，脚本会限制该文件的 Windows 用户权限，
不会写进 YAML、下载包、命令行参数或检查报告。系统环境中已有的同名 Key 优先于
`.env.local`；如切换后仍使用旧账号，请检查 Windows 的用户环境变量。

配置后可用 `MARS-Windows.cmd` 的 **6 Test API** 发出作者/评审者各一次小型真实请求。
这会使用所配置账号的少量额度。HTTP 402 通常是余额/计费问题，401 是 Key 问题；
脚本会显示脱敏错误码，不会把服务启动或模型列表查询当成完成了研究任务。

前端 API 配置页：**http://127.0.0.1:3001/config/agents**。
在界面保存模型配置后，请停止并重新启动服务，让各 Agent 重新加载配置。
本机后端 API 文档：**http://127.0.0.1:8010/docs**。

## 把 PIMC 源码和数据接到 Windows

MARS 源码包不包含真实 PIMC 数据、模型权重或外部研究源码。把这些资料另行复制到目标电脑。
API 向导允许填写 PIMC 源码目录（必须含 `train_static.py`）和数据文件路径，会更新
`projects/pimc/repo_link.yaml` 与 `configs/execution.yaml` 的本机路径，保留基线保护规则。
修改前的 YAML 在 `local/windows/config-backups/`。

如果先只调试前端和 API，可以留空，之后在项目页接入源码、在新任务里选择数据。
不要使用旧 Mac 上的 `/Users/...` 或 `/opt/anaconda3/...` 路径发起 Windows 实验。
代码包不迁移旧任务的绝对路径与执行环境；Windows 验证建议新建任务。
Execution 开始后，已有 TensorBoard 集成会展示该次实际实验过程。

## 常见问题

- **PowerShell 阻止脚本**：先在下载的 ZIP 文件属性中选择“解除锁定”，再解压可信代码。
  入口仅为本次进程使用 `RemoteSigned`，不修改系统执行策略；组织策略仍有最高优先级。
  若公司禁止脚本，请让管理员按公司流程放行，不需要设置 `Bypass` 或 `Unrestricted`。
- **下载失败**：检查上述官方源的网络可达性；重新运行安装可复用已完成且校验通过的压缩包。
  日志中缺少某个轮子时会明确失败，不会假装安装成功。安装窗口会停留供查看错误。
- **端口占用**：停止占用程序，或修改 `configs/windows_native.yaml` 的两项端口再启动。
  脚本不会根据端口随意结束其他程序。
- **前后端启动失败**：查看 `local/windows/logs/` 下对应启动时间的日志。
  菜单 5 可查健康状态。只有前端页面、后端健康和前端到后端代理都通过才显示 ready。
- **复制到了另一位置**：重新运行安装。Python 虚拟环境包含绝对路径，不应直接搬用。
- **停止时仍在关闭**：等待后再次执行停止；后端会先请求任务退出并执行清理，避免立即强杀。

## 验证范围

维护检查：`deploy/windows-native/Test-NativeScripts.ps1` 验证真实 PowerShell 语法、
配置、路径转义、进程身份与校验失败分支。Python 测试验证本机配置文件、基线规则保留、
依赖完整性，并实际启动/关闭后端；没有替身模型或模拟服务成功。

另附 `.github/workflows/windows-native.yml`，用于 Windows 实机 CI 的安装、启动、代理和停止验证。
当前在 macOS 完成的脚本与后端检查不等于 Windows 实机验收；首次 Windows 启动仍以目标电脑
的实际安装日志和健康结果为准。模型完整任务、真实数据实验与科学结论需要另行验收。
