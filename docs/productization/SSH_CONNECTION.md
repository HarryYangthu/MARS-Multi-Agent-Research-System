# F 增量：严格 SSH 连接与认证

本切片提供密码和密钥共用的连接层；既有 `RemoteExecutor` 可以显式选择它。它不等于项目向导、平台凭据存储、远端 GPU 真任务或 F 的全部验收。

## API 与信任顺序

实现位于 `backend/app/execution/remote/ssh_transport.py`：

- `SshConnectionPolicy`：主机、端口、用户名、`known_hosts` 路径、独立核验的 `SHA256:` 主机公钥指纹、连接超时、合并 stdout/stderr 字节上限。
- `SshCredentials`：连接层持有的临时密码或密钥路径/口令。不是 Pydantic model/dataclass；`repr` 脱敏，常规 JSON/YAML 与 pickle 序列化拒绝它。调用方仍须遵守凭据边界，Python 对象不是隔离沙箱或可保证清零的内存。
- `AsyncSshTransport.preflight()`：真实连接、验证主机并完成指定认证后才返回 `authenticated`；固定 `scope=ssh_connection_only`。`blocked` 可同时具有 `host_key_verified=true`，表示服务器身份已验证但认证失败，不表示连接可用。
- `run/upload/download`：符合既有 `RemoteTransport` 接缝，供 durable runner 复用，不增加调度器。SFTP 不构造 `scp` shell 目标字符串。

每次连接都读取 `known_hosts`，由 AsyncSSH 原生规则匹配主机及端口，再将可信公钥集合收窄为与显式 SHA256 pin 相同的 key，并保留 `@revoked` 集合。原生验证在认证之前执行；`begin_auth` 再核对实际协商 key 并记录身份回执。没有关闭 host key 检查、自动接受新主机或失败降级。初始接口支持 raw host key；CA-only host certificate 信任不在此范围。

pin 必须来自独立可信渠道；从当前未验证网络连接获取 key 再自动接受不能构成身份验证。本模块不实现这种首次自动信任流程。

