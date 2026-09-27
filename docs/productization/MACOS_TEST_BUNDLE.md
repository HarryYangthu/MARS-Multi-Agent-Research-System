# macOS Apple Silicon 本地自包含测试应用

工作包 I 的一个切片：内置 Electron、CPython 3.11、锁定的 Python 依赖、Next 生产服务器与前端构建。输出是可搬迁的 `.app`，不是源码压缩包。仅本地 ad-hoc 签名；不具有 Developer ID 签名或 Apple 公证，不应称为正式安装包、公开发行或完整研究验收。

## 构建与检查

构建主机需要 macOS arm64、真实 standalone CPython 3.11、`uv`、`npm`、Apple `codesign`/`otool`，以及 Python 构建工具环境中的 PyYAML。构建工具本身不复制进包。先按锁安装 desktop/frontend 依赖并生成真实生产 `.next`；前端不能烘焙外部 `NEXT_PUBLIC_*` 后端地址。

```sh
python -m scripts.desktop.build_macos \
  --python-runtime /absolute/standalone-cpython-3.11-prefix \
  --output /absolute/new-build-directory

python -m scripts.desktop.verify_bundle \
  '/absolute/new-build-directory/MARS Local Test.app' \
  /absolute/new-build-directory/bundle-manifest.json

python -m scripts.desktop.smoke_macos \
  --bundle '/absolute/new-build-directory/MARS Local Test.app' \
  --manifest /absolute/new-build-directory/bundle-manifest.json \
  --output /absolute/new-smoke-directory \
  --deny-read /absolute/developer-checkout \
  --deny-read /absolute/developer-python-prefix
```

所有输出目录必须是新目录；不覆盖旧构建或旧证据。示例的 `python` 是构建工具，不是用户启动应用的前置依赖。正常运行使用应用内 `Contents/MacOS/MARS Local Test`，无需 `npm`、`uv`、shell 配置或开发 `.venv`。

当同一工作树存在尚未验收的 StateJournal 增量时，可显式传入 `--state-journal-ref <已提交 revision>`；这只对 `backend/app/harness/runtime/state_journal.py` 使用 `git show` 完整字节，回执记录该来源与文件哈希。其他文件仍须一致快照，不能将混合快照宣称为某次 commit 的原样构建。

## 包内容和来源

- `Contents/Resources/app/`：明确 shell 文件及其锁定 `yaml` 依赖。保留普通文件，避免 Python 无法读取 Electron ASAR 虚拟路径。
- `Contents/Resources/mars/`：从精确 source allowlist 选出的后端、配置、模板、合成示例 adapter；另含前端 production dependencies、`.next` 与 `public`。不含 `.env`、研究历史、开发 `.venv` 或真实研究代码。
- `Contents/Resources/python/`：standalone CPython 3.11 的实目录副本，随后按 `uv.lock` 导出带哈希 requirements 并为该 ABI 安装真实 wheels。原 uv 管理的 Python 与原开发环境不修改。只在副本移除 PEP 668 标记；移除带构建绝对路径 shebang 的 console scripts，保留 Python 可执行入口。
- `Contents/Resources/licenses/`：MARS、Electron/Chromium 许可证和冻结 Python requirements。Python 原生许可证、Python wheel 的 dist-info/许可证、Node 包许可证保留在对应包目录。此材料保留不等于法律合规审查完成。
- `Contents/Resources/local-test.json`：源码 revision、每个选中文件 SHA256、锁文件哈希、真实前端 build ID 和显式固定文件来源。

`bundle-manifest.json` 位于 `.app` 外，逐项记录普通文件 hash/尺寸/权限、目录权限与链接目标，拒绝逃逸或断裂 symlink、`.env*`、editable `.pth`、`pyvenv.cfg`。原生依赖检查列出真实 Mach-O 加载项与 LC_RPATH，拒绝包外绝对搜索路径和逃逸相对路径；按应用主程序、Python 或独立原生程序的宿主上下文解析 `@rpath`、`@loader_path` 与 `@executable_path`，要求非系统加载项能在包内递归解析。构建副本中上游 wheel 残留的外部绝对 LC_RPATH 会在签名前移除，随后仍须完整通过解析。`LC_ID_DYLIB` 自身标识不误判为加载路径。该静态检查不覆盖尚未实际执行的任意字符串动态 `dlopen`。签名前逐个对所有原生文件执行 ad-hoc 签名，再封装整个 `.app`；验证时同时核验每个原生文件与外层应用。仅 `codesign --deep` 对根应用通过不足以证明 Resources 下 wheel dylib 的签名有效。清单用于本地内容复核，未由外部发布者签名，不构成供应链真实性证明。

