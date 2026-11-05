# VSCode 插件的离线分发方案（只出方案，插件还没开始开发）

前提：公司内网访问不了 VS Code Marketplace，用户没有管理员权限，插件只和本机的 mars-app（`http://127.0.0.1:8020`）通信，不直接 import 内核。

## 1. 在外网电脑上打包

- 插件源码放在 mars-app 下（由 Mars-app 会话维护），用 esbuild 把所有 npm 依赖打进一个 `dist/extension.js`，`.vsix` 里不带 `node_modules`，内网运行时不需要 npm。
- 用 `npx @vscode/vsce package --no-dependencies` 生成 `mars-<版本>.vsix`。`engines.vscode` 按公司内网 VSCode 的实际版本填写，用 `code --version` 查看，不要写得比公司版本新。
- `build/build_bundle.py` 增加一个可选参数 `--vsix <文件>`，把 vsix 放进离线包的 `bundle/vscode/` 目录，并写进 `MANIFEST.json` 做 SHA256 校验。这和其他依赖的处理方式一样，等插件出来再加，现在不加代码。

## 2. 在内网安装（不需要管理员）

```bash
code --install-extension bundle/vscode/mars-0.1.0.vsix
```

也可以在 VSCode 里用"扩展 → … → 从 VSIX 安装"。插件装在用户目录（`%USERPROFILE%\.vscode\extensions`），不需要管理员权限。

建议在用户设置里关掉在线检查，避免 VSCode 反复尝试访问 Marketplace：

```json
{ "extensions.autoUpdate": false, "extensions.autoCheckUpdates": false }
```

更新：离线包里放新的 vsix，执行 `code --install-extension <新 vsix> --force`。

## 3. 设计约束

- `extensionKind: ["ui"]`：插件始终运行在本机。用户通过 Remote-SSH 打开 GPU 服务器上的目录时，插件仍然连接本机的 mars-app，不会在服务器上找 127.0.0.1:8020。
- 只调用 mars-app 的 HTTP API（运行列表、事件 SSE、人工确认、停止）。这和网页走的是同一条路，规则也一致：页面上不能做的事，插件也不能做。
- mars-app 现在没有鉴权，只监听 127.0.0.1。以后 mars-app 加了访问令牌，插件用 VSCode 的 `SecretStorage` 保存令牌，不写进 `settings.json`。
- 不采集遥测，不依赖任何在线服务。
- 只有一台机器、一个用户时，以上方式就够用。以后如果有很多人用，再考虑在内网自建扩展源（例如自建 Open VSX），那需要 IT 部署服务。