新增依赖 `asyncssh[bcrypt]>=2.24,<3`（锁定 2.24.0）。现有 OpenSSH subprocess 只提供非交互密钥认证；AsyncSSH 让密码和加密密钥口令经协议连接参数传递，并提供异步连接、原生 known_hosts 和 SFTP，避免密码命令行、`sshpass` 或自行实现 SSH。`bcrypt` 用于加密 OpenSSH 私钥。接口依据 [AsyncSSH 官方 API](https://asyncssh.readthedocs.io/en/latest/api.html)。

## 配置与凭据边界

`configs/execution.yaml` 的 `execution.remote_gpu.transport` 默认仍为 `system_ssh`，保留原有密钥/known_hosts 行为。新层须显式选 `asyncssh`，并选 `auth_method: key` 或 `password`；未提供 pin 或其他必要配置时阻断，密码模式不能静默走旧 transport。旧 transport 不是本切片双重主机验证的验收对象。

新层使用这些非秘密引用：

| 配置环境变量 | 用途 |
| --- | --- |
| `MARS_REMOTE_SSH_TRANSPORT` | 显式 `asyncssh` |
| `MARS_REMOTE_SSH_AUTH_METHOD` | `key` 或 `password` |
| `MARS_REMOTE_SSH_HOST` / `PORT` / `USER` | 目标身份 |
| `MARS_REMOTE_SSH_KNOWN_HOSTS` | 已审核的 known_hosts 文件路径 |
| `MARS_REMOTE_SSH_HOST_KEY_SHA256` | 独立核验的完整 `SHA256:` 指纹 |
| `MARS_REMOTE_SSH_KEY_PATH` | 仅密钥模式需要；POSIX 文件不可向 group/other 开放 |

现有 `MARS_REMOTE_ROOT`、`MARS_REMOTE_PYTHON`、GPU 及 runner 配置继续由 `RemoteExecutorConfig` 管理。

秘密值只能通过显式 `SshCredentials` 或当前进程环境 `MARS_REMOTE_SSH_PASSWORD` / `MARS_REMOTE_SSH_KEY_PASSPHRASE` 交给连接层。这两项不从 `.env`、Settings 或任务 YAML 加载，密码两侧空白不删除。`execution.remote_gpu` 的 inline `password/passphrase/private_key/credentials` 配置被拒绝。不要将密码写进 shell 历史、启动参数或示例配置。

连接明确禁用用户 SSH config、默认密钥、SSH agent、agent forwarding、keyboard-interactive、host-based 和 GSS 回退；`key` 和 `password` 不互相回退。密码/口令不加入远端 argv 或发送的环境变量；包含已知凭据的命令参数/目标路径会被拒绝。回执、异常错误码和输出不会带已知密码/口令；本层不记录原始认证异常或日志。它不能撤回调用者已经自行写入文件、模型输入或其他日志的秘密。

平台凭据存储和 UI 秘密输入尚未接入；当前进程环境属于过渡接缝，并非平台凭据保险库，也不隔离同进程任意代码或具同等权限的进程。

## 实测与未完成边界

定向命令：

```bash
PYTHONPATH=backend python -m pytest backend/tests/unit/test_ssh_connection.py backend/tests/unit/test_remote_executor.py backend/tests/unit/test_remote_runner.py backend/tests/unit/test_remote_adapter.py
```

本次 macOS 本机执行结果：**57 passed**。SSH 测试启动真实临时 OpenSSH sshd，监听 loopback 随机端口，不使用 sudo、不更改用户账号、不联系公网服务器。测试生成独立的真实 host/client key、加密 key 和 known_hosts；通过实际 `id`、`printf`、`cat` 及 SFTP 文件往返验证通路，退出后关闭连接并终止测试 daemon。

独立审查使用真实认证作为前置条件，先复现远端符号链接下载、本地目标符号链接被覆盖两项失败；修复后同一独立探针 **2 passed**。仓内另有 8 项源/目标/父目录符号链接回归。OpenSSH 的 StrictModes 会拒绝权限不合规的临时目录祖先；测试应使用用户私有临时根目录，不能把环境认证失败误计为文件路径安全拒绝成功。

已验证：密钥和加密密钥认证成功；错误口令与未授权 client key 拒绝；正确/错误 pin、错误/空/缺 known_hosts、撤销 host key；主机验证失败先于密码认证；未知 OS 用户的真实密码认证拒绝；真实缺失命令路径和缺文件失败；shell 元字符作为参数文字；真实输出字节上限、连接不可用与命令超时；真实文件权限和凭据禁止序列化/输出脱敏；上传/下载源、目标和父目录的真实符号链接拒绝，保护原有文件不被覆盖。既有 executor 选择新层、远端 Python 路径无效时真实阻断，没有伪造 GPU 成功。

没有可用于自动化的授权 OS 密码，因此**密码登录成功尚未验收**。缺 `sshd` 或不能无特权启动的环境会明确 skip，不使用服务替身补成功。这里也未完成 Windows SSH 环境、外部 Linux GPU、远端现有代码/同步代码向导、环境探测和完整训练恢复验收。

连接/命令超时返回 `ssh_timeout_outcome_unknown` 并关闭客户端连接；这不证明远端 durable job 或全部进程组已经停止。`asyncio` 取消与关闭仍需等待底层清理，不能据此宣称发布矩阵的停止时限。真实作业仍必须按原 `job_id` 查询状态，不能因连接失败直接重提同一实验。

SFTP 仅负责普通文件传输，拒绝非绝对远端路径、`..`、源/目标及其父目录现存的符号链接。下载先写独占临时普通文件、flush/fsync，完成后再次核验目标并原子替换；失败清理本机临时文件，不用半文件覆盖既有目标。这些检查不是远端文件系统 jail，也不抵御其他同权限进程在检查后并发替换祖先目录。安全同步目录、上传的原子提交/断连中间文件处理和授权根目录约束仍须在 F 后续目录/任务层完成，不能把文件往返当成完整隔离验收。
