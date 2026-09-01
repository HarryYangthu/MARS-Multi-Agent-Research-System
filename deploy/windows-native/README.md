# MARS V3.1 原生 Windows 一键运行（无 Docker）

此入口直接运行 Python 后端和 Next.js standalone 前端，不安装、不调用 Docker，
单机模式使用进程内事件总线，因此也不要求 Redis。

## 前置条件

- Windows 10/11 x64；
- 公司批准安装的 Python 3.11 x64；
- 公司批准安装的 Node.js 20 x64；
- `mars_v2` 与 `mars_v31_wireless` 位于同一父目录。

先在 PowerShell 执行：

```powershell
cd D:\MARS\mars_v2
.\deploy\windows-native\Test-Mars.ps1
```

检查通过后，双击仓库根目录的 `start-mars-windows-native.cmd`。首次启动会创建
`.venv-windows`、安装依赖、构建前端，然后打开 `http://127.0.0.1:3001/`。

如果内网不能访问公网包源，在 `deploy/windows-native/.env` 中设置公司镜像：

```dotenv
MARS_PYTHON_INDEX_URL=https://你的内网-PyPI-镜像/simple
MARS_NPM_REGISTRY=https://你的内网-npm-镜像
```

完全断网时，源码本身不能提供 Python/Node 第三方依赖。请在获准联网且装有相同
Python/Node 版本的 Windows x64 构建机执行：

```powershell
cd D:\MARS\mars_v2\deploy\windows-native
.\Export-MarsNativeDependencies.ps1
```

将生成的 `deploy/windows-native/offline/` 连同两个源码目录带入内网，再双击
`start-mars-windows-offline.cmd`。离线入口对 pip 使用 `--no-index`，并直接运行
预构建前端；缺少 wheel 或前端产物时明确失败，不会偷偷访问公网。

停止和状态入口：

```text
stop-mars-windows-native.cmd
status-mars-windows-native.cmd
```

运行日志位于 `deploy/windows-native/runtime/`。普通停止不会删除 `runs/`、
`knowledge/`、工作区或密钥配置。

## 能力边界

原生 Windows 入口用于单机 CPU 研究和演示。它不提供 Docker 的 Linux 只读挂载
隔离，也不应被当成多进程生产部署。真实 baseline、数据和候选执行仍需依赖项目
清单及 Gate 5；涉及生产放行时应继续使用受控 Linux 环境。
