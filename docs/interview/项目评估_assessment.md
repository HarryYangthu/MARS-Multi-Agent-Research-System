# MARS 项目评估 / Project Assessment(中英双语)

---

## 一、一页速览 / At a glance

| 维度 Dimension | 数据 / Fact |
|---|---|
| 代码规模 Code size | 后端 12.7k 行 Python(125 文件)+ 前端 4.7k 行 TS(21 文件)≈ **17.5k 行** |
| 测试 Tests | 44 测试文件,200+ 测试;schema 合规率 ≥95%,baseline 召回≥80%/精度≥90% |
| 静态检查 Static | `mypy --strict` 零错误;`import-linter` 4 条架构 contract CI 强制 |
| Agent | 5 领域 Agent + 1 对话主控(Commander) |
| 治理机制 Governance | 6 种 Schema、5 个 HITL Gate、4 区知识库、自愈反馈环路 |
| LLM backends | Anthropic / OpenAI / Qwen / Gemini / DeepSeek / 本地 vLLM / Mock |
| 真实数据 Real data | 自有 PIMC 代码 139 chunks + 20 篇 PIM 论文 517 chunks(共 852 chunks) |
| 真实物理 Real physics | 双载波 PIM 对消仿真(QR 正交化,RES 随记忆深度真实变化) |

---

## 二、水平定级 / Level rating

**综合评级:准生产级研究 Agent 平台 MVP(强个人项目上限)**
**Near-production MVP of a research-agent platform (top-tier personal project)**

按维度打分(满分 5):

| 维度 | 分 | 说明 |
|---|---|---|
| 系统架构 System architecture | ★★★★★ | 5 层依赖单向 + CI 强制 + 依赖反转,平台级抽象 |
| LLM / Agent 工程 | ★★★★☆ | 多 Agent 编排、debate、function-calling-ReAct、多 backend、mock 兜底 |
| 机制创新 Mechanism novelty | ★★★★☆ | Schema 脊柱 / 切面 Gate / 自愈环路 / 双层 FSM,系统级原创 |
| 工程纪律 Eng. discipline | ★★★★★ | mypy strict / import-linter / 200 测试 / 完整沉淀 |
| 领域深度 Domain depth | ★★★★☆ | 真实 PIM 物理 + 真实代码/论文,但完整 7 层模型未上 GPU 训练 |
| 产品/前端 Product/FE | ★★★★☆ | 三栏指挥台 + 实时曲线 + i18n,P0 完整 |
| 完成度 Completeness | ★★★☆☆ | V0 单用户;posttrain 占位;部分 mock |

---

## 三、对标 / Comparables

- 概念上接近:Google **AI Co-scientist**、Sakana **AI Scientist**、Stanford 科研 Agent;
- 编排范式接近:Anthropic multi-agent research、**AutoGen / CrewAI / LangGraph**;
- **差异化**:我强调**工程可控性**(Schema 治理 + HITL + 沉淀 + 自愈),而非"全自动发论文";并有**真实领域物理**落地。

---

## 四、按岗位的定位策略 / Positioning by role

| 目标岗位 | 主打卖点 | 弱化 |
|---|---|---|
| **AI Agent / LLM 应用工程** | Commander 双层 FSM、debate、function-calling-ReAct、Schema 治理、mock 兜底 | 物理仿真细节 |
| **AI Infra / 平台** | 5 层架构、依赖反转、import-linter CI、harness/agent 解耦、可观测沉淀 | 单一领域 |
| **Research Engineer / AI for Science** | 真实 PIM 物理、自愈实验环路、自主消融设计、知识沉淀闭环 | 前端细节 |
| **算法 / ML** | QR 正交化解病态、memory-polynomial 对消、领域指标(RES/PIM/APE) | 系统工程 |

---

## 五、3 个最强记忆点(面试官只记得住 3 个)

1. **Schema 是脊柱** —— 把软件契约思想引入 LLM 流水线,"人写=机器写",彻底解耦。
2. **自愈反馈环路** —— Execution 不达标 → 规则追责锅在哪 → 动态改 DAG 拉回重跑 → 预算控制。
3. **真实物理 + QR 正交化** —— 双载波 PIM 对消,普通梯度下降发散,我用 QR 正交化救活,RES 随记忆深度真实变化。

> One-liner: *I built a substrate that makes research agents controllable, trustworthy, and reproducible — validated end-to-end on my own PIM-cancellation research.*

---

## 六、注意事项 / Caveats(诚实加分)

- **不要夸大**:7 层真实模型未训练(物理替身);posttrain 是 V0 占位;部分 mock。主动说明这些反而显成熟。
- **强调判断力**:把"能演示的真实"与"需硬件的真实"分清,本身是工程判断。
- **承认单人取舍**:深度 vs 广度的取舍要能说清为什么这么选。
