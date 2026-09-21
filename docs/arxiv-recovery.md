# arXiv 406 恢复

`search.arxiv_search` 优先访问官方 Atom API。API 返回 HTTP 406 时，在同一次工具调用内限时访问官方 HTTPS 搜索页或摘要页，保留原始网页与 SHA-256。不会关闭证书校验、伪装浏览器身份或把网络失败当成空结果。

- 普通关键词：使用官网相关性排序，保留原查询。返回的 `metadata_format=arxiv_html`、`api_status=406` 和 `warnings` 明确说明回退及排序差异。
- 精确编号：读取官方摘要页的 citation 元数据和明确版本 URL，再校验请求的论文身份及版本。不同论文、版本不符和缺少关键元数据会失败。
- 分类、日期、非相关性排序以及 API 字段/布尔表达式不会被悄悄丢弃。API 不可用且请求含这些约束时，返回明确失败；调用方可另发普通关键词请求。
- 页面结构无法识别、网页截断或错误页面不能变成“成功、零篇论文”。Atom 错误 feed 同样返回失败。
- API 与回退共享 55 秒总预算；请求起始间隔至少 3 秒，单次 HTTP 超时 20 秒，单页 HTML 上限 2 MiB。多个编号超出总预算时整次调用失败，可分成较小批次重试。
- HTML 缓存复用会核对原始网页哈希、重建元数据并比较命中结果。旧缓存与新格式分开。

本次真实核验（2026-09-21）：

1. `speeding up convolutional neural networks CP-decomposition` 在 API 406 后，经官方搜索页返回 `1412.6553`，题为 *Speeding-up Convolutional Neural Networks Using Fine-tuned CP-Decomposition*。
2. `arxiv_ids=["1812.03655"]` 在 API 406 后，经官方摘要页返回 `1812.03655v1`，题为 *Digital Cancellation of Passive Intermodulation in FDD Transceivers*。
3. 两种请求均完成真实工具调用、原始网页存档及缓存重建验证。

以上是文献元数据检索验证，不代表读完论文、完成 Idea 方案或通过后续实验。此前 run 的失败日志保留原状；更新并重启后端后，新调用使用修复。实际调用记录以本机验证目录或对应 run 的工具记录为准。

接口格式参考：[arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html)。
