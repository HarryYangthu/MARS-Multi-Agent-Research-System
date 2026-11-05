# 内网模型：思考参数与流式输出的兼容性

内核按角色调用模型。网页为 `strong`、`fast`、`coding` 分别保存服务地址、模型和Key；三者独立，修改一个角色不影响其他角色。`coding` 在内核配置中名为 `coder`。本包 strong/coding 默认使用MAAS的GLM-V5.2，可选GLM-V5；fast默认使用SH的model1，可选model2。按用户要求，coding初始Key与strong相同，但分别保存在 `MARS_STRONG_API_KEY`、`MARS_FAST_API_KEY`、`MARS_CODER_API_KEY` 中。地址、模型ID和兼容参数写在 `local/models.yaml`，Key写在 `local/models.env`（模板：[`templates/models.intranet.yaml`](../templates/models.intranet.yaml)）。以内网连接检查的实测结果为准。

修改后先点“保存配置”，再分别测试角色。基础连接出现鉴权、地址、限流或网关错误时，检查会立即显示该错误，后续能力探测不再重复请求。

## 1. 流式输出

| 情况 | 内核怎么做 | 配置 |
|---|---|---|
| 网关支持 `stream: true` | 流式调用；`idle_timeout` 限制两次输出之间的静默时间，`timeout` 限制总时长 | 默认，不用写 |
| 仅非流式可用的部署 | 每次只发一个非流式请求，总时长受 `timeout` 限制，没有静默检测。所以 `timeout` 要按"排队时间加完整生成时间"来设 | `stream: false` |
| CodingAgent（coder） | CodingAgent 总是用流式请求和原生工具调用。`stream: false` 时，本机 LiteLLM 网关向上游发非流式请求（经内核的非流式路径，兼容 JSON 或 SSE 文本响应），再把完整回答（文本、工具调用、用量）按流式分块回放给 CodingAgent。凭据只放在 Authorization 头里 | 同上，coder 也写 `stream: false` |
| 网关只接受单条消息 | 有的网关不接受多条 user/system 消息（老 MARS-Product 的 `commander_message_format: single_user`） | 角色里写 `single_user: true`，内核会把多轮合并成一条 user 消息。内网 Qwen 若有此限制再打开 |
| 网关对非流式请求返回 SSE（`data: ...`） | MARS-Product 在公司 GLM 网关上遇到过 | **内核已兼容**（mars-core b1b640f）：`stream: false` 时内核绕开 LiteLLM，直接请求 `<api_base>/chat/completions`，响应体是 JSON 就按 JSON 解析，是 SSE 文本就把各 `data` 块拼起来（正文、`reasoning_content`、按 index 合并的 tool_calls、usage）。`check_models` 的 `raw.sse_body_for_non_stream` 仍会标出这种格式；非流式检查现在复用内核兼容路径。若基础流式调用通过，后续能力检查默认优先采用流式 |

非流式回放已用真实请求验证过（智谱云 glm-5.2）：普通文本、流式分块、流式工具调用都正确，CodingAgent 端到端也能完成编辑（见 `proposals/mars-core.md` 第 2 节）。

## 2. 思考（thinking）开关

| 部署 | 打开/关闭思考的方式 | models.yaml 写法 |
|---|---|---|
| 智谱云（本机开发用） | 请求体 `thinking: {type: enabled/disabled}` | `thinking: true/false` |
| vLLM / SGLang 自部署 GLM-4.5 及以后、GLM-5.x | 聊天模板参数 `chat_template_kwargs.enable_thinking` | `extra_body: {chat_template_kwargs: {enable_thinking: true}}` |
| vLLM / SGLang 自部署 Qwen3 系列（含 27B 档） | 同上；官方模板默认开启长思考，高频的 fast 角色建议关掉 | `extra_body: {chat_template_kwargs: {enable_thinking: false}}` |
| 公司统一网关（one-api、new-api 等） | 可能会过滤或拒绝不认识的字段 | 以 `check_models` 的 `thinking` 项为准 |
| 始终思考、不能关闭的模型 | 发送 "disabled" 可能会被拒绝 | 不写 `thinking`，不写 `enable_thinking` |

`thinking` 和 `extra_body` 可以同时写，内核会合并。`reasoning_effort` 只有少数部署认，不确定时不要写。

