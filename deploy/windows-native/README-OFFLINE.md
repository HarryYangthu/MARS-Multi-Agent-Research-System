# Windows 离线包

适用 Windows 10/11 x64。软件依赖已包含在包里，首次安装和以后启动都不需要访问 GitHub、PyPI、npm 或其他外部安装源。模型调用使用你配置的公司模型 API。

1. 把完整 ZIP 复制到公司电脑。若可信 ZIP 的文件属性显示“解除锁定”，先解除锁定，再解压到短路径，例如 `D:\MARS-Product`。不要直接从压缩包窗口运行。
2. 双击 `MARS-Windows.cmd`。首次自动校验依赖，安装独立运行环境并构建前端。
3. 首次按向导配置模型地址、模型名称和 Key。公司网关选择自定义 OpenAI 兼容服务。
4. 安装、后端健康和前后端代理检查通过后，自动打开 `http://127.0.0.1:3001/`。

以后双击同一个入口直接启动。停止用 `stop-mars-windows-native.cmd`，重新配置 API 用 `configure-mars-api.cmd`，其他管理选项用 `manage-mars-windows.cmd`。

包内有 Windows 专用 Python 3.11.14、Node.js、MinGit、uv、Microsoft C++ 运行库、PyTorch CPU、SciPy、Matplotlib、TensorBoard、前后端全部锁定依赖和 ZCode 3.14.5 命令运行时。默认继续使用 ZCode，不需要安装完整 ZCode 桌面客户端。

若系统缺少 C++ 运行库，首次可能需要管理员安装权限，安装器要求重启时按提示重启后再次双击。组织的脚本/软件策略仍生效，入口不绕过策略，也不修改系统执行策略。

软件环境保存在 `local/windows/`，日志在 `local/windows/logs/`。不依赖目标机器已有的 Python、Node 或 Git。不要单独搬运安装后的虚拟环境；需要换目录时重新解压整包并双击，环境会在新位置重新生成。

不要删除 `WINDOWS-OFFLINE.json` 或 `offline/`。缺文件或校验失败时入口明确停止，绝不会自动转向外网下载。`install-mars-windows.cmd` 可离线重建环境；重建不会删除已有研究结果。

源码包不包含用户 API 密钥、真实研究代码、数据、基线权重或旧任务。启动后接入 Windows 上的项目和数据；Mac 的绝对路径不能直接迁移。任意外部研究仓额外要求的库不等于 MARS 自身的运行依赖，需要按那个项目的实际要求准备。

构建来源：Python 来自 Astral 的 Windows 独立发行，Node/Git/uv 来自官方发行，Python 库使用 Windows wheel，前端缓存按锁文件校验，ZCode 命令运行时来自官方 Windows 发行。包内 SHA256 清单校验文件完整性，不替代软件签名或公司软件准入。
