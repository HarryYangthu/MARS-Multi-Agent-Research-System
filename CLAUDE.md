# CLAUDE.md

> 这是 MARS 项目的 CLAUDE.md。Claude Code / Codex / Cowork 在 session 启动时自动加载。
> 控制在 200 行内,只放硬约束 + 目录结构 + 风格规范。详细 Why / What / How 看 `PRODUCT.md` / `DESIGN.md` / `ACCEPTANCE.md`。

## 项目一句话定义

MARS = 研究型多 Agent 系统的底座(Multi-Agent Research System)。
首个项目:`projects/pimc/` — PIMC for FDD Massive MIMO under beam/layer switching。

## 技术栈

- 后端:Python 3.11 / FastAPI / python-socketio / asyncio / Redis
- 前端:Next.js 15 / Tailwind / shadcn/ui / Socket.IO 客户端
- Agent 编排:LangGraph(V0 默认实现,但 BaseAgent 接口不绑死,可替换)
- 知识库:ChromaDB(4 个独立 collection)
- LLM Backends:Anthropic / OpenAI / Qwen / Gemini API + 本地 vLLM serve
- 部署:Docker Compose,单机 4 × NVIDIA L40S

## 目录结构(强约束)

```
mars/
├─ CLAUDE.md / PRODUCT.md / DESIGN.md / ACCEPTANCE.md / README.md
├─ pyproject.toml / docker-compose.yml / .env.example
├─ configs/                    # 全局配置
│  ├─ agents.yaml              # 5 Agent 各自的 model / debate / tools 配置
│  ├─ models.yaml              # LLM provider 注册
│  ├─ tools.yaml               # 工具开关与权限
│  ├─ gates.yaml               # 5 个 HITL Gate 阈值
│  ├─ knowledge.yaml           # 4 区 KB embedding / chunk 配置
│  └─ execution.yaml           # GPU 调度 / 并发上限
│
├─ backend/app/
│  ├─ api/                     # FastAPI routes(按资源拆分)+ websocket
│  ├─ bridge/                  # 产品编排层 — 知道有哪些 Agent
│  ├─ harness/                 # Agent-agnostic 可信机制 — 不知道任何具体 Agent
│  │  ├─ runtime/              #   RunGraph / state_machine / queue / event_bus
│  │  ├─ schema/               #   frontmatter_parser / validator + 5 个 JSON Schema
│  │  ├─ tools/                #   5 类 tool 注册中心(Gate 5 hook 在此)
│  │  ├─ gates/                #   5 个系统 HITL Gate
│  │  ├─ context/              #   3 层装载 + Manifest + 压缩
│  │  ├─ llm/                  #   多 backend 抽象 + post_training_loader
│  │  ├─ kb/                   #   4 区 ChromaDB client
│  │  └─ sedimentation/        #   每 Agent 完成后的沉淀 hook
│  ├─ agents/                  # 5 Agent + debate 子模块
│  ├─ hitl/                    # 高频人工交互(review / diff / approval / audit)
│  ├─ execution/               # 仿真运行(simulation_runner / log_streamer / metrics)
│  ├─ storage/                 # 数据访问层(run_store / artifact_store / file_store)
│  └─ workers/                 # 异步后台(agent_worker / simulation_worker / heartbeat)
│
├─ frontend/                   # Next.js 工作台
├─ workspace/repos/            # 接入真实研究代码仓(submodule / 软链接 / 镜像)
├─ workspace/uploads/          # 上传论文 / 文档 / 数据
├─ knowledge/                  # 4 区 KB 物理文件(literature/methodology/code_assets/run_archive)
├─ runs/<timestamp>_<task>/    # 每次任务完整沉淀
├─ templates/                  # prompts / artifacts / reports / code_rules
├─ projects/pimc/          # 项目元数据 + AGENTS.md + repo_link.yaml + data_gen.py
├─ posttrain/                  # V2 占位
├─ docs/                       # 补充文档
└─ scripts/                    # dev.sh / run_agent.py / index_repo.py
```

## 8 条硬约束

1. **依赖方向单向**:`frontend → api → bridge → agents`,`bridge / agents → harness`,`harness/runtime` 不依赖任何上层。**禁止**反向 import。`harness/` 内部模块**禁止** import `agents/` 或 `bridge/`。

2. **Schema 是脊柱**:Agent 输出 = `markdown body + YAML frontmatter`,frontmatter 必须通过对应 JSON Schema 校验。**人手写的 md** 与 **Agent 生成的 md** 对下游等价(只要 Schema 校验通过)。Schema 合规率目标 ≥95%。5 种 schema 类型:`proposal.v1` / `experiment_plan.v1` / `code_spec.v1` / `run_log.v1` / `report.v1`。