思考内容有三种返回形式，内核都能处理：放在 `reasoning_content` 字段（vLLM 启动时带了 `--reasoning-parser`）；放在 `reasoning` 字段（新版 vLLM，LiteLLM 会映射回 `reasoning_content`）；以 `<think>…</think>` 夹在正文里（没有 reasoning parser，内核用 `strip_thinking` 去掉）。思考也占输出长度，`max_tokens` 不要设得太小，否则结构化输出会在 JSON 写完之前被截断。截断会报告 `finish_reason=length`；完整回复的 JSON 不合规时，内核最多修复两次。

`check_models` 的 `thinking` 项采用基础检查选定的同一调用方式，以同一道题分别测试“不传、`thinking.type=enabled/disabled`、`enable_thinking=true/false`”五种请求。每次移除其他思考开关，保留无关的服务参数，避免互相冲突。

请求成功只表示该请求被接受，不证明开关有效。只有同一参数格式的开启/关闭两项都完整成功，且开启时有可见思考、关闭时没有，才报告 `observed_disabled` 并提供建议；其他情况为 `inconclusive` 或 `not_checked`。一次观察不证明所有输入的内部推理都能关闭。

## 3. JSON 模式与工具调用

- 内核的结构化输出会发 `response_format: {type: json_object}`，然后自己校验，不合格就让模型修复。如果网关拒绝这个参数，在角色里写 `json_mode: false`，校验和修复照常进行。
- coder 必须支持**原生工具调用**。检查会按 index 拼接分片中的函数名和 JSON 参数，允许正文为空；核对 `add(2, 3)` 的参数后真实计算 5，将结果作为 tool 消息回传，并保留上轮实际返回的 `reasoning_content` 和 tool call ID，确认模型返回最终答案 5。`tools.native_tool_call`、`arguments_valid` 和 `round_trip.ok` 分别记录这些阶段。CodingAgent 不能用纯文本的工具协议。
- CodingAgent 侧的上下文窗口目前写死为 128000（`mars/codingagent.py` 的 `provider_config`）。如果内网部署的上下文更小，需要内核把它改成可配置的值。

## 4. 检查步骤

```bash
python checks/check_models.py              # 每个角色最多约 12 次短请求，报告保存在 local/checks/models-*.json
python checks/check_models.py --role strong --transport stream --max-tokens 4096  # 明确验证流式强模型
python checks/check_gateway.py             # coder 经本机网关：非流式、流式、流式工具调用、错误令牌
python checks/check_codingagent.py --live        # CodingAgent 在沙箱里经网关完成一次真实编辑
```

前两步失败时，先看报告里的 `error`：401 是 Key 的问题，404 通常是模型 ID 或 `/v1` 路径不对，429 是限流，超时可能是排队太久（调大 `timeout`）。


## 5. 流式能力检查与角色验收（report_version: 2）

- 先做直接 HTTP、非流式及流式基础请求；HTTP 200 只表示接口响应，不能证明模型或研究任务成功。
- `--transport auto` 优先选择已通过的流式通道，流式不通时才采用已通过的非流式；`--transport stream` 强制能力项走流式。如果指定通道的基础调用失败，JSON/工具等标记为“未检测”，不判定为模型不支持。
- JSON、工具调用、思考开关均显示实际 `transport`。JSON 校验使用完整回复，不再解析回复尾部；截断、只有思考没有正文、上游错误或缺少终止分片都不作为成功。
- 流式聚合复用 `mars.nostream.read_stream/assemble`，与内核使用相同的分片解析、usage、静默超时和总时长限制。非流式检查复用 `complete` 的 JSON/SSE 兼容路径。
- `json` 检查原生 `response_format=json_object`。未通过时建议 `json_mode: false`，然后 `structured` 使用所选通道真实调用 `LLM.structured`，要求 `answer=pong`，检查提示词输出加 schema 校验是否可用。该项禁用角色降级和重试/修复，不允许 fast 的成功代替 strong。
- strong/fast 的 `ready` 要求 `structured.ok`；coder 要求真实工具结果回传成功。基础 pong 成功不再单独使角色验收通过。通过这些短请求也不等于长上下文、多轮研究或科研结果已验收。
- 网页可应用 `stream: true/false`、`json_mode: true/false` 建议；仍需点击“保存配置”才生效，不自动改写其他角色或凭据。

公司内网复测必须在可访问该网关、已配置该角色 Key 的环境运行。外网兼容性回归不能代替公司网关的真实验收。
