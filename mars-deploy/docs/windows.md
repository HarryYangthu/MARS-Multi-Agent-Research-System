# Windows 适配检查与已知限制

检查范围：mars-core、mars-app 和 mars-deploy 在 Windows 10/11 x64、普通用户权限下运行时会遇到的问题。下表"状态"一列：**已修**表示内核或 mars-app 已合入修改，**部署处理**表示由 mars-deploy 的安装脚本或启动器解决、不改代码，**待内网确认**表示只能在公司 Windows 电脑上实测。

## 1. 检查结果

| 问题 | 影响 | 处理 | 状态 |
|---|---|---|---|
| `api.py`、`cli.py`、`kernel.py` 顶层 `import fcntl` | Windows 上一导入就失败，mars-app 连 `mars.api` 都加载不了 | 新增 `mars/osutil.py`，Windows 用 `msvcrt.locking` | 已修（内核） |
| `api.stop` 依赖 `SIGSTOP` 和 `ps` | 网页上"停止"无效 | Windows 用 `taskkill /PID <pid> /T /F` 结束整棵进程树 | 已修（内核） |
| `CodingAgentSession.close` 用 `os.killpg` / `SIGKILL` | Step2 每轮结束时报错，CodingAgent 进程残留 | Windows 用 `taskkill /T /F` | 已修（内核） |
| `bin/mars` 是 sh 脚本，`caffeinate` 只有 macOS 有 | 网页发起、续跑研究失败；长跑时电脑睡眠 | `api._launch` 在 Windows 上直接运行 `.venv\Scripts\python.exe -m mars.cli`，并脱离控制台；CLI 和启动器调用 `SetThreadExecutionState` 防止睡眠 | 已修（内核）+ 部署处理 |
| 网关用 venv 的 `litellm` 命令脚本启动 | Windows 上是 `Scripts\litellm.exe`，路径不对 | 改为 `python -m litellm.proxy.proxy_cli` | 已修（内核） |
| 远程训练结果用 `rsync` 取回 | Windows 没有 rsync | 改为 `ssh … tar` 数据流，用 Python `tarfile` 解包 | 已修（内核） |
| `GIT_SSH_COMMAND` 需要 sh | MinGit 里没有 sh 的话 `git push` 会失败 | 检查过 MinGit 2.55 自带 `usr\bin\sh.exe` 和 `ssh.exe`，不用改 | 无需修改 |
| MinGit 系统配置 `core.autocrlf=true` | 检出的文件变成 CRLF，候选范围检查会误报改动，和 GPU 服务器上的代码也不一致 | 启动器用 `GIT_CONFIG_COUNT/KEY/VALUE` 环境变量强制 `core.autocrlf=false` | 部署处理 |
| 大量不带 `encoding` 的 `read_text()` / `open()` | 中文 Windows 默认编码是 GBK，读 UTF-8 的提示词、配置、事件会乱码或报错 | 启动器和 `api._launch` 设置 `PYTHONUTF8=1`（子进程会继承） | 部署处理（长期建议代码里写明 `utf-8`） |
| 路径含中文和空格 | 例如 `D:\Code\新建文件夹\`、`MARS 项目` | 沙箱测试和 CodingAgent 检查都特意使用含中文和空格的目录；Optuna 的 SQLite 路径已用 SQLAlchemy 验证（中文、空格、`#`、`%` 都能正确解析） | 已覆盖测试，待内网确认 |
| `PermissionPolicy` 只认 `/` 和 `~` 开头的路径，并跳过所有含空格的路径 | Windows 上 `C:\…` 路径完全不检查 | 识别盘符路径和 UNC 路径；路径类的键即使含空格也检查；Windows 上 `shlex` 保留反斜杠 | 已修（内核） |
| step2 白名单写死 `/opt/anaconda3/bin/python3` | 内网的领域 Python 路径不同，CodingAgent 跑项目检查命令会被拒绝 | 按项目声明（或本机 `machine.yaml`）的 `python` 生成，命令按平台加引号 | 已修（内核） |
| CodingAgent 只认 macOS 的 `/Applications/CodingAgent.app` | 找不到 CodingAgent | 安装包带官方 Windows 版 CodingAgent 3.14.5 的运行时（`glm/`、`tools/`、`config/`）和 Node 22.23.2，启动器设置 `MARS_CODINGAGENT_COMMAND` | 部署处理 |
| 编码 Agent 沙箱只有 macOS 的 `sandbox-exec` | Step2 拒绝启动 CodingAgent | `MARS_SANDBOX_COMMAND` 指向 `sandbox/mars_sandbox.py`：低完整性级别限制写入，并给凭据文件加"不可读上"标签（见第 2 节） | 已修（内核）+ 部署处理，待内网确认 |
| 编码 Agent 能读到凭据（`local/models.env`、GPU 私钥、`~/.ssh`） | 读到的内容会经网关进入模型上下文 | `--deny-read` 给这些文件加 Medium 完整性的 `NoReadUp` 标签（SDDL `S:(ML;;NRNWNX;;;ME)`），低完整性的 Agent 读不到，Medium 的内核仍可读；加不上就拒绝启动 | 已实现，待内网确认 |
| 进程存活判断用 `os.kill(pid, 0)`（`api.active_jobs`、`_alive`） | Windows 上信号 0 是 `CTRL_C_EVENT`：同一控制台时会向训练进程组发 Ctrl+C，否则抛异常，运行中的任务被当成已结束、任务文件被删 | `osutil.alive()`：`OpenProcess` 加 `GetExitCodeProcess` | 已提案（第二轮），待内核合入 |
| CLI 以 `DETACHED_PROCESS` 启动 | CLI 没有控制台，它启动的每个 git、训练、网关、node 进程都会弹出控制台窗口 | 改为 `CREATE_NO_WINDOW`（隐藏控制台，子进程共用） | 已提案（第二轮），待内核合入 |
| `api.status` 汇总旧记录时在项目基线副本里执行 git | 内网刚装完，或者从别的机器拷来 `runs/` 时，这个目录还不存在，`status`、运行列表、报告导出都会报错 | 同时捕获 `OSError` | 已修（内核） |
| 测试里的 `symlink_to` | Windows 普通用户（没开开发者模式）没有创建符号链接的权限 | 创建失败时跳过并写明原因 | 已提案（第二轮），待内核合入 |
| 独立项目的路径判断（`project.py`：`resolve()` 后 `is_relative_to()`） | 盘符、大小写、映射盘、中文和空格 | Windows 上路径比较不区分大小写，映射盘 resolve 后统一为 UNC；`mars-check` 的 `paths` 一项在目标机器上实测 | 待内网确认 |
| SSH 私钥权限 | Windows OpenSSH 遇到权限过宽的私钥会拒绝使用 | `icacls <key> /inheritance:r /grant:r "%USERNAME%:R"`；`check_remote.py` 会识别这种错误并给出提示 | 部署处理 |