Next 的 custom server 需要完整 production dependencies；不能仅复制 `.next/standalone`，后者不追踪 custom server 所需的所有文件。`next.config.mjs` 读取的 `configs/frontend.yaml` 一并打包。前端缓存、standalone 重复副本和构建类型产物排除。

## 实际验证与边界

smoke 将 `.app` 复制到带空格的新路径，设置空 HOME/TMPDIR、系统 PATH 和故意无效的开发 Python override。真实窗口检查 renderer 无 Node、页面有内容、真实 API/WS 连通、未授权/跨源请求被拒绝；退出后核对本次服务 PID 已消失，无关真实 TCP listener 仍存活，再比对应用内容未变。

图形应用保持 Electron sandbox/contextIsolation/webSecurity。当前 macOS 对 `sandbox-exec` 包裹整个 Electron 图形进程出现嵌套 sandbox 初始化 EPERM，因此不关闭 renderer sandbox，也不声称整个 GUI 通过了外层读取隔离。另以应用内 Node 作为真实控制进程，在 `sandbox-exec` 拒绝源码、Conda、uv、nvm 等开发目录读取的条件下启动应用内 Python API 与 Next，验证真实 HTTP 响应并清理它们。Python executable/prefix/sys.path 和实际导入模块来源必须位于应用内；Node/Next 来源也核验。两层证据分别记录。

首次启动仅按精确 runtime_assets 清单初始化用户数据目录，之后仍执行已有资源哈希冲突拒绝策略。用户数据位于应用外，应用内 runtime 不读开发 `.env`、不继承 provider 密钥。尚未提供原生凭据设置/Keychain、CLI 桌面身份交接、安装更新/回滚/卸载、Windows 包、Intel Mac 包、外部 GPU、完整研究、所有可选依赖路径或离线模型能力。Next 页面启动与结果查看均不能代替真实模型和研究验收。

本次在 macOS 26.5.2 arm64 完成实际验证，脱敏记录见 [macos-self-contained-local-test.json](evidence/macos-self-contained-local-test.json)。最终 e 应用约 1.1 GB，35,682 项内容、119 个 Mach-O 的静态加载解析及逐库/根应用签名均通过。f smoke 使用相同 e 应用的搬迁副本；GUI 和单独服务拒读检查均通过，前后清单一致。Python 3.11.15、内置 Node 24.21.0、Electron 44.4.5；真实前端 build ID 为 `MKXVnryUMEMAN19IR_r6s`。17 个桌面 Node 测试、10 个真实文件/clang 测试、4 个文件 strict mypy 通过。

该包以 `42849d949dd9b0b1622699b041eaaa2ebb81c294` 为基准，StateJournal 明确固定至该版本，456 个源文件逐个记录哈希。8 个未提交覆盖文件为本次 desktop 文件和已冻结 LocalRunner/results 变更，精确清单在证据中；它不包含后续研究预算账本或新项目准入增量。d 首次构建后遇到原生库签名失败，e 从同一冻结内容复制并通过构建器相同的 `seal_bundle` 函数重新逐库签名，未重取正在变化的后端源码。完整后续构建器已接入此签名顺序；本次证据准确区分 d 构建与 e 再封装过程。

保留的失败包括 PEP 668、工具对 helper 文件名的解析、Electron 名称导致的错误开发模式、认证中间件清单遗漏、未检查 LC_RPATH、修改 dylib 后的内核拒绝，以及首页实际 HTTP 307 与测试预期冲突。最后一次使用真实 `/projects` 页面检查 HTTP 200。渲染截图确认显示项目页和“代码尚未绑定”提示，未将页面可用描述为研究可执行。macOS 13 仅为包元数据最低版本，尚未在该系统实测。

## 官方依据

- [Electron 手动打包与重命名](https://www.electronjs.org/docs/latest/tutorial/application-distribution)
- [Electron process.resourcesPath](https://www.electronjs.org/docs/latest/api/process)
- [Next custom server 的 standalone 限制](https://nextjs.org/docs/app/guides/custom-server)
- [uv 的 managed Python 来源与发行方式](https://docs.astral.sh/uv/concepts/python-versions/)
