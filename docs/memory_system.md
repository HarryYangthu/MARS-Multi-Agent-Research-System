# MARS Memory Management

> MARS Memory 是研究型多 Agent 系统的长期经验层。它以 `runs/<run_id>/` 为事实源,以 schema / evaluation / HITL / deterministic gate 为治理边界,把可复用经验注入后续 Agent 上下文,同时避免 mock、过期、未审核内容污染 prompt。

![MARS Memory System Overview](./mars_memory_system_overview.svg)

## 1. 系统定位

MARS Memory 管三类东西:

- **Run 内短期记忆**:当前任务的上下文、事件、失败原因、反馈包和 episode memory。
- **跨 run 长期记忆**:文献、方法、代码资产、历史运行档案和评测发现。
- **Agent 自进化记忆**:人工审核后的 memory item、prompt/example/eval mutation、regression task 候选。

核心原则:

- **Run 是事实源**:任何长期记忆都必须能追到 run、artifact、scorecard 或 Agent context 文件。
- **默认不信任**:mock、失败、未审核、stale、superseded 内容默认不进入后续上下文。
- **只注入摘要**:prompt 中只放 selected summary;长内容写入 raw ref。
- **每次注入可审计**:context manifest + `memory_usage.jsonl` 记录选择和使用。

## 2. 完整能力图

![MARS Memory Capability Matrix](./mars_memory_development_roadmap.svg)

MARS 当前 Memory 由 6 个能力面组成:

| 能力面 | 实现位置 | 作用 |
|---|---|---|
| Context Injection | `backend/app/harness/memory/injection.py` | 在 Context V2 pre-call 自动注入 selected memory |
| Usage Audit | `backend/app/harness/memory/usage.py` | 写 `memory_usage.jsonl`,记录注入了哪些 memory |
| Importance Scoring | `backend/app/harness/memory/importance.py` | 计算 salience,决定写入和排序权重 |
| Episode Index | `backend/app/harness/memory/episode.py` | 将 run-local learning event 建索引,支持本 run 检索 |
| Semantic Graph | `backend/app/harness/memory/semantic.py` | 从 MemoryRecord 抽取实体/关系,为 graph-enhanced retrieval 提供索引 |
| Memory Evals | `backend/app/harness/memory/evals.py` | 评估 retrieval precision、pollution、stale/superseded recall |

## 3. Memory 分层

| 层 | 名称 | 存储 | 生命周期 |
|---|---|---|---|
| L1 | Working Memory | `runs/<run_id>/context/` | 当前 run 内有效 |
| L2 | Episode Memory | `runs/<run_id>/memory/episode_memory.jsonl` + `episode_index.jsonl` | 当前 run 内可检索,后续可人工提升 |
| L3 | Governed KB Memory | `knowledge/{literature,methodology,code_assets,run_archive}` | approved/active,受 TTL、decay、supersede 管理 |
| L4 | Quarantine Memory | `knowledge/quarantine` | mock、eval fail、低可信内容隔离区 |
| L5 | Agent Long-term Context | `configs/agent_contexts/*.yaml` + `backend/app/agents/*/{prompts,examples,evals}` | 只加载 approved/active memory item |
| L6 | Self-Evolution Memory | `runs/<run_id>/memory/*candidates*.jsonl` | pending review,批准后进入 Agent memory 或 KB |

## 4. 写入路径

### 4.1 Approved Artifact 沉淀

```text
artifact.approved.md
  -> sediment_approved_artifact()
  -> artifact evaluators
  -> importance scorer
  -> extractor by agent
  -> MemoryRecord v2
  -> governed KB / quarantine
  -> semantic graph index
```

Agent 到 KB 分区映射:

| Agent | 主写入分区 |
|---|---|
| Idea | `literature`, `methodology` |
| Experiment | `methodology` |
| Coding | `code_assets` |
| Execution | `run_archive` |
| Writing | `methodology` |
| Evaluation | `methodology` 或 `quarantine` |

### 4.2 Commander Learning Event

```text
Commander observation
  -> episode_memory.jsonl
  -> episode_index.jsonl
  -> memory_candidates.jsonl
  -> human review
  -> configs/agent_contexts/<agent>.yaml
  -> governed KB sync
```

Episode memory 永远先留在 run 内。只有 `approve_memory_candidate()` 之后,候选经验才会进入 Agent 长期 memory。

### 4.3 Self-Evolution Mutation

```text
evaluation finding / context lever
  -> self_evolution_mutation.v1
  -> deterministic mutation gate
  -> human approve
  -> update prompt/example/eval file
  -> sync file to governed KB memory
```

Mutation 只允许改 `prompts/`、`examples/`、`evals/`,不能自动改 runtime code。

## 5. MemoryRecord v2

每条长期记忆都以 `MemoryRecord v2` 存储,关键字段包括:

| 字段 | 用途 |
|---|---|
| `record_id` | 稳定 memory id |
| `zone` | 所属 KB 分区 |
| `memory_type` | `semantic` / `episodic` / `procedural` |
| `source_path` | artifact、scorecard 或 Agent context 来源 |
| `run_id` / `agent` / `schema` | provenance |
| `is_mock` / `approved` | 治理状态 |
| `confidence` / `salience` | 排序权重 |
| `eval_status` | 写入前评测结果 |
| `ttl_days` / `valid_from` | 生命周期 |
| `superseded_by` | 被替代关系 |
| `access_count` / `last_accessed_at` | 召回使用记录 |

