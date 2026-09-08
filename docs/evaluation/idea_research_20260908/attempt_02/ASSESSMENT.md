# 第二轮真实 Idea 研究独立验收

本轮失败，未产生合格研究包或可交付的 Idea 方案。43 次实际模型请求均返回；35 次工具分派包括父层委派/Memory及子层研究工具。总计 651,290 tokens，耗时 1,147.52 秒，四份父子 trace 一致。未执行独立模型方案审查或真实仿真。

| 统计口径 | 实际数量 | 含义 |
|---|---:|---|
| 去重检索来源 | 65 | 有真实元数据，不代表相关、已读或采纳 |
| 实际下载不同 PDF | 5 | 文件与读取凭据保留在原 run |
| 有可见页段的不同论文 | 5 | 共 8 个成功读取窗口，不代表全文理解 |
| 已完整通过 dossier 验收的报告 | 0 | 不等于没有读取论文 |
| 最终接受的文献提取数 | 0 | 报告整体失败；两份失败稿各有 1 篇摘录凭据通过，但未达到每份 2 篇要求 |
| Idea 下游交付 | 0 | 最终停止原因为父循环 protocol_exhausted |

| 子任务 | 模型 / 工具 | 终态与实际问题 |
|---|---:|---|
| 50b4f21ee4564ff498f91a16628c91e0 | 12 / 10 | validation_exhausted。节点放置论文读取成功，修正逐字摘录后只剩 1 篇通过；Instant-NGP 超过本轮 12 MiB 限制，两次下载失败。 |
| 5634b15ad474460da1fbf09072a308db | 11 / 10 | validation_exhausted。实际成功读取 3 篇及追加页段，但最终稿使用不存在的回执、page=0、defer 来源仍提取等错误；最后宣称缺少回执，不能替代真实工具记录。 |
| 527b962fd6104392b2d2d5406577c92a | 11 / 10 | validation_exhausted。ICELUT 读取成功且有摘录通过；Instant-NGP 超过 12 MiB 下载失败；仅 1 篇满足提取条件。 |
| Idea 主 Agent | 9 / 5 | 两次候选校验失败、3次协议修复记录，最终 protocol_exhausted。没有收到合格委派报告，仍尝试输出候选。 |

实际读取论文及窗口：

| 论文 | arXiv ID | 可见页窗口 |
|---|---|---|
| A Deep Neural Network for Knot Placement in B-spline Approximation | 2205.02978 | 1–4 |
| Interpolating amplitudes | 2412.09534 | 1–6；2–8；10–17 |
| Comparing machine learning and interpolation methods for loop-level calculations | 2111.14788 | 1–6；2–8 |
| Radial basis function approximations: comparison and applications | 1806.07705 | 1–6 |
| Taming Lookup Tables for Efficient Image Retouching | 2403.19238 | 1–4 |

Instant neural graphics primitives with a multiresolution hash encoding（2201.05989）检索到了，但本轮三次下载都被大小限制拒绝，不能计入已读。后续 32 MiB 配置及隔离预检不属于本轮结果。

主 Agent 最后保留的候选用人话概括：将 16×16 均匀双线性网格换成可训练非均匀 15×15 张量积网格，让节点向复杂区域集中。该方向是未验证假设。原稿声明 251 个可训练参数，但实际声明 225 个表值加 28 个间隙参数，共 **253**；归一化后有效自由度少 2 不等于少训练 2 个参数。程序成功拦截该错误。训练与留出数据又引用同一个 canonical spec_ref；研究链接指向不存在的已接受 insight，材料与交付校验均未通过。

原始候选见 `agent_traces/idea/612a400c539546538a263ac664e4aafc/candidate.md`，未由程序补写或修正。三份研究候选保存在各子 Agent 的同名文件，可检查逐篇选文理由及未通过的提取内容。

独立上下文检查发现：子任务 5634… 第 6/7 次真实模型请求包含实际 read_receipt 路径，第 8–11 次输出/修订输入已不包含这些实际路径。结合真实工具成功，说明压缩后缺少凭据是具体工程风险，不能单纯归咎于没有下载或通过增加模型调用次数解决。此检查只追踪路径可见性，不证明压缩是每项模型错误的唯一原因。

`summary.json` 中 `unique_publications_with_verified_extractions=0` 按已完整接受报告计数，范围比“某失败稿已有单条引用通过”更窄。必须同时报告 5 篇实际读取，避免误导。

本目录保留输入、终态 summary/audit/findings/review、所有父子 checkpoint、委派 request/failure。完整事件、工具原始结果与读取凭据保留在原 run，将随完整运行归档，避免在 Git 重复。`candidate.md` 为 checkpoint 的 candidate 字符串原样导出；`archive_manifest.json` 记录文件 SHA256。PDF 原文保留原 run，未重复入 Git；没有修改原运行或模型输出。

## 论文技术内容独立核对

以下是测试结束后审阅者对原 run PDF 的核对，未传给 Agent，也不改变失败结果。

| 论文 | 原文核对 | 对模型提取的判断 |
|---|---|---|
| Knot Placement（页2–4） | 原文确实定义自由节点非凸拟合问题，固定节点后求线性最小二乘，并用 Softmax 处理节点间隔。 | 主要方法提取有依据；人话总结的“证明可训练节点优于均匀网格”过强，应限定为论文实验与问题条件。向二维双线性迁移是新假设，原文不证明固定256预算下的收益。 |
| ICELUT（页2–3） | 原文确有 MSB/LSB 双支路及分组 FC 转 LUT 的存储讨论。 | 两段摘录真实，但将此直接称作“FC权重存储压缩”不准确：指数成本来自查找表地址/转换后的存储。原文对 L/K 的文字定义与指数式也存在需解释的对应关系，不应照抄后当作已核验的通用计数。低秩二维节点分解、局部细化均是模型另提假设。 |
| Comparing ML and interpolation（页5–6） | 原文给出规则网格插值、IDW 与RBF展开，RBF中心在已知数据点处；并非本文直接优化可训练中心。 | 原始方法确实可用于比较替代插值。但最终模型报告无有效摘录，不能把审阅者找到的公式算作Agent提取成功；“学习中心”是额外设计。 |
| Radial Basis Function Approximations（页1–3） | 原文讨论散点逼近、紧支撑/全局RBF、形状参数、线性系统与病态风险；PDF字符编码有明显噪声。 | 与散点/参数预算问题有间接相关性，但无最终合格模型提取。编码异常意味着公式需进一步核对，不能仅据纯文本保证定义准确。 |
| Interpolating amplitudes（页15–17） | 实際窗口含按维度调节点数的自适应方法；原文指出张量积结构本身不能作任意局部细化。 | 可以启发维度预算分配；不能直接作为“局部高曲率区域任意细化”的依据。未实际返回的后续稀疏网格页也不能算读过。 |

结论：五篇中两篇失败研究稿有实质且大体相关的提取，但仍有过强结论和成本语义问题；其余三篇虽真实读到内容，没有完成有效提取交付。来源真实与逐字摘录匹配都不能替代迁移有效性审查。
