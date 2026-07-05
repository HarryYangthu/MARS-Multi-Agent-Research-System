# V2 Agent TODO

V0 已完成 Dev E2E 验收。V2 开发的第一原则是:所有新增能力必须继续保留
`scripts/run_demo.py --mock-mode` 和 `scripts/acceptance.sh` 的 V0 回归路径。

## 0. V2 开工清理

- [x] 明确当前正式版本仍是 `v0.1.0`;V2 是开发阶段,不能把 UI/API 版本提前标成稳定 V2。
- [x] 清理仓库表面的生成物:Python package metadata、TypeScript incremental build cache 不再纳入版本控制。
- [x] 收紧 runtime KB index 忽略规则,避免 `_index 2.json` 这类本地副本污染 diff。
- [x] 新增 `ACCEPTANCE_V2.md`,给 V2 从开发态进入稳定态设 release gate。
- [ ] V2 每个大功能合并前,跑一次 V0 acceptance 子集,确认没有破坏 mock-first 主链路。

## 1. Scope Guard

- [ ] V2 可以做:Commander/Bridge 诊断与反馈回路、Agent 自上下文、系统化 evaluation、Coding post-training control plane、真实后训练流水线。
- [ ] V2 保持:Schema 是下游契约;所有 Agent 输出仍必须是 `markdown body + YAML frontmatter` 并通过 schema 校验。
- [ ] V2 保持:Bridge 必经路径和 `bridge/agent_registry.py` 反转依赖,禁止 bridge 直接 import 具体 Agent。
- [ ] V2 暂不做:MemoryRecord v2 统一记忆模型、遗忘治理、跨项目多租户 SaaS 化。相关内容只作为 V2 规划文档存在。

## 2. Agent Work

- [ ] Idea Agent:补齐调研来源的 provenance,把质量 warning 结构化为后续 Agent 可消费的字段。
- [ ] Experiment Agent:优化 baseline reuse 决策、消融矩阵生成、预算估算和失败回滚目标。
- [ ] Coding Agent:稳定 patch 生成/应用/回滚闭环,把 post-training endpoint、local vLLM、remote API 的选择逻辑测试补齐。
- [ ] Execution Agent:把 mock simulation 与真实 runner 的接口收敛,保留 CPU-only fallback。
- [ ] Writing Agent:让报告引用 run metrics、diagnosis 和 evaluation result,避免只复述上游产物。

## 3. System Work

- [ ] Commander:将自然语言入口选择、run 状态查询、feedback loop start 统一为可测的工具调用链。
- [ ] Diagnosis:失败归因产物必须 schema 化,并能驱动回拉到 Experiment/Coding/Execution/Writing 的目标节点。
- [ ] Evaluation:把 evaluator registry 接入 run lifecycle,形成可追踪的 `evaluation_report.v1`。
- [ ] Observability:保留 JSONL audit source,LangSmith/trace sink 只能作为旁路增强。
- [ ] Posttrain:V2 才能加入 GRPO/preference pair/reward 训练流水线;训练产物必须写入 `posttrain/` 下已忽略的运行目录。

## 4. Release Gate

- [x] 新增 `ACCEPTANCE_V2.md` 或在 `ACCEPTANCE.md` 增补 V2 章节后,才把 README/backend/frontend 的稳定版本从 V0 改为 V2。
- [ ] V2 release 前必须同时通过 V0 Dev E2E、V2 新增验收、import-linter、`mypy --strict`。
