# 桌面运行架构决定

日期：2026-09-28。工作包：A。结论：保留 Electron 路线，A 的已验收记录仍是 macOS Apple Silicon 源码环境生产页面启动/退出验证。I 的自包含本地测试 `.app` 增量与独立证据见 [MACOS_TEST_BUNDLE.md](MACOS_TEST_BUNDLE.md)；**未达到正式安装与产品发布验收**。

## 已实施边界

- Electron 主进程管理 Python API、Next.js 生产页面服务器和同源代理。Python 与 Node 服务器直接绑定 `127.0.0.1:0`；没有先寻找端口再释放的占用竞态，也不复用未知服务。
- 代理按固定路径转发 `/api/`、`/ws/` 和 `/health`，其余到前端。路径检查先于 URL 归一化：拒绝编码服务前缀、编码斜杠/反斜杠、嵌套转义、点段、大小写歧义和非法百分号编码；普通编码 ID 与 query 参数可用。每个入口验证随机会话令牌；代理额外验证 Host、Origin、Fetch Metadata。令牌由主进程只对准确桌面来源附加，渲染 JavaScript 不接触令牌。前端验证后清除 normalized/raw/distinct 请求头中的令牌，再调用 Next；同时从其后续运行环境中删除令牌，避免旧构建中的外部 rewrite 转发该凭据。
- 渲染器启用 sandbox、contextIsolation 和 webSecurity，禁用 Node/WebView，拒绝权限、弹窗和外部导航。此阶段没有 preload 或 IPC，所以没有通用文件、命令、URL 打开桥。
- 工作空间从精确运行资源白名单初始化，不复制源码 `.env`、运行历史、真实研究目录。原代码安装路径和可写工作空间分离；资源仍存于可写工作空间是过渡设计，正式版本还需细分只读资源、配置与数据路径。
- 服务子进程使用最小环境。主进程仅写受控生命周期回执，任意 provider/工具输出不进入回执，错误时报告失败阶段。未来需补充可诊断且脱敏的服务日志。
- 关闭应用清理自己启动的服务进程组；不按端口查杀进程。macOS 已实测正常退出和强制结束忽略 SIGTERM 的实际子进程。Windows 分支、主进程被 SIGKILL 后的孤儿恢复以及长训练独立管理仍需验收。

## 依赖选择与兼容性

`electron@44.4.5` 提供桌面主进程与隔离 Chromium；`yaml@2.9.1` 读取显式行为配置，二者精确锁定。新增运行配置位于 `desktop/config.yaml`。

Next.js 使用官方 custom-server API 加载既有生产构建，当前需要 `frontend/node_modules` 和 `.next`。它没有把 `next dev` 包装为桌面应用。Next 官方文档说明 standalone 不追踪 custom-server 文件，因此不能将此运行方式与现有 standalone 目录直接等同；正式发行阶段必须选择并验证完整打包方案。

安全设置依据 [Electron 安全规范](https://www.electronjs.org/docs/latest/tutorial/security)；主进程/渲染器职责依据 [Electron 进程模型](https://www.electronjs.org/docs/latest/tutorial/process-model)；单实例使用 [app.requestSingleInstanceLock](https://www.electronjs.org/docs/latest/api/app#apprequestsingleinstancelockadditionaldata)。构建边界依据 [Next.js custom server](https://nextjs.org/docs/app/guides/custom-server) 与 [Electron 打包指南](https://www.electronjs.org/docs/latest/tutorial/tutorial-packaging)。

## 验证与剩余门槛

本阶段命令与可执行入口见 `deploy/desktop/README.md`。桌面 unit tests 使用实际子进程、临时文件与真实 HTTP 监听器验证生命周期/访问拒绝，不伪造模型、MARS 服务或实验成功。Electron smoke 使用实际 Python API 和真实前端页面；其 JSON/截图保存在指定数据目录，公开证据汇总由产品化证据索引维护。

已保存脱敏回执 `evidence/desktop-smoke-attempts.json` 与 `evidence/desktop-second-instance.json`，保留失败尝试及修复后重验，不拼接为发布成功。最终 attempt g 的前端构建为 `CBgrP4a2MxiZhv7EnkQLh`，覆盖真实页面、API、WebSocket、认证拒绝和正常退出；退出后再次核对本次两个服务 PID 均不存在。attempt e 另验证第二实例不重复启动服务及无关监听器存活。attempt f 保留为硬化中间版本证据，最终 g 增加全部桌面运行文件 SHA256。最终截图哈希随回执保存，截图本体留在本地验证目录。桌面 14 项测试通过；桌面 npm 依赖官方源审计报告 0 个漏洞，此结论不覆盖后端/前端或操作系统依赖。

硬化时新增的真实 HTTP 头测试曾发现 Node `headersDistinct` 的延迟缓存与删除 raw header 对不兼容，第一次测试未通过并停止了该测试进程；修复为先清除 distinct 缓存再缩短 raw headers 后，完整 14 项重验通过。该失败与重验见 `evidence/desktop-security-checks.json`，不计入通过次数。

必须继续完成平台安装、完整资源及许可审查、凭据存储、升级回滚与卸载策略、持久训练任务管理、崩溃恢复和跨平台验收。内置运行依赖的后续 I 切片另有检查与真实启动边界，不能追溯扩充上述 A 回执。CSP 为兼容当前 Next hydration 保留 inline script/style；nonce/hash CSP 收紧和所有页面功能回归尚未完成。现有 UI 没有因此满足工作包 G 的导航/布局/交互要求。来源查看、编辑器远程资源等被网络策略阻断的能力必须改成本地受控资源并验收，不能为此放开任意远程内容。