3. **Bridge 必经路径**:产品调用必经 `bridge/`。开发调试可用 `--bypass-bridge` 直接调 agent,但 UI 路径上不暴露此 flag。

4. **HITL 两层**:每个 Agent 输出支持人工 review / 编辑 / approve(`hitl/`,高频);系统级 5 个 Gate 强制阻塞(`harness/gates/`,稀疏)。Gate 5(baseline_compatibility)由项目 `AGENTS.md` 静态规则触发,**挂在 `harness/tools/registry.py` 的 dispatch 路径上**,不是 bridge 流程的 checkpoint。

5. **Posttrain V0/V2 边界**:V0 只支持**加载**后训练模型(vLLM serve / LoRA adapter / 远程 endpoint)。V0 **不实现** GRPO 训练流水线、preference pair 构造、reward 设计。这些是 V2。

6. **真实研究代码不复制进仓**:通过 `projects/<name>/repo_link.yaml` 接入(`local_path` / `git_submodule` / `mirror`)。仓内只存:项目元数据、`AGENTS.md`、可入仓的合成数据脚本(如 `data_gen.py`)、可选脱敏简化样例。

7. **每 Agent 独立 LLM 配置**:`configs/agents.yaml` 给每个 Agent 配 model / endpoint / key / temperature。Coding Agent 额外支持 3 backend(`remote_api` / `local_vllm` / `live_checkpoint`,V0 只前两种)。

8. **每次 run 完整沉淀**:写入 `runs/<timestamp>_<task>/`,子目录 `input/ context/ idea/ experiment/ coding/ execution/ writing/ hitl/ events/`。版本化:`*.v1.md / *.v2.md / *.approved.md`。

9. **V0 必须在 mock 模式下能跑通完整 demo**:无真实 LLM API key 时,自动降级到 `mock_provider`(返回结构化的占位响应);无 GPU 时,Execution Agent 走 `mock_simulation`(基于 `data_gen.py` 合成数据 + 模拟 loss 曲线)。Demo 在**零外部依赖**下必须能完成 ACCEPTANCE.md §2 的 11 步主脚本。

10. **End-to-end first 开发顺序**:严格按 `ACCEPTANCE.md §11` 的 7 个 Phase 实现。**核心原则**:任何 Phase 完成后,系统必须仍能跑通端到端 demo(从 mock pipeline 起步,逐步把 mock 替换成真实)。**禁止**先横向把 KB / LLM / 前端 / Execution 各自独立做完,最后才尝试集成——这是 V0 失败的最常见模式。新模块必须先接入 e2e 链路,再补充完整功能。

## 风格规范

- Python 3.11,类型注解强制(`mypy --strict` CI 必过)
- 日志用 `loguru`,禁用 `print`
- tensor 操作前后必须有 shape 注释(沿用 `projects/pimc/AGENTS.md` 第 4 条)
- 配置统一用 YAML,禁止硬编码常量
- 新增依赖必须在 PR 描述说明理由
- 测试目录 `backend/tests/`,新功能必须配 unit test
- frontend 用 TypeScript,禁用 `any`(除非配 `// @ts-expect-error` 注释解释)

## 关键禁止行为

- ❌ `harness/` 内部 import `agents/` 或 `bridge/`
- ❌ Agent 输出绕过 Schema 校验直接传下游
- ❌ 真实研究代码 commit 进 mars 仓
- ❌ V0 实现训练流水线(超出范围)
- ❌ 生成 Agent 输出不写 frontmatter / 不通过 Schema
- ❌ 修改 `projects/<name>/AGENTS.md` 中标 baseline 保护的内容(触发 Gate 5)
- ❌ 在 `harness/runtime/run_graph.py` 写死"Idea→Experiment→Coding→Execution→Writing"线性拓扑(拓扑由 `bridge/workflow_service.py` 构造)
- ❌ 在 `bridge/` 直接 `import agents.idea.IdeaAgent`(走 `bridge/agent_registry.py` 反转)

## 详细文档指针

- 产品定义、5 Agent 各自做什么、典型工作流 → **PRODUCT.md**
- 5 Tier 架构、依赖方向图、Schema 详细定义、Harness 内部、运行时、前端架构 → **DESIGN.md**
- 验收边界、Demo 脚本、Eval 用例、性能指标 → **ACCEPTANCE.md**
- 项目级约束(baseline 保护、领域规则)→ **projects/&lt;name&gt;/AGENTS.md**
