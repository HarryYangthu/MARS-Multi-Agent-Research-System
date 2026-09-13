# Idea Agent 真实运行验证（2026-09-14）

## 结论

默认 focused_v1 已经由正常 API → Bridge 路径完成真实检索、源码阅读、方案生成、不同模型评审和结构化交付。最终 Idea 节点为 `waiting_review`，等待人查看和批准；没有启动训练或后续 Agent。

本次方案是在现有 StaticPIMC 的复增益 LUT 上增加可选的节点学习：把有限节点分配到更需要分辨率的幅度区间，默认仍保留现有模型。先与同规模基线比较，再判断是否有收益。

这是一次**修订验证**：输入包含 `2026-09-13T1630_pimc_lut_` 的未通过候选与评审意见，作为未验收的已有分析。没有使用 `seed_artifact` 绕过生成，没有修改旧运行，没有把该结果计为全新任务的一次成功。

## 实际执行证据

| 项目 | 结果 |
| --- | --- |
| 运行 | `2026-09-13T1645_pimc_lut_` |
| 执行时间 | 2026-09-14 00:45:45—01:06:23（北京时间），约 20 分 38 秒 |
| 生成 / 评审 | DeepSeek `deepseek-v4-pro` / `deepseek-v4-flash`，独立会话 |
| 实际模型请求 / 工具调用 | 16 / 16 |
| 有效评审 | 3 轮，先后修订后通过；另有一次评审 JSON 格式修复 |
| 结构与来源修复 | 1 次；旧运行来源编号与无读取记录的 URL 被拒绝 |
| 背景加载 | 审计确认完整 PIMC Markdown 正文出现在每次真实模型输入中，包括评审 |
| 输出 | `proposal.v1`、内嵌交接字段、`idea.research_handoff.v2`、中文方案与研究说明 |
| 代码检查 | 最新实现 `ae986806237f81f0c9d55240711ffee91682b75b` 的 GitHub CI 全部通过：后端测试、严格类型检查、依赖方向、前端类型检查与构建、Windows 脚本 |
| 本地测试 | 原生工具支持后全量 2016 通过 / 158 跳过；后续修订输入新增 2 项测试，与 focused 相关共 12 项通过 |
| 工作台 | 实际打开最终方案；显示有效 schema、完整方法、选文依据与保存原文链接；两份原件接口均 HTTP 200 且内容哈希匹配 |

运行目录保留初稿、每轮评审意见、完整公开调用记录和最终导出。模型私有推理内容不保存。

## 本轮调研

检索记录返回 8 个不同链接，方案明确记录 3 项候选决策，采用 2 篇。此数量是本次执行结果，不是固定停止条件。

| 文献 | 判断与实际用途 | 阅读边界 |
| --- | --- | --- |
| KAN: Kolmogorov-Arnold Networks | 采用样条表示、网格调整和初始化保持函数的思路，约束 LUT 节点设计 | 实际读取窗口覆盖第 1—11 页，采用 §2.2、§2.4；不声称全文逐页读完 |
| Neural Spline Flows | 采用有序节点与内部自由度构成可微样条模块的框架 | 实际窗口第 1—4 页，采用 §3.1；§3.2 未读，不作为具体节点参数化依据 |
| Learning Activation Functions in Deep (Spline) Neural Networks | 待补充；正文获取失败，不作为方法证据 | IEEE 返回 HTTP 418 |

两篇采用文献的 PDF 已归档，下载分别约 6 秒、10 秒；后续 KAN 页段从缓存续读。arXiv 检索发生限流，模型分析与修订占主要耗时。当前 20 分钟的单次耗时仍有优化空间。

## 公式核验与下游边界

对最终方案相同的 `method_spec` 做了额外 float32 数值核验：K=2/16/32，均匀、随机与极端节点参数共 9 组，在显式固定首尾为 0/1 时，帽基单位分解最大误差约 `1.19e-7`。当前 K=16 的均匀初始化增益相对误差约 `2.72e-7`，满足方案的 `1e-5` 条件。

检查同时暴露了需要交接的边界，不能把模型通过评审等同于已实现或已验证收益：

- 首尾节点必须显式固定；仅依赖 float32 累加和得到 1 会产生端点偏差。
- K=32 的均匀初始化相对误差约 `1.43e-5`，没有通过 K=16 的容差，改变 K 时需重新校验精度。
- 评审已提示检查 `L3.knots` 的可视化调用方，保留派生节点访问接口，并限制 `u_max` 的适用范围。
- 候选将 NSF 的有理二次样条写成“k=3 光滑”不准确；它与三次 B-spline 不应混用阶数表述。本方案不迁移 NSF 的有理二次公式，原文和该提示均保留供复核。
- 未运行 PIMC 训练、梯度验证、真实数据比较或泛化实验；结果是可尝试的研究方案。

机器审计：`runs/<run_id>/idea/focused_verification.json`。公式核验：同目录 `numerical_sanity_2e590e8e9ffa403fbcf0704858dc5075.json`。最终方案 SHA-256：`bbe7dbb43647cb727a6487640222e333d2ba8a8a595eaf7e6e6308b93e0fde62`。运行材料保存在本地，不随源码提交。

## 复现与维护

维护 [`PIMC 知识文件`](../projects/pimc/context/public_context.md)，新任务自动加载全文；同一任务恢复保持原快照。目的、功能、架构和配置参见 [`工作流说明`](idea-agent-usable-workflow.md)。

```bash
PYTHONPATH=backend .venv/bin/python scripts/check_idea_models.py
.venv/bin/python scripts/verify_focused_idea.py start
# 复现本轮的修订方式：
.venv/bin/python scripts/verify_focused_idea.py start --revise-run 2026-09-13T1630_pimc_lut_
PYTHONPATH=backend .venv/bin/python scripts/audit_focused_idea.py 2026-09-13T1645_pimc_lut_
```

服务需启用真实网络工具并配置两个可用模型。当前不需要新增 LangChain 或迁移 Idea 执行器：现有工具、记录、恢复、评审与 Bridge 编排已经覆盖本阶段路径。
