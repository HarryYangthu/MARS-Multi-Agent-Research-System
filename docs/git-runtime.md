# Git 运行配置

MARS 在启动、研究任务接入以及编码前验证 Git。分支创建、代码补丁、代码差异和 CLI 代码记录共用经过验证的可执行文件，不依赖终端与图形应用恰好拥有相同的 PATH。

Windows 原生启动器使用安装时校验下载的 MinGit，并明确设置 `MARS_GIT_EXECUTABLE`。源码运行会检查 PATH 中所有 Git 候选以及标准安装目录；macOS 同样支持 PATH 和 Homebrew 的标准安装目录。只有 `git --version` 真实执行成功的候选才会被选中。策略与超时位于 `configs/git_runtime.yaml`。

需要指定 Git 时，在应用工作目录的 `.env.local` 配置完整路径，然后重新启动后端。例如：

```dotenv
# macOS（以实际安装位置为准）
MARS_GIT_EXECUTABLE=/opt/homebrew/bin/git
```

```dotenv
# Windows（路径含空格也支持；不加其他命令或参数）
MARS_GIT_EXECUTABLE="C:\Program Files\Git\cmd\git.exe"
```

显式路径无效时会明确阻止任务，不会默默换一个 Git。没有配置时，发现机制会跳过不可运行的候选；若没有可用 Git，会提示安装或配置。不会自动接受 Xcode 许可，不会自动修改 Git 信任目录。

`GET /api/readiness?project=<项目标识>` 的 `git_runtime` 检查包含实际路径、版本、来源或具体失败类型。配置、候选文件的时间戳变化会使选择缓存失效；其余探测最长缓存 30 秒。启动发现错误时后端仍能提供配置与诊断服务，新的研究任务会提前被阻止，避免先花费模型调用费用再在编码阶段失败。

已有任务恢复时仍检查实际代码仓：未提交改动、分支切换、基线变化与其他操作占用分别处理。修复 Git 安装不会跳过这些检查，也不会自动暂存、重置、覆盖用户代码。编码与执行阶段继续使用任务绑定的分支。
