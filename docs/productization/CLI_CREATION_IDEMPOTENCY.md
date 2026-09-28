# CLI 显式幂等保存与只读核对

此增量复用已经部署的同一 API/bridge owner 与创建 catalog，不增加 run 驱动器、本地 catalog、自动启动、重试或新建研究权限。

## 命令与输出

```sh
mars project --server http://127.0.0.1:8010 create \
  --contract /absolute/frozen-task.json --name "比较候选与基线" \
  --request-id YOUR_RETAINED_REQUEST_ID

mars project --server http://127.0.0.1:8010 request-status YOUR_RETAINED_REQUEST_ID \
  --task-sha256 YOUR_FROZEN_TASK_SHA256
```

端口须使用实际后端 origin。用户在发送前保留一个非秘密稳定 ID（8–128 位 ASCII 字母/数字/`_`/`-`，首字符字母或数字）及冻结任务哈希。CLI 不自动生成、替换或重发 ID。未提供 `--request-id` 时仍兼容旧保存方式，回执为 `request_id:null`、`idempotent:false`；它不具备重复请求去重保障。

`RuntimeClient.create_research_run()` 仅在显式提供 ID 时发送该字段；`creation_status()` 只发 GET。`project request-status` 强制显式 `--server`，不会读取项目配置、冻结文件或 live 源码来猜测结果。网络 origin、会话凭据、字节限制、deadline、无代理/重定向/自动重试策略沿用原客户端。

CLI 保留 `http_status`、`http_ok`、后端 `response`，另提供：

| 字段 | 含义 |
| --- | --- |
| `creation_confirmed` | 只有真实 created 回执的 request ID、合同哈希、run ID、`research_started:false`、幂等标记和必要形状一致才为 true。它确认保存，不确认研究执行或科学结果。 |
| `contract_match` | create 始终核对实际读取的冻结任务哈希；lookup 带 `--task-sha256` 时核对用户保留的哈希。匹配为 true；未提供预期哈希，或 unknown 尚无登记哈希时为 null。 |

保存确认或 created 查询退出 0。pending（含 HTTP 202）、unknown（含 HTTP 200）、rejected、404、401、冲突等退出 2；协议/身份不一致返回安全 `creation_response_mismatch`，不能把 HTTP 成功当保存成功。查询不带预期哈希也可以确认所选 ID 的真实创建记录，但不会声称它匹配某个本地合同。

发生超时、断连、响应过大或协议不一致时，固定提示建议使用原 ID 的 `request-status`；不输出请求体、源码、HTTP exception 或自动重发命令。明确 rejected 才证明分配前拒绝：用户修复输入、重新冻结后可以明确用新 ID。unknown 或 404 都不提供自动换 ID 的依据。端到端崩溃边界仍以 [创建保存协议](RESEARCH_CREATION_IDEMPOTENCY.md) 为准。

## 会话与资料边界

token 仍只来自 `MARS_DESKTOP_SESSION_TOKEN` 进程环境，不增加 token 命令行参数或凭据文件。request ID 是会在 URL/回执出现的公开关联字段；若用户误把当前会话 token 或其前 12 个字符放入 ID，客户端在请求前固定报错，不将其作为 catalog key 保存。

已有安全 argparse 提示、通用本地校验错误信息及 HTTP 回执 token redaction 保持生效。合同 JSON 只在原输入文件读取；不复制源文件、不把合同完整内容写入保存/核对回执。`request-status` 不做 live preflight，所以源目录或本地合同已移动/删除仍可查询真实记录。

## 验证

`backend/tests/unit/test_cli_runtime_client.py` 全模块 **79 passed / 22.19 s**；本批新增 9 个真实 CLI/HTTP 场景计数（含参数化），6 个纯解析拒绝用例。strict mypy 3 文件通过。

真实场景使用 production uvicorn、独立 `python -m app.cli` 子进程、真实 SQLite 与文件：显式 ID 保存和重放、不同 body 冲突、删除原源码/合同后 lookup、预期哈希不匹配、真实 pending lease 与子进程 SIGKILL 后 unknown、明确 rejected 后人工新 ID、真实 401/404、无 server 拒绝、token/前缀/路径误填拒绝。另以真实 SQLite writer 暂时持锁和仅该 CLI 的 0.05 s YAML deadline 造成 HTTP timeout，确认客户端退出非零，后端完成原保存，后续显式 GET 查回同一 run。

纯解析用例的 authored receipt 仅是函数输入，拒绝 ID/hash/started/idempotent/envelope/run ID 不一致，不作为服务、模型或实验成功证据。

保留中间失败：首轮 72 passed / 1 failed，新短 deadline fixture 只复制 configs，被原 Settings 的完整 runtime 资源校验拒绝；修复为复制真实 runtime assets，没有放松校验。随后 strict 首次指出测试 payload 推断为 `Collection[str]` 的两项类型错误，补明确测试字典类型后通过。最终日志位于 ignored `release-evidence/productization/20260928/cli-idempotency.pytest.log` 与 `cli-idempotency.mypy.log`。

本批不声称 Windows/macOS 安装包验收，不接管旧 StaticPIMC CLI 的运行模型，也不解除合同研究 start 的现有阻断。
