# MARS · 多 Agent 研究系统

> **研究型多 Agent 系统的工程底座** — 把研究问题经 5 个专门 Agent 一路推到论文初稿,
> 全程 schema 强校验、每步 HITL 审核、完整审计可回放。

[English](README.md) · 简体中文

![status](https://img.shields.io/badge/V0-验收通过-brightgreen) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![next](https://img.shields.io/badge/next.js-15-black) ![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## 这是什么

MARS 是一套把研究周期从 **月级压缩到周级** 的工作台。研究员的一个问题
经过 5 个专门 Agent 接力:

```
Idea(创意)→ Experiment(实验)→ Coding(编码)→ Execution(执行)→ Writing(写作)
```

每个 Agent 输出一份 **Schema 校验后的 markdown 文件**;**每步等你 Approve**
才会进下一节点,Reject 终止链路。底层 Harness 提供多模型 debate、4 区
共享知识库、Baseline 复用 fingerprint、以及 hook 在工具分发路径上的
**Gate 5** — 任何会破坏项目 baseline 的 patch 在执行前就被静态拒绝。

首个落地项目是 `projects/moe-pimc/` — *PIMC for FDD Massive MIMO under
beam/layer switching*。

## 亮点

- **5 个专门 Agent**(Idea / Experiment / Coding / Execution / Writing),
  每个有独立的 LLM、工具、(可选)多角色辩论。
- **Schema 是脊柱。** 每份产物都是 `markdown body + YAML frontmatter`,
  对应 5 个 JSON Schema 之一。**人手写的和 Agent 生成的对下游等价**,
  只要 schema 通过。
- **每一步 HITL。** 每个 Agent 完成后停在 `WAITING_REVIEW`,你点 Approve
  下一节点才启动;Reject 直接 halt 整条链路。
- **5 个系统级 Gate** — 流程 Gate 1-4 + **Gate 5 hook 在 tool dispatch
  路径上**,根据项目 `AGENTS.md` 的静态规则拒绝任何会动 baseline 的修改。
- **多模型辩论。** Idea / Writing 默认开 3 角色辩论;模式根据可用 API key
  自动降级:`real_multi_model` → `single_model_simulated` → `mock_debate`。
- **LLM 一层抽象。** 一等支持 Anthropic、OpenAI、Qwen、Gemini、**DeepSeek**、
  本地 vLLM,以及任何 OpenAI-compatible 自定义端点。
- **4 区共享 KB**(文献 / 方法 / 代码资产 / 实验运行档案),开箱用
  确定性 hash embedding,后续可热替换为 ChromaDB / sentence-transformers。
- **Mock-first。** 零 API key、零 GPU 时,完整 11 步 Demo 仍然能跑通,
  靠 `mock_provider` + `mock_simulation` + `mock_debate` 兜底。CI 每个
  PR 都跑这条路径。
- **完整沉淀。** 每个任务在 `runs/<时间戳>_<任务名>/` 写 9 个子目录:
  input / context / 各 Agent 产物 / HITL / events,**全程可审计可回放**。

## 架构一览

```
┌──────────────────────────────────────────────────────────────────────┐
│ Tier 1  前端工作台(Next.js 15)                                      │
│   Lab 主页 · Agent 工作区 · 多实验 split view · HITL 编辑器          │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 2  API + Bridge(FastAPI)                                       │
│   /api/runs · /api/artifacts · /api/execution · /api/templates …     │
│   bridge/orchestrator 推动 RunGraph;agent_registry 反转依赖         │
│   bridge 永远不直接 import 任何具体 Agent                            │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 3  五个 Agent                                                   │
│   IdeaAgent(开 debate)     →  proposal.v1                          │
│   ExperimentAgent           →  experiment_plan.v1                    │
│   CodingAgent               →  code_spec.v1   (3 LLM backend)        │
│   ExecutionAgent            →  run_log.v1     (≤6 路并发仿真)        │
│   WritingAgent(开 debate)  →  report.v1                             │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 4  Harness(agent-agnostic)                                     │
│   runtime · schema · llm · context · kb · gates · tools · sediment.  │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 5  存储与项目                                                   │
│   runs/<id>/ (9 子目录) · knowledge/<zone>/ · workspace/repos/       │
│   projects/<name>/{AGENTS.md, repo_link.yaml, data_gen.py}           │
└──────────────────────────────────────────────────────────────────────┘
```

依赖方向严格单向,由 **import-linter** 强制:

```
api  →  bridge  →  hitl  →  (agents | execution | workers)  →  storage  →  harness
```

详细架构图见 [`docs/architecture.md`](docs/architecture.md)。

## 快速开始(零依赖)

不用 GPU、不用 LLM key、不用 Docker:

```bash
git clone git@github.com:HarryYangthu/MARS-Multi-Agent-Research-System.git mars
cd mars
cp .env.example .env                 # 所有 key 留空也行 — 自动用 mock
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# === 后端 ===
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# === 前端 ===
cd frontend && npm install --legacy-peer-deps && npm run dev
# 浏览器打开 http://localhost:3000
```

跑标准 11 步 e2e demo(mock 模式):

```bash
python scripts/run_demo.py --port 8000 --mock-mode
```

完整验收(mypy --strict + import-linter + 209 tests + e2e):

```bash
bash scripts/acceptance.sh
```

## 接入真实数据(Hardware E2E)

把 provider key 写进 `.env`,以 DeepSeek 为例:

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

用软链接挂上你的真实研究代码(**不入 MARS 仓**,这是 CLAUDE.md 硬约束):

```bash
ln -s /path/to/your/code workspace/repos/pimc-current
# 改 projects/moe-pimc/repo_link.yaml 的 repo_path
python scripts/ingest_repo.py --project moe-pimc
```

索引参考论文(PDF):

```bash
cp ~/Downloads/*.pdf workspace/uploads/papers/
python scripts/ingest_pdfs.py
```

## 仓库结构

```
mars/
├─ README.md / CLAUDE.md / PRODUCT.md / DESIGN.md / ACCEPTANCE.md
├─ pyproject.toml · docker-compose.yml · .env.example
├─ configs/                  # agents/models/tools/gates/knowledge/execution 配置
├─ backend/app/
│  ├─ api/                   # REST + WebSocket
│  ├─ bridge/                # orchestrator · agent_registry · workflow_service
│  ├─ harness/               # runtime · schema · llm · context · kb · gates · tools · sedimentation
│  ├─ agents/                # 5 个 Agent + debate runner
│  ├─ hitl/                  # review_session · approval · audit_log · diff_view
│  ├─ execution/             # mock_simulation · batch_runner · log_streamer · metrics_collector
│  ├─ storage/               # run_store · artifact_store · file_store
│  └─ workers/
├─ frontend/src/
│  ├─ app/                   # Next.js 路由 — Lab 主页 / RunDetail / Multi view / Entries
│  ├─ components/            # TopBar · ProjectsPanel · PipelineOverview · EventLog · KBPanel
│  ├─ lib/                   # api · i18n · socket
│  └─ stores/
├─ projects/moe-pimc/        # AGENTS.md · repo_link.yaml · data_gen.py
├─ workspace/repos/          # 真实研究代码(软链接,gitignore)
├─ workspace/uploads/papers/ # 参考论文 PDF(gitignore)
├─ knowledge/                # 4 区 KB(首次摄入后 gitignore)
├─ runs/                     # 任务沉淀(gitignore)
├─ templates/                # artifact 模板 · 代码规范
├─ scripts/                  # dev.sh · run_demo.py · acceptance.sh · ingest_repo.py · ingest_pdfs.py
└─ docs/                     # architecture · agent_io_schema · run_lifecycle · phase status
```

## 项目状态

**V0 验收已通过**(Dev E2E 通道)。完整审计见
[`docs/implementation_report.md`](docs/implementation_report.md)。要点:

| | |
|---|---|
| 测试 | **209 通过** |
| `mypy --strict` | 132 个源文件 0 错 |
| `import-linter` 4 条契约 | 全部 KEPT |
| Schema 合规率 | ≥ 95% |
| Baseline matcher 召回/精度 | 合成集 100% / 100% |
| 11 步 e2e demo | 零外部依赖通过 |
| `runs/<id>/` 完整性 | 9/9 子目录有内容 |

## 路线图(V1 主题)

- **后训练流水线** — GRPO 训练器、从 `runs/<id>/hitl/*` 构造 preference
  pair、复合 reward(schema 合规 × baseline 保护 × 下游指标)。
- **streaming UX** — Coding Agent 逐 token 显示 LLM 输出;Schema 错误
  一键"补全模板字段"修复。
- **真实训练可观测** — 子进程 stdout → WS、GPU 利用率曲线 + loss 曲线并排。
- **多 project 隔离** — 每 project 独立 `runs/` 和 `knowledge/`,Lab
  主页加项目切换器。
- **真实向量 KB** — 把 deterministic-hash embedder 替换为
  sentence-transformers / ChromaDB,API 不变。

## 文档

- [`PRODUCT.md`](PRODUCT.md) — 产品定义(5 Agent、双形态、KB 区、决策日志)
- [`DESIGN.md`](DESIGN.md) — 架构(分层、Schema、Harness 内部、运行时、前端)
- [`ACCEPTANCE.md`](ACCEPTANCE.md) — V0 验收边界(Dev E2E + Hardware E2E)
- [`CLAUDE.md`](CLAUDE.md) — 硬约束 + 目录结构 + 风格规范(Claude Code / Codex 自动加载)
- [`docs/architecture.md`](docs/architecture.md) — 配套架构图
- [`docs/agent_io_schema.md`](docs/agent_io_schema.md) — 5 个 schema 字段说明 + 示例
- [`docs/run_lifecycle.md`](docs/run_lifecycle.md) — 一次任务从创建到归档的时序图
- [`docs/frontend_ux.md`](docs/frontend_ux.md) — P0 UI 契约

## 开源协议

MIT — 见 [LICENSE](LICENSE)。

## 引用

如果你在学术工作中用了 MARS,请引用本仓库:

```bibtex
@misc{mars2026,
  title  = {MARS: Multi-Agent Research System},
  author = {Yang, Harry},
  year   = {2026},
  url    = {https://github.com/HarryYangthu/MARS-Multi-Agent-Research-System}
}
```
