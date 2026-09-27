# 桌面启动验证

这里提供工作包 A 的源码环境桌面验证入口。它启动现有 Next.js **生产构建**和真实 Python API，并管理其退出；目前仍需开发者准备 Node 22.12+ / Python 依赖，不是可分发安装包。

从仓库根目录执行：

```sh
npm ci --prefix frontend
npm run build --prefix frontend
npm ci --prefix desktop
npm test --prefix desktop
# Python 必须是已安装本仓库后端依赖的真实环境；默认读取仓库 .venv。
MARS_DESKTOP_PYTHON=/absolute/path/to/python npm start --prefix desktop
```

生产前端应在未设置 `NEXT_PUBLIC_BACKEND_URL`、`NEXT_PUBLIC_WS_URL` 的环境构建，浏览器请求使用桌面同源入口。Python 不会从源码目录读取 `.env`；模型凭据也不会从启动 shell 自动传给子进程。首次启动按 `scripts/release/runtime_assets.txt` 精确白名单生成应用数据目录中的 `workspace/`。后续启动保留其用户修改。白名单源资源变化时，当前验证入口拒绝覆盖并要求使用新的开发数据目录；正式升级迁移尚未实现。

可用 `MARS_DESKTOP_DATA_DIR=/absolute/empty/directory` 为一次验证选择隔离目录。该目录含应用浏览器缓存、`workspace/` 和不含会话令牌的 `desktop-lifecycle.json`。不应提交或发行用户目录。

```sh
MARS_DESKTOP_PYTHON=/absolute/path/to/python \
MARS_DESKTOP_DATA_DIR=/absolute/empty/smoke-directory \
npm run smoke --prefix desktop
```

`smoke` 打开真实窗口并检查：实际后端健康、全部入口的未认证拒绝、跨站拒绝、渲染页面无 Node、真实项目 API、WebSocket 连接，然后保存截图并正常退出。它不会创建研究任务或调用模型，不能代替研究闭环验收。

安全边界：三处监听均绑定 `127.0.0.1` 的动态端口；会话令牌每次启动随机生成，只在进程环境和请求头传输；渲染器没有 preload/IPC、Node、WebView、系统权限或远程导航。子进程不继承模型密钥和 Node 注入变量，任意子进程日志不直接转录，回执只记录生命周期字段。原始服务输出诊断、日志轮转与升级仍待后续实施。

macOS/Linux 退出只对本次启动的进程组发信号，超时升级为 SIGKILL；Windows 实现使用本次仍存活子进程的 `taskkill /T`，必须实机验证进程树和异常退出。独立长训练生命周期、远端任务重联不属于本验证入口的已验收范围。

后续发行必须补齐：离线 Python 与前端运行依赖、精确包内容审计、平台凭据存储、安装升级/回滚/卸载、Windows 实机、签名、公证、许可证与研究全流程。当前程序在 `app.isPackaged` 为真时明确拒绝启动，防止把不完整壳误称为正式应用。