## 6. 召回与注入

Context V2 在 `compile_context()` 阶段自动调用 MemorySelector。

```text
Agent task
  -> per-agent memory policy
  -> select_memory()
  -> semantic graph bonus
  -> memory segments
  -> ContextManifestV2
  -> memory_usage.jsonl
```

默认综合评分:

```text
score =
  similarity * 0.4
  + recency * 0.2
  + confidence * 0.2
  + eval_status * 0.1
  + salience * 0.1
  + semantic graph bonus
```

默认过滤:

- `approved=false` 不注入。
- `is_mock=true` 在 research/hardware profile 下不注入。
- `superseded_by` 非空不注入。
- stale/rejected/superseded 的 Agent memory item 不加载。

每个 Agent 有不同召回范围:

| Agent | 默认召回 |
|---|---|
| Commander | `methodology`, `run_archive` |
| Idea | `literature`, `methodology`, `run_archive` |
| Experiment | `methodology`, `run_archive` |
| Coding | `code_assets`, `methodology`, `run_archive` |
| Execution | `run_archive`, `methodology` |
| Writing | `methodology`, `run_archive`, `literature` |

## 7. 治理策略

配置入口:[configs/memory.yaml](../configs/memory.yaml)

| Profile | 写入要求 |
|---|---|
| `dev_e2e` | schema approved; mock 进入 quarantine |
| `research` | schema approved + eval pass + provenance |
| `hardware` | research 要求 + real execution |

Quarantine API:

```text
GET  /api/knowledge/quarantine/items
GET  /api/knowledge/quarantine/search
POST /api/knowledge/quarantine/{record_id}/review
```

Review action:

| action | 结果 |
|---|---|
| `approve` | 非 mock memory 可提升到主 KB 分区 |
| `reject` | 标记 rejected,不召回 |
| `stale` | 标记 stale,不召回 |
| `supersede` | 标记 superseded_by |

Mock memory 不能被 promote 到主分区。

## 8. 生命周期管理

`consolidate()` 执行:

- mock/quarantine 超期后删除。
- 主库 memory 超期后标记 `expired/archived`,不直接无审计删除。
- 根据 half-life 衰减 `confidence` 和 `salience`。
- 低于阈值的 memory 标记 `prune_candidate`。
- 召回时更新 `access_count` 和 `last_accessed_at`。

Agent memory item 还有 outcome 生命周期:

- 注入过的 memory 会记录 usage。
- 后续失败会写 outcome history。
- 连续两次负反馈后自动标记 `stale`,并禁用后续注入。

## 9. 语义图与冲突处理

每次 `ingest_memory()` 成功写入后,系统会更新:

```text
knowledge/_semantic_graph.json
```

语义图记录:

- record -> entities
- entity -> record ids
- source_path -> mentions relations

抽取对象包括:

- `project`、`agent`、`schema`、`kind`
- metric: `RES`、`PIM`、`APE`、`loss`、`latency`、`score`
- path-like refs
- 技术 identifier

召回时,同实体相关 record 会获得 graph bonus。

冲突处理由 `assess_conflict()` 提供 deterministic hint:

| decision | 含义 |
|---|---|
| `new` | 低重叠,新记忆 |
| `complementary` | 部分重叠,可补充 |
| `duplicate_or_update` | 高重叠,可能是更新 |
| `conflict` | 共享实体但语义极性相反,需要 review |

## 10. Memory 评测

Memory 专项评测入口:

```text
backend/app/harness/memory/evals.py
```

当前覆盖:

| Evaluator | 检查 |
|---|---|
| `memory.retrieval_precision` | 查询是否召回预期 memory |
| `memory.pollution_guard` | mock、unapproved、superseded 是否污染默认召回 |

Evaluation Harness 可把这些指标纳入 suite,用于防止 Memory 改动破坏上下文治理。

## 11. 关键文件

```text
backend/app/harness/memory/
├─ injection.py          # pre-call memory 注入
├─ selector_policy.py    # 每 Agent 召回策略
├─ usage.py              # memory_usage.jsonl
├─ importance.py         # salience / importance scorer
├─ episode.py            # run-local episode index
├─ semantic.py           # lightweight semantic graph
├─ conflict.py           # conflict / merge hints
└─ evals.py              # memory-specific evaluators

backend/app/harness/kb/
├─ models.py             # MemoryRecord v2
├─ selector.py           # hybrid + graph-enhanced retrieval
├─ ingester.py           # write + semantic index
├─ consolidate.py        # ttl / decay / prune
└─ stores.py             # file/chroma-compatible store facade

backend/app/storage/
├─ agent_context_store.py
└─ self_evolution_store.py
```

## 12. 验证

核心测试:

```bash
PYTHONPATH=backend uv run pytest \
  backend/tests/unit/test_memory_v2.py \
  backend/tests/integration/test_memory_v2_governance.py \
  backend/tests/unit/test_memory_system_complete.py -q
```

测试覆盖:

- approved memory 自动注入 Context V2。
- `memory_usage.jsonl` 审计写入。
- episode memory 建索引并可搜索。
- semantic graph 写入并参与召回。
- quarantine review promote/reject/stale/supersede。
- mock memory 禁止 promote 到主库。
- retrieval precision 和 pollution guard。
