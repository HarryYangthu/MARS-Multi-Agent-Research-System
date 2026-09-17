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

首个落地项目是 `projects/pimc/` — *PIMC for FDD Massive MIMO under
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
- **真实模型审查。** FocusedIdea 使用 Pro 提案与 Flash 审查；模型、工具或凭据缺失时明确失败。
- **LLM 一层抽象。** 一等支持 Anthropic、OpenAI、Qwen、Gemini、**DeepSeek**、
  本地 vLLM,以及任何 OpenAI-compatible 自定义端点。
- **4 区共享 KB**(文献 / 方法 / 代码资产 / 实验运行档案),开箱用
  确定性 hash embedding,后续可热替换为 ChromaDB / sentence-transformers。
- **真实执行。** 缺数据或服务不生成成功结果。新增[本地 CLI 研究闭环](docs/cli-research.md)，
  通过真实外部静态仓完成候选代码、CPU 实验、验证反馈和最终报告，无需前端。
- **完整沉淀。** 每个任务在 `runs/<时间戳>_<任务名>/` 写 9 个子目录:
  input / context / 各 Agent 产物 / HITL / events,**全程可审计可回放**。
- **Tools V2 平台。** Agent 与 Commander 共用 registry-backed 工具目录,
  统一 schema 校验、配置开关、Gate 5 保护、审计事件、审批记录和 rollback 快照。

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

## 本地开发

启动界面可不使用 GPU 或 Docker；执行研究需要配置真实模型凭据和数据：

```bash
git clone git@github.com:HarryYangthu/MARS-Multi-Agent-Research-System.git mars
cd mars
cp .env.example .env                 # 按实际模型填写凭据，不要提交 .env
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# === 后端 ===
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# === 前端 ===
cd frontend && npm install --legacy-peer-deps && npm run dev
# 浏览器打开 http://localhost:3000
```

只做研究与仿真时，使用 [`mars research` 的完整命令](docs/cli-research.md)，无需启动上述前后端。

完整验收(mypy --strict + import-linter + 后端/前端检查 + Tools V2 audit + e2e):

```bash
bash scripts/acceptance.sh
```

## 上线部署

生产环境建议采用混合部署:Next.js 前端上 Vercel,FastAPI 后端、Redis、
Chroma、`runs/`、`knowledge/` 与执行后端放在长驻服务器/GPU 机器上。

标准上线流程、回滚策略、memory 管理、多用户访问边界和工具防乱 FAQ 见
[`docs/deployment_runbook.md`](docs/deployment_runbook.md)。

## 接入真实数据(Hardware E2E)

把 provider key 写进 `.env`,以 DeepSeek 为例:

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

用软链接挂上你的真实研究代码(**不入 MARS 仓**,这是 CLAUDE.md 硬约束):

```bash
ln -s /path/to/your/code workspace/repos/pimc-current
# 改 projects/pimc/repo_link.yaml 的 repo_path
python scripts/ingest_repo.py --project pimc
```

索引参考论文(PDF):

```bash
cp ~/Downloads/*.pdf workspace/uploads/papers/
python scripts/ingest_pdfs.py
```

## 仓库结构

```
mars/
├─ README.md / AGENTS.md / CLAUDE.md
├─ pyproject.toml · docker-compose.yml · .env.example
├─ configs/                  # agents/models/tools/gates/knowledge/execution 配置
├─ backend/app/
│  ├─ api/                   # REST + WebSocket
│  ├─ bridge/                # orchestrator · agent_registry · workflow_service
│  ├─ harness/               # runtime · schema · llm · context · kb · gates · tools · sedimentation
│  ├─ agents/                # 5 个 Agent + debate runner
│  ├─ hitl/                  # review_session · approval · audit_log · diff_view
│  ├─ execution/             # 真实静态/通用适配器 · batch_runner · log_streamer · metrics_collector
│  ├─ storage/               # run_store · artifact_store
├─ frontend/src/
│  ├─ app/                   # Next.js 路由 — Lab 主页 / RunDetail / Multi view / Entries
│  ├─ components/            # TopBar · ProjectsPanel · PipelineOverview · EventLog · KBPanel
│  ├─ lib/                   # api · i18n · socket
│  └─ stores/
├─ projects/pimc/        # AGENTS.md · repo_link.yaml · data_gen.py
├─ workspace/repos/          # 真实研究代码(软链接,gitignore)
├─ workspace/uploads/papers/ # 参考论文 PDF(gitignore)
├─ knowledge/                # 4 区 KB(首次摄入后 gitignore)
├─ runs/                     # 任务沉淀(gitignore)
├─ templates/                # artifact 模板 · 代码规范
├─ scripts/                  # dev.sh · ingest_repo.py · ingest_pdfs.py
└─ docs/                     # architecture · agent_io_schema
```

## 验证

当前软件检查以 `.github/workflows/ci.yml` 为准。真实研究需要有效模型服务、研究代码和数据；软件测试通过不代表研究目标已达成。CLI 使用方式见 [cli-research.md](docs/cli-research.md)。

## 文档

- [当前核心代码与实现思路](docs/core-code-map.md)
- [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) — 编码 Agent 使用的硬约束、目录结构和风格规范
- [`docs/architecture.md`](docs/architecture.md) — 配套架构图
- [`docs/agent_io_schema.md`](docs/agent_io_schema.md) — 5 个 schema 字段说明 + 示例
- [`docs/tools_catalog.md`](docs/tools_catalog.md) — Tools V2 工具目录、API、审计记录、外部 smoke
- [`docs/tool_security.md`](docs/tool_security.md) — dispatch 顺序、Gate 5、rollback、红action、网络策略

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