## 2. 已知限制

1. **网络隔离**：普通用户在 Windows 上做不到"只放行本机网关端口"。防火墙规则和 AppContainer 回环豁免都需要管理员权限，Codex 的 Windows 沙箱在非管理员模式下也是同样的结论。包装脚本限制写入（CodingAgent 和它启动的所有子进程都运行在低完整性级别，只有候选 worktree 和 CodingAgent 运行目录被标记为可写）并限制读取凭据（见下一条），但网络不做限制。必须在 `local/mars.env` 里写上 `MARS_SANDBOX_WINDOWS_NETWORK=unrestricted` 表示接受这一点，否则 CodingAgent 不会启动。另外还有三层防护：PermissionPolicy 逐条审批命令（curl、wget、ssh 等会被拒绝）；CodingAgent 进程拿不到上游 API Key；代理环境变量指向一个不可用的地址（只能挡住遵守代理设置的工具，不算强制隔离）。需要严格网络隔离时，可以请 IT 授予管理员权限后改用 AppContainer，或者把编码 Agent 放到 Linux 上运行（bubblewrap）。
2. **凭据不可读是通过完整性标签实现的**：`--deny-read` 给每个凭据文件（目录则递归每个文件）加 Medium 完整性的 `NoReadUp` 标签。这是对这些文件的**持久**改动：会话结束后标签仍在，其他低完整性进程（如浏览器沙箱）也读不到它们——对凭据而言这是更安全、可接受的。GPU 私钥默认放在 `~/.ssh` 下（模板如此），已被覆盖。Linux 上不持久：bubblewrap 用空文件/ tmpfs 覆盖，只在沙箱内生效；Landlock 只影响被限制的进程本身。这部分在 Linux 上已实测（见下文验证情况），Windows 上待内网确认。
3. **低完整性标签会保留**：候选 worktree 和 CodingAgent 运行目录在会话结束后仍然是低完整性标签，其他低完整性进程（比如浏览器的沙箱进程）也能写这两个目录。它们都是 MARS 自己的临时目录，影响有限。
4. **文件锁只能是排他的**：Windows 的 `msvcrt.locking` 没有共享锁。`api.status()` 判断"是否在运行"时会短暂加一次排他锁再释放；如果恰好在这一瞬间有 CLI 正在启动，CLI 会报"已有进程在执行"。概率很低，重试一次即可。
5. **只读打开的文件能否加锁**：`api._process` 以只读方式打开 `run.lock` 后加锁。按 Windows 文档，`LockFile` 只要求句柄有读或写权限之一，应该可以加锁，但需要实测：`mars-check` 的 `locks` 一步会用两个真实进程验证（持锁进程以 `a+` 打开并写入 pid，检测方以只读打开，要能读到 pid、能检测到锁被占用，持锁进程退出后要能加上锁）。macOS 上已通过，Windows 上待内网确认。
6. **git 路径区分大小写**：项目声明里 `repo.required_files`、`repo.context_files` 由内核用 `git cat-file <提交>:<路径>` 核对，git 区分大小写，所以路径的大小写必须和仓库里完全一致，虽然 Windows 文件系统本身不区分。
7. **目录联接（junction）**：Python 3.13 里 junction 不算符号链接。项目的 `runs`、`workspace` 如果是指向项目外的 junction，内核会拒绝（`paths` 一项会测）；但 `context_sources` 目录里的 junction 会被 `rglob` 跟进去，读到项目以外的文档。风险低，请不要在背景资料目录里放 junction。
8. **长跑防睡眠**：`SetThreadExecutionState` 能阻止空闲睡眠，但挡不住合盖和手动睡眠。长时间运行时请接上电源，并在电源设置里关闭合盖睡眠。

## 3. 内网 Windows 上要确认的项目

运行 `mars-check.cmd`（离线部分）和 `mars-check.cmd --live --remote --sources --evaluate`，把 `code\mars-deploy\local\checks\` 下的报告发回来。重点看这几项：

- `locks`：三项都为 true（对应限制 4）。
- `paths`：全部为 true；`junction_out_of_project_refused` 必须为 true。
- `sandbox`：`read_deny_enforced` 和 `reads_still_work` 都为 true（凭据读不到，普通文件和标准库仍可读）。
- `sandbox`：`verdict` 为 "PASS: writes confined; network NOT isolated…"，并且 `refuses_without_acknowledgement` 为 true。
- `codingagent`：在低完整性级别下 CodingAgent 能启动并建好会话；`codingagent-live` 在宿主侧确认越界写入被挡住（这一项在 Windows 上不要求网络隔离）。
- `core-tests` 和 `app-tests` 全部通过；`app` 能打开网页。
- `remote` 和 `evaluate`：经 Windows 自带的 OpenSSH 和 MinGit 推送代码、训练、取回结果都正常。
