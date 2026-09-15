# 仓库完整目录与文件清单

按本次合入的 Git 受控文件生成；包含核心代码、前端、配置、文档、测试和部署文件。只包含有受控文件的目录，不包含运行时生成的依赖、缓存、日志、真实采集数据或外部私有代码仓。实现说明见 [core-code-map.md](core-code-map.md)。

合计 **1059 个文件，226 个子目录**（根目录另列）。本索引自身也计入。

## 顶层分布

| 范围 | 文件数 |
|---|---:|
| `.github/` | 2 |
| `backend/` | 545 |
| `configs/` | 43 |
| `deploy/` | 24 |
| `docs/` | 251 |
| `frontend/` | 99 |
| `posttrain/` | 3 |
| `projects/` | 22 |
| `scripts/` | 32 |
| `templates/` | 11 |
| `workspace/` | 1 |
| 根目录文件 | 26 |

## 全部目录

| 目录 | 直接文件 | 含子目录的文件数 |
|---|---:|---:|
| `/` | 26 | 1059 |
| `.github/` | 0 | 2 |
| `.github/workflows/` | 2 | 2 |
| `backend/` | 2 | 545 |
| `backend/app/` | 5 | 341 |
| `backend/app/agents/` | 3 | 69 |
| `backend/app/agents/coding/` | 3 | 5 |
| `backend/app/agents/coding/docs/` | 1 | 1 |
| `backend/app/agents/coding/prompts/` | 1 | 1 |
| `backend/app/agents/debate/` | 4 | 4 |
| `backend/app/agents/execution/` | 2 | 4 |
| `backend/app/agents/execution/docs/` | 1 | 1 |
| `backend/app/agents/execution/prompts/` | 1 | 1 |
| `backend/app/agents/experiment/` | 2 | 4 |
| `backend/app/agents/experiment/docs/` | 1 | 1 |
| `backend/app/agents/experiment/prompts/` | 1 | 1 |
| `backend/app/agents/idea/` | 25 | 45 |
| `backend/app/agents/idea/discovery/` | 12 | 12 |
| `backend/app/agents/idea/docs/` | 2 | 2 |
| `backend/app/agents/idea/evals/` | 1 | 1 |
| `backend/app/agents/idea/examples/` | 2 | 2 |
| `backend/app/agents/idea/prompts/` | 3 | 3 |
| `backend/app/agents/writing/` | 2 | 4 |
| `backend/app/agents/writing/docs/` | 1 | 1 |
| `backend/app/agents/writing/prompts/` | 1 | 1 |
| `backend/app/api/` | 28 | 28 |
| `backend/app/bridge/` | 34 | 36 |
| `backend/app/bridge/docs/` | 1 | 1 |
| `backend/app/bridge/prompts/` | 1 | 1 |
| `backend/app/execution/` | 12 | 24 |
| `backend/app/execution/adapters/` | 5 | 5 |
| `backend/app/execution/remote/` | 7 | 7 |
| `backend/app/harness/` | 4 | 152 |
| `backend/app/harness/agent_loop/` | 11 | 11 |
| `backend/app/harness/context/` | 13 | 13 |
| `backend/app/harness/discovery/` | 17 | 17 |
| `backend/app/harness/evaluation/` | 15 | 18 |
| `backend/app/harness/evaluation/evaluators/` | 3 | 3 |
| `backend/app/harness/gates/` | 7 | 7 |
| `backend/app/harness/kb/` | 16 | 16 |
| `backend/app/harness/llm/` | 7 | 7 |
| `backend/app/harness/memory/` | 9 | 9 |
| `backend/app/harness/observability/` | 4 | 4 |
| `backend/app/harness/project_packs/` | 3 | 3 |
| `backend/app/harness/runtime/` | 9 | 9 |
| `backend/app/harness/schema/` | 3 | 14 |
| `backend/app/harness/schema/schemas/` | 11 | 11 |
| `backend/app/harness/sedimentation/` | 3 | 4 |
| `backend/app/harness/sedimentation/extractors/` | 1 | 1 |
| `backend/app/harness/tools/` | 4 | 16 |
| `backend/app/harness/tools/code/` | 1 | 1 |
| `backend/app/harness/tools/collaboration/` | 1 | 1 |
| `backend/app/harness/tools/execution/` | 1 | 1 |
| `backend/app/harness/tools/knowledge/` | 1 | 1 |
| `backend/app/harness/tools/mcp_adapters/` | 1 | 1 |
| `backend/app/harness/tools/reporting/` | 1 | 1 |
| `backend/app/harness/tools/search/` | 6 | 6 |
| `backend/app/hitl/` | 6 | 6 |
| `backend/app/reporting/` | 4 | 4 |
| `backend/app/storage/` | 16 | 16 |
| `backend/app/workers/` | 1 | 1 |
| `backend/tests/` | 4 | 202 |
| `backend/tests/baseline/` | 2 | 2 |
| `backend/tests/e2e/` | 1 | 5 |
| `backend/tests/e2e/discovery/` | 4 | 4 |
| `backend/tests/gate/` | 3 | 3 |
| `backend/tests/integration/` | 16 | 16 |
| `backend/tests/schema/` | 3 | 3 |
| `backend/tests/unit/` | 140 | 169 |
| `backend/tests/unit/agents/` | 0 | 3 |
| `backend/tests/unit/agents/idea/` | 3 | 3 |
| `backend/tests/unit/bridge/` | 8 | 8 |
| `backend/tests/unit/discovery/` | 12 | 12 |
| `backend/tests/unit/storage/` | 6 | 6 |
| `configs/` | 16 | 43 |
| `configs/agent_contexts/` | 6 | 6 |
| `configs/evaluation/` | 11 | 11 |
| `configs/evaluation_datasets/` | 2 | 2 |
| `configs/evaluation_rubrics/` | 6 | 6 |
| `configs/evaluation_suites/` | 2 | 2 |
| `deploy/` | 0 | 24 |
| `deploy/nginx/` | 2 | 2 |
| `deploy/windows/` | 21 | 22 |
| `deploy/windows/tests/` | 1 | 1 |
| `docs/` | 49 | 251 |
| `docs/assets/` | 0 | 2 |
| `docs/assets/readme/` | 2 | 2 |
| `docs/engineering/` | 2 | 2 |
| `docs/evaluation/` | 6 | 195 |
| `docs/evaluation/idea_multiagent_20260908/` | 2 | 97 |
| `docs/evaluation/idea_multiagent_20260908/feedback_replay/` | 5 | 13 |
| `docs/evaluation/idea_multiagent_20260908/feedback_replay/input/` | 1 | 1 |
| `docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/` | 0 | 6 |
| `docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0001_evolution/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0002_reflection/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/feedback_replay/role_trace/` | 1 | 1 |
| `docs/evaluation/idea_multiagent_20260908/previous_candidate/` | 4 | 4 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_01/` | 2 | 16 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/` | 0 | 8 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/` | 8 | 8 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_01/input/` | 2 | 2 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_01/role_calls/` | 0 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_01/role_calls/0001_generation/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_01/role_trace/` | 1 | 1 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/` | 3 | 38 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/` | 0 | 8 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/` | 8 | 8 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/input/` | 2 | 2 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/` | 0 | 24 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0001_generation/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0002_reflection/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0003_pairwise_judge/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0004_meta_review/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0005_evolution/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0006_reflection/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0007_pairwise_judge/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0008_meta_review/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_trace/` | 1 | 1 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/` | 1 | 24 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/` | 0 | 8 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/` | 8 | 8 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/input/` | 2 | 2 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/` | 0 | 12 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0001_generation/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0002_reflection/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0003_meta_review/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0004_meta_review/` | 3 | 3 |
| `docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_trace/` | 1 | 1 |
| `docs/evaluation/idea_quality_20260908/` | 14 | 14 |
| `docs/evaluation/idea_react_20260907/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/` | 0 | 62 |
| `docs/evaluation/idea_research_20260908/attempt_01/` | 8 | 9 |
| `docs/evaluation/idea_research_20260908/attempt_01/input/` | 1 | 1 |
| `docs/evaluation/idea_research_20260908/attempt_02/` | 6 | 21 |
| `docs/evaluation/idea_research_20260908/attempt_02/agent_traces/` | 0 | 8 |
| `docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea/` | 0 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea/612a400c539546538a263ac664e4aafc/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/` | 0 | 6 |
| `docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/50b4f21ee4564ff498f91a16628c91e0/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/527b962fd6104392b2d2d5406577c92a/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/5634b15ad474460da1fbf09072a308db/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/idea/` | 0 | 6 |
| `docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/` | 0 | 6 |
| `docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/50b4f21ee4564ff498f91a16628c91e0/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/527b962fd6104392b2d2d5406577c92a/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/5634b15ad474460da1fbf09072a308db/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_02/input/` | 1 | 1 |
| `docs/evaluation/idea_research_20260908/attempt_03_interrupted/` | 3 | 8 |
| `docs/evaluation/idea_research_20260908/attempt_03_interrupted/agent_traces/` | 0 | 3 |
| `docs/evaluation/idea_research_20260908/attempt_03_interrupted/agent_traces/idea/` | 0 | 3 |
| `docs/evaluation/idea_research_20260908/attempt_03_interrupted/agent_traces/idea/21cfb0564d934b54958ae80181d3987f/` | 3 | 3 |
| `docs/evaluation/idea_research_20260908/attempt_03_interrupted/idea/` | 1 | 1 |
| `docs/evaluation/idea_research_20260908/attempt_03_interrupted/input/` | 1 | 1 |
| `docs/evaluation/idea_research_20260908/attempt_04/` | 6 | 24 |
| `docs/evaluation/idea_research_20260908/attempt_04/agent_traces/` | 0 | 8 |
| `docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea/` | 0 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea/251dcd370ed44b74ad21151d8eb390b7/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/` | 0 | 6 |
| `docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/1d6f3119c34f44fc814a006f30528cd7/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/523fc5ed2dc448e1bb4a912271aecb65/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/69f760d68e43470f8d566cc1cdb4cd3b/` | 2 | 2 |
| `docs/evaluation/idea_research_20260908/attempt_04/idea/` | 0 | 9 |
| `docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/` | 0 | 9 |
| `docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/1d6f3119c34f44fc814a006f30528cd7/` | 3 | 3 |
| `docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/523fc5ed2dc448e1bb4a912271aecb65/` | 3 | 3 |
| `docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/69f760d68e43470f8d566cc1cdb4cd3b/` | 3 | 3 |
| `docs/evaluation/idea_research_20260908/attempt_04/input/` | 1 | 1 |
| `docs/evaluation/native_loop_20260907/` | 4 | 14 |
| `docs/evaluation/native_loop_20260907/react/` | 3 | 5 |
| `docs/evaluation/native_loop_20260907/react/tools/` | 2 | 2 |
| `docs/evaluation/native_loop_20260907/reflection/` | 3 | 5 |
| `docs/evaluation/native_loop_20260907/reflection/tools/` | 2 | 2 |
| `docs/interview/` | 3 | 3 |
| `frontend/` | 16 | 99 |
| `frontend/public/` | 0 | 4 |
| `frontend/public/personal/` | 4 | 4 |
| `frontend/scripts/` | 2 | 2 |
| `frontend/src/` | 0 | 77 |
| `frontend/src/app/` | 3 | 17 |
| `frontend/src/app/config/` | 1 | 3 |
| `frontend/src/app/config/agents/` | 1 | 1 |
| `frontend/src/app/config/yaml/` | 1 | 1 |
| `frontend/src/app/context/` | 1 | 1 |
| `frontend/src/app/discovery/` | 0 | 2 |
| `frontend/src/app/discovery/[id]/` | 1 | 1 |
| `frontend/src/app/discovery/candidates/` | 0 | 1 |
| `frontend/src/app/discovery/candidates/[candidateId]/` | 1 | 1 |
| `frontend/src/app/entries/` | 1 | 1 |
| `frontend/src/app/personal/` | 1 | 1 |
| `frontend/src/app/runs/` | 1 | 5 |
| `frontend/src/app/runs/[id]/` | 1 | 3 |
| `frontend/src/app/runs/[id]/idea-discovery/` | 1 | 1 |
| `frontend/src/app/runs/[id]/multi/` | 1 | 1 |
| `frontend/src/app/runs/new/` | 1 | 1 |
| `frontend/src/app/v31/` | 0 | 1 |
| `frontend/src/app/v31/runs/` | 0 | 1 |
| `frontend/src/app/v31/runs/new/` | 1 | 1 |
| `frontend/src/components/` | 21 | 21 |
| `frontend/src/contracts/` | 0 | 4 |
| `frontend/src/contracts/v31/` | 4 | 4 |
| `frontend/src/features/` | 0 | 24 |
| `frontend/src/features/discovery/` | 3 | 11 |
| `frontend/src/features/discovery/components/` | 8 | 8 |
| `frontend/src/features/idea-discovery/` | 8 | 8 |
| `frontend/src/features/project-pack/` | 5 | 5 |
| `frontend/src/lib/` | 8 | 8 |
| `frontend/src/pages/` | 1 | 1 |
| `frontend/src/stores/` | 1 | 1 |
| `frontend/src/types/` | 1 | 1 |
| `posttrain/` | 1 | 3 |
| `posttrain/src/` | 0 | 2 |
| `posttrain/src/mars_posttrain/` | 2 | 2 |
| `projects/` | 0 | 22 |
| `projects/pimc/` | 5 | 7 |
| `projects/pimc/context/` | 2 | 2 |
| `projects/synthetic_regression/` | 8 | 15 |
| `projects/synthetic_regression/src/` | 0 | 6 |
| `projects/synthetic_regression/src/synthetic_regression_adapter/` | 3 | 6 |
| `projects/synthetic_regression/src/synthetic_regression_adapter/resources/` | 3 | 3 |
| `projects/synthetic_regression/tests/` | 1 | 1 |
| `scripts/` | 26 | 32 |
| `scripts/release/` | 6 | 6 |
| `templates/` | 0 | 11 |
| `templates/artifacts/` | 10 | 10 |
| `templates/code_rules/` | 1 | 1 |
| `workspace/` | 0 | 1 |
| `workspace/repos/` | 1 | 1 |

## 全部文件

以下路径均相对于仓库根目录；此清单没有省略目录中的文件。

```text
.dockerignore
.env.example
.env.production.example
.github/workflows/ci.yml
.github/workflows/v30-release-gate.yml
.gitignore
.importlinter
ACCEPTANCE.md
ACCEPTANCE_V2.md
AGENTS.md
CLAUDE.md
DESIGN 2.md
DESIGN.md
LICENSE
PRODUCT 2.md
PRODUCT.md
README.md
README.zh-CN.md
backend/Dockerfile
backend/Dockerfile.prod
backend/app/__init__.py
backend/app/agents/__init__.py
backend/app/agents/base.py
backend/app/agents/coding/__init__.py
backend/app/agents/coding/agent.py
backend/app/agents/coding/docs/working_principles.md
backend/app/agents/coding/opencode_adapter.py
backend/app/agents/coding/prompts/code_spec.md
backend/app/agents/debate/__init__.py
backend/app/agents/debate/debate_runner.py
backend/app/agents/debate/judge.py
backend/app/agents/debate/roles.py
backend/app/agents/execution/__init__.py
backend/app/agents/execution/agent.py
backend/app/agents/execution/docs/working_principles.md
backend/app/agents/execution/prompts/run_log.md
backend/app/agents/experiment/__init__.py
backend/app/agents/experiment/agent.py
backend/app/agents/experiment/docs/working_principles.md
backend/app/agents/experiment/prompts/experiment_plan.md
backend/app/agents/idea/__init__.py
backend/app/agents/idea/acceptance.py
backend/app/agents/idea/agent.py
backend/app/agents/idea/delivery.py
backend/app/agents/idea/discovery/__init__.py
backend/app/agents/idea/discovery/backend.py
backend/app/agents/idea/discovery/contracts.py
backend/app/agents/idea/discovery/evolution.py
backend/app/agents/idea/discovery/generation.py
backend/app/agents/idea/discovery/meta_review.py
backend/app/agents/idea/discovery/models.py
backend/app/agents/idea/discovery/proximity.py
backend/app/agents/idea/discovery/ranking.py
backend/app/agents/idea/discovery/reflection.py
backend/app/agents/idea/discovery/storage.py
backend/app/agents/idea/discovery/workflow.py
backend/app/agents/idea/docs/pimc_notes.md
backend/app/agents/idea/docs/working_principles.md
backend/app/agents/idea/evals/quality_rubric.md
backend/app/agents/idea/examples/bad_proposal.md
backend/app/agents/idea/examples/good_proposal.md
backend/app/agents/idea/focused_agent.py
backend/app/agents/idea/focused_research.py
backend/app/agents/idea/prompts/debate_synthesis.md
backend/app/agents/idea/prompts/proposal.md
backend/app/agents/idea/prompts/research.md
backend/app/agents/idea/protocol.py
backend/app/agents/idea/publication_count.py
backend/app/agents/idea/research.py
backend/app/agents/idea/research_assessment.py
backend/app/agents/idea/research_brief.py
backend/app/agents/idea/research_cancellation.py
backend/app/agents/idea/research_delegate.py
backend/app/agents/idea/research_dossier.py
backend/app/agents/idea/research_gap.py
backend/app/agents/idea/research_handoff.py
backend/app/agents/idea/research_links.py
backend/app/agents/idea/research_origin.py
backend/app/agents/idea/research_review.py
backend/app/agents/idea/research_review_plan.py
backend/app/agents/idea/research_stop.py
backend/app/agents/idea/research_unit.py
backend/app/agents/idea/runtime_profile.py
backend/app/agents/idea/service_agent.py
backend/app/agents/idea/source_identity.py
backend/app/agents/research_cli.py
backend/app/agents/writing/__init__.py
backend/app/agents/writing/agent.py
backend/app/agents/writing/docs/working_principles.md
backend/app/agents/writing/prompts/report.md
backend/app/api/__init__.py
backend/app/api/agents.py
backend/app/api/artifacts.py
backend/app/api/chat.py
backend/app/api/config.py
backend/app/api/context.py
backend/app/api/data_sources.py
backend/app/api/dependencies.py
backend/app/api/diagnoses.py
backend/app/api/discovery.py
backend/app/api/evaluation.py
backend/app/api/events.py
backend/app/api/execution.py
backend/app/api/idea_materials.py
backend/app/api/knowledge.py
backend/app/api/llm_errors.py
backend/app/api/projects.py
backend/app/api/readiness.py
backend/app/api/reports.py
backend/app/api/runs.py
backend/app/api/runtime.py
backend/app/api/stats.py
backend/app/api/system.py
backend/app/api/templates.py
backend/app/api/timeline.py
backend/app/api/tools.py
backend/app/api/traces.py
backend/app/api/websocket.py
backend/app/bridge/__init__.py
backend/app/bridge/agent_progress.py
backend/app/bridge/agent_registry.py
backend/app/bridge/agent_runner.py
backend/app/bridge/bridge_agent.py
backend/app/bridge/candidate_workspace.py
backend/app/bridge/cli_research_service.py
backend/app/bridge/code_workspace_resolver.py
backend/app/bridge/commander.py
backend/app/bridge/commander_agent.py
backend/app/bridge/commander_eval.py
backend/app/bridge/commander_observability.py
backend/app/bridge/commander_session.py
backend/app/bridge/commander_tools.py
backend/app/bridge/diagnostics.py
backend/app/bridge/discovery_commander_tools.py
backend/app/bridge/discovery_composition.py
backend/app/bridge/discovery_core.py
backend/app/bridge/discovery_events.py
backend/app/bridge/discovery_service.py
backend/app/bridge/discovery_types.py
backend/app/bridge/docs/commander_principles.md
backend/app/bridge/evaluation_export_service.py
backend/app/bridge/evaluation_policy.py
backend/app/bridge/evaluation_service.py
backend/app/bridge/extension_runtime.py
backend/app/bridge/idea_input_context.py
backend/app/bridge/idea_selection.py
backend/app/bridge/langgraph_runtime.py
backend/app/bridge/node_key.py
backend/app/bridge/orchestrator.py
backend/app/bridge/owned_run_tasks.py
backend/app/bridge/prompts/commander_routing.md
backend/app/bridge/research_context.py
backend/app/bridge/run_observability.py
backend/app/bridge/workflow_service.py
backend/app/cli.py
backend/app/cli_composition.py
backend/app/execution/__init__.py
backend/app/execution/adapters/__init__.py
backend/app/execution/adapters/base.py
backend/app/execution/adapters/process.py
backend/app/execution/adapters/registry.py
backend/app/execution/adapters/workspace.py
backend/app/execution/batch_runner.py
backend/app/execution/curve_parser.py
backend/app/execution/log_streamer.py
backend/app/execution/metrics_collector.py
backend/app/execution/paper_static_adapter.py
backend/app/execution/pim_cancellation.py
backend/app/execution/pimc_static_worker.py
backend/app/execution/remote/__init__.py
backend/app/execution/remote/adapter.py
backend/app/execution/remote/adapter_worker.py
backend/app/execution/remote/executor.py
backend/app/execution/remote/records.py
backend/app/execution/remote/runner.py
backend/app/execution/remote/transport.py
backend/app/execution/research_process.py
backend/app/execution/results.py
backend/app/execution/simulation_runner.py
backend/app/execution/subprocess_env.py
backend/app/harness/__init__.py
backend/app/harness/agent_loop/__init__.py
backend/app/harness/agent_loop/completion_recovery.py
backend/app/harness/agent_loop/context.py
backend/app/harness/agent_loop/executor.py
backend/app/harness/agent_loop/native_protocol.py
backend/app/harness/agent_loop/policy.py
backend/app/harness/agent_loop/protocol.py
backend/app/harness/agent_loop/review.py
backend/app/harness/agent_loop/review_plan.py
backend/app/harness/agent_loop/stop.py
backend/app/harness/agent_loop/trace.py
backend/app/harness/context/__init__.py
backend/app/harness/context/budget_policy.py
backend/app/harness/context/compiler.py
backend/app/harness/context/engine.py
backend/app/harness/context/folder_context.py
backend/app/harness/context/loader.py
backend/app/harness/context/manifest.py
backend/app/harness/context/manifest_v2.py
backend/app/harness/context/project_knowledge.py
backend/app/harness/context/project_layer.py
backend/app/harness/context/raw_store.py
backend/app/harness/context/system_layer.py
backend/app/harness/context/task_layer.py
backend/app/harness/discovery/__init__.py
backend/app/harness/discovery/archive.py
backend/app/harness/discovery/candidate_builder.py
backend/app/harness/discovery/canonical.py
backend/app/harness/discovery/code_candidate.py
backend/app/harness/discovery/code_materialization.py
backend/app/harness/discovery/code_workspace_transfer.py
backend/app/harness/discovery/evaluation_aggregate.py
backend/app/harness/discovery/models.py
backend/app/harness/discovery/novelty.py
backend/app/harness/discovery/preflight.py
backend/app/harness/discovery/promotion.py
backend/app/harness/discovery/protocol.py
backend/app/harness/discovery/sampling.py
backend/app/harness/discovery/snapshots.py
backend/app/harness/discovery/source_commit.py
backend/app/harness/discovery/stopping.py
backend/app/harness/evaluation/__init__.py
backend/app/harness/evaluation/aggregation.py
backend/app/harness/evaluation/artifacts.py
backend/app/harness/evaluation/calibration.py
backend/app/harness/evaluation/evaluators/__init__.py
backend/app/harness/evaluation/evaluators/artifact_quality.py
backend/app/harness/evaluation/evaluators/contract.py
backend/app/harness/evaluation/models.py
backend/app/harness/evaluation/post_training_export.py
backend/app/harness/evaluation/registry.py
backend/app/harness/evaluation/rubrics.py
backend/app/harness/evaluation/run_evaluators.py
backend/app/harness/evaluation/run_report.py
backend/app/harness/evaluation/run_types.py
backend/app/harness/evaluation/runner.py
backend/app/harness/evaluation/self_evolution.py
backend/app/harness/evaluation/suite_report.py
backend/app/harness/evaluation/suites.py
backend/app/harness/execution_intent.py
backend/app/harness/gates/__init__.py
backend/app/harness/gates/baseline_compatibility.py
backend/app/harness/gates/conclusion_output.py
backend/app/harness/gates/experiment_launch.py
backend/app/harness/gates/gate_base.py
backend/app/harness/gates/large_refactor.py
backend/app/harness/gates/plan_finalized.py
backend/app/harness/kb/__init__.py
backend/app/harness/kb/backends.py
backend/app/harness/kb/baseline_matcher.py
backend/app/harness/kb/config.py
backend/app/harness/kb/consolidate.py
backend/app/harness/kb/embedder.py
backend/app/harness/kb/fingerprint.py
backend/app/harness/kb/ingester.py
backend/app/harness/kb/memory_writer.py
backend/app/harness/kb/models.py
backend/app/harness/kb/profiles.py
backend/app/harness/kb/provenance.py
backend/app/harness/kb/resolver.py
backend/app/harness/kb/retriever.py
backend/app/harness/kb/selector.py
backend/app/harness/kb/stores.py
backend/app/harness/llm/__init__.py
backend/app/harness/llm/anthropic_provider.py
backend/app/harness/llm/gemini_provider.py
backend/app/harness/llm/model_registry.py
backend/app/harness/llm/openai_provider.py
backend/app/harness/llm/post_training_loader.py
backend/app/harness/llm/provider_base.py
backend/app/harness/memory/__init__.py
backend/app/harness/memory/conflict.py
backend/app/harness/memory/episode.py
backend/app/harness/memory/evals.py
backend/app/harness/memory/importance.py
backend/app/harness/memory/injection.py
backend/app/harness/memory/selector_policy.py
backend/app/harness/memory/semantic.py
backend/app/harness/memory/usage.py
backend/app/harness/observability/__init__.py
backend/app/harness/observability/events.py
backend/app/harness/observability/langsmith_sink.py
backend/app/harness/observability/tracing.py
backend/app/harness/project_packs/__init__.py
backend/app/harness/project_packs/models.py
backend/app/harness/project_packs/registry.py
backend/app/harness/project_workspace.py
backend/app/harness/research_trial.py
backend/app/harness/runtime/__init__.py
backend/app/harness/runtime/conversation_state.py
backend/app/harness/runtime/distribution.py
backend/app/harness/runtime/event_bus.py
backend/app/harness/runtime/queue_manager.py
backend/app/harness/runtime/readiness.py
backend/app/harness/runtime/run_graph.py
backend/app/harness/runtime/state_machine.py
backend/app/harness/runtime/system_status.py
backend/app/harness/schema/__init__.py
backend/app/harness/schema/frontmatter_parser.py
backend/app/harness/schema/schemas/__init__.py
backend/app/harness/schema/schemas/code_spec.v1.json
backend/app/harness/schema/schemas/diagnosis.v1.json
backend/app/harness/schema/schemas/evaluation_report.v1.json
backend/app/harness/schema/schemas/experiment_plan.v1.json
backend/app/harness/schema/schemas/feedback_packet.v1.json
backend/app/harness/schema/schemas/proposal.v1.json
backend/app/harness/schema/schemas/report.v1.json
backend/app/harness/schema/schemas/report_bundle.v1.json
backend/app/harness/schema/schemas/research_report.v1.json
backend/app/harness/schema/schemas/run_log.v1.json
backend/app/harness/schema/validator.py
backend/app/harness/sedimentation/__init__.py
backend/app/harness/sedimentation/asset_metadata.py
backend/app/harness/sedimentation/extractors/__init__.py
backend/app/harness/sedimentation/hooks.py
backend/app/harness/tools/__init__.py
backend/app/harness/tools/code/__init__.py
backend/app/harness/tools/collaboration/__init__.py
backend/app/harness/tools/config.py
backend/app/harness/tools/execution/__init__.py
backend/app/harness/tools/knowledge/__init__.py
backend/app/harness/tools/mcp_adapters/__init__.py
backend/app/harness/tools/project_repo.py
backend/app/harness/tools/registry.py
backend/app/harness/tools/reporting/__init__.py
backend/app/harness/tools/search/__init__.py
backend/app/harness/tools/search/arxiv_query.py
backend/app/harness/tools/search/cvf.py
backend/app/harness/tools/search/neurips.py
backend/app/harness/tools/search/openalex.py
backend/app/harness/tools/search/source_fetch.py
backend/app/hitl/__init__.py
backend/app/hitl/approval.py
backend/app/hitl/audit_log.py
backend/app/hitl/diff_view.py
backend/app/hitl/review_session.py
backend/app/hitl/revision_loop.py
backend/app/main.py
backend/app/reporting/__init__.py
backend/app/reporting/bundle.py
backend/app/reporting/data_pack.py
backend/app/reporting/generators.py
backend/app/settings.py
backend/app/storage/__init__.py
backend/app/storage/agent_context_store.py
backend/app/storage/artifact_store.py
backend/app/storage/coding_workspace_store.py
backend/app/storage/data_source_store.py
backend/app/storage/discovery_archive_store.py
backend/app/storage/discovery_budget_ledger.py
backend/app/storage/discovery_candidate_store.py
backend/app/storage/discovery_checkpoint_store.py
backend/app/storage/discovery_common.py
backend/app/storage/discovery_lineage_store.py
backend/app/storage/discovery_promotion_store.py
backend/app/storage/discovery_search_state_store.py
backend/app/storage/run_state_store.py
backend/app/storage/run_store.py
backend/app/storage/self_evolution_store.py
backend/app/workers/__init__.py
backend/tests/__init__.py
backend/tests/baseline/__init__.py
backend/tests/baseline/test_baseline_matcher.py
backend/tests/e2e/__init__.py
backend/tests/e2e/discovery/test_idea_co_scientist_api_e2e.py
backend/tests/e2e/discovery/test_release_export_security.py
backend/tests/e2e/discovery/test_runtime_composition_e2e.py
backend/tests/e2e/discovery/test_synthetic_pack_e2e.py
backend/tests/gate/__init__.py
backend/tests/gate/test_gate_5_baseline_compatibility.py
backend/tests/gate/test_gates_1_to_4.py
backend/tests/integration/__init__.py
backend/tests/integration/test_agent_post_training_api.py
backend/tests/integration/test_agent_standalone_mock.py
backend/tests/integration/test_api_runs.py
backend/tests/integration/test_bridge_no_agent_import.py
backend/tests/integration/test_concurrent_execution.py
backend/tests/integration/test_discovery_api.py
backend/tests/integration/test_hitl_flow.py
backend/tests/integration/test_idea_feedback_replay_script.py
backend/tests/integration/test_idea_research_live_script.py
backend/tests/integration/test_idea_roles_live_script.py
backend/tests/integration/test_idea_standalone_script.py
backend/tests/integration/test_memory_v2_governance.py
backend/tests/integration/test_orchestrator_missing_agents.py
backend/tests/integration/test_pipeline_missing_provider.py
backend/tests/integration/test_research_context_intake.py
backend/tests/local_memory.py
backend/tests/real_discovery.py
backend/tests/schema/__init__.py
backend/tests/schema/test_schema_compliance.py
backend/tests/schema/test_template_files_pass.py
backend/tests/test_cli_research.py
backend/tests/unit/__init__.py
backend/tests/unit/agents/idea/test_deep_discovery.py
backend/tests/unit/agents/idea/test_evolution_feedback.py
backend/tests/unit/agents/idea/test_role_contracts.py
backend/tests/unit/bridge/test_agent_progress.py
backend/tests/unit/bridge/test_candidate_workspace.py
backend/tests/unit/bridge/test_code_workspace_resolver.py
backend/tests/unit/bridge/test_discovery_composition.py
backend/tests/unit/bridge/test_discovery_core.py
backend/tests/unit/bridge/test_discovery_service.py
backend/tests/unit/bridge/test_idea_selection.py
backend/tests/unit/bridge/test_orchestrator_external_projection.py
backend/tests/unit/discovery/__init__.py
backend/tests/unit/discovery/test_archive.py
backend/tests/unit/discovery/test_candidate_builder.py
backend/tests/unit/discovery/test_code_candidate.py
backend/tests/unit/discovery/test_code_candidate_preflight.py
backend/tests/unit/discovery/test_code_materialization.py
backend/tests/unit/discovery/test_code_workspace_transfer.py
backend/tests/unit/discovery/test_evaluation_aggregate.py
backend/tests/unit/discovery/test_novelty.py
backend/tests/unit/discovery/test_policy_and_preflight.py
backend/tests/unit/discovery/test_sampling.py
backend/tests/unit/discovery/test_snapshots.py
backend/tests/unit/storage/test_discovery_archive_lineage_store.py
backend/tests/unit/storage/test_discovery_budget_ledger.py
backend/tests/unit/storage/test_discovery_candidate_store.py
backend/tests/unit/storage/test_discovery_checkpoint_store.py
backend/tests/unit/storage/test_discovery_promotion_store.py
backend/tests/unit/storage/test_discovery_search_state_store.py
backend/tests/unit/test_action_protocol_feedback.py
backend/tests/unit/test_agent_context_store.py
backend/tests/unit/test_agent_loop.py
backend/tests/unit/test_agent_loop_stop.py
backend/tests/unit/test_artifact_store.py
backend/tests/unit/test_arxiv_exact_lookup.py
backend/tests/unit/test_author_empty_completion_recovery.py
backend/tests/unit/test_bridge_evaluation_service.py
backend/tests/unit/test_coding_post_training.py
backend/tests/unit/test_coding_workspace_store.py
backend/tests/unit/test_commander_agent_feedback.py
backend/tests/unit/test_commander_runtime.py
backend/tests/unit/test_commander_tools.py
backend/tests/unit/test_config_api_v2.py
backend/tests/unit/test_context_engine_v2.py
backend/tests/unit/test_context_loader.py
backend/tests/unit/test_context_runtime_v2.py
backend/tests/unit/test_cvf_research_integration.py
backend/tests/unit/test_cvf_sources.py
backend/tests/unit/test_data_source_store.py
backend/tests/unit/test_debate_runner.py
backend/tests/unit/test_diagnostics.py
backend/tests/unit/test_evaluation_calibration.py
backend/tests/unit/test_evaluation_policy.py
backend/tests/unit/test_evaluation_runner.py
backend/tests/unit/test_evaluation_suite_report.py
backend/tests/unit/test_event_bus.py
backend/tests/unit/test_execution_intent.py
backend/tests/unit/test_execution_tools_v2.py
backend/tests/unit/test_external_review.py
backend/tests/unit/test_focused_idea.py
backend/tests/unit/test_folder_projects.py
backend/tests/unit/test_frontmatter_parser.py
backend/tests/unit/test_idea_agent_v2.py
backend/tests/unit/test_idea_api_failure_archive.py
backend/tests/unit/test_idea_audit_versions.py
backend/tests/unit/test_idea_budget_cases.py
backend/tests/unit/test_idea_delivery.py
backend/tests/unit/test_idea_delivery_audit.py
backend/tests/unit/test_idea_input_context.py
backend/tests/unit/test_idea_input_evidence.py
backend/tests/unit/test_idea_lead_tool_profile.py
backend/tests/unit/test_idea_live_resume.py
backend/tests/unit/test_idea_live_verification.py
backend/tests/unit/test_idea_materials.py
backend/tests/unit/test_idea_mechanism_guidance.py
backend/tests/unit/test_idea_pointer_guidance.py
backend/tests/unit/test_idea_primary_case_identity.py
backend/tests/unit/test_idea_protocol.py
backend/tests/unit/test_idea_research_continuation.py
backend/tests/unit/test_idea_research_links.py
backend/tests/unit/test_idea_research_stop.py
backend/tests/unit/test_idea_revision_parent.py
backend/tests/unit/test_idea_runtime_profile.py
backend/tests/unit/test_idea_single_publication_profile.py
backend/tests/unit/test_json_action_author_contract.py
backend/tests/unit/test_langgraph_runtime_v2.py
backend/tests/unit/test_langsmith_observability.py
backend/tests/unit/test_live_failure_contracts.py
backend/tests/unit/test_llm_api_errors.py
backend/tests/unit/test_llm_deadline.py
backend/tests/unit/test_local_memory_provenance.py
backend/tests/unit/test_log_streamer.py
backend/tests/unit/test_loop_budget_context.py
backend/tests/unit/test_mcp_adapters.py
backend/tests/unit/test_memory_system_complete.py
backend/tests/unit/test_memory_v2.py
backend/tests/unit/test_mock_provider.py
backend/tests/unit/test_mock_simulation.py
backend/tests/unit/test_model_registry.py
backend/tests/unit/test_native_document_arguments.py
backend/tests/unit/test_native_observation_history.py
backend/tests/unit/test_native_tool_protocol.py
backend/tests/unit/test_neurips_sources.py
backend/tests/unit/test_observability_events.py
backend/tests/unit/test_openai_provider.py
backend/tests/unit/test_openalex_search.py
backend/tests/unit/test_owned_run_cancellation.py
backend/tests/unit/test_paper_static_adapter.py
backend/tests/unit/test_pim_cancellation.py
backend/tests/unit/test_post_training_export.py
backend/tests/unit/test_post_training_loader.py
backend/tests/unit/test_posttrain_dry_run.py
backend/tests/unit/test_process_adapter.py
backend/tests/unit/test_protocol_repair_context.py
backend/tests/unit/test_provider_error_diagnostics.py
backend/tests/unit/test_public_research_context.py
backend/tests/unit/test_publication_count.py
backend/tests/unit/test_readiness.py
backend/tests/unit/test_real_loop_contracts.py
backend/tests/unit/test_remote_adapter.py
backend/tests/unit/test_remote_executor.py
backend/tests/unit/test_remote_runner.py
backend/tests/unit/test_reporting_bundle_v2.py
backend/tests/unit/test_required_review_evidence.py
backend/tests/unit/test_research_assessment.py
backend/tests/unit/test_research_brief.py
backend/tests/unit/test_research_collect_review.py
backend/tests/unit/test_research_delegation.py
backend/tests/unit/test_research_dossier.py
backend/tests/unit/test_research_evidence_scope.py
backend/tests/unit/test_research_gap.py
backend/tests/unit/test_research_handoff.py
backend/tests/unit/test_research_origin.py
backend/tests/unit/test_research_quote_feedback.py
backend/tests/unit/test_research_quote_locator.py
backend/tests/unit/test_research_review_context.py
backend/tests/unit/test_research_review_plan.py
backend/tests/unit/test_research_runtime_failure.py
backend/tests/unit/test_research_source_identity.py
backend/tests/unit/test_research_title_aliases.py
backend/tests/unit/test_research_unit.py
backend/tests/unit/test_review_format_repair.py
backend/tests/unit/test_review_plan_collect.py
backend/tests/unit/test_review_plan_runtime.py
backend/tests/unit/test_run_evaluation_replay.py
backend/tests/unit/test_run_graph.py
backend/tests/unit/test_run_state_store.py
backend/tests/unit/test_run_store.py
backend/tests/unit/test_run_store_trash.py
backend/tests/unit/test_runtime_status.py
backend/tests/unit/test_search_tools_v2.py
backend/tests/unit/test_sedimentation.py
backend/tests/unit/test_settings_env.py
backend/tests/unit/test_source_fetch_reliability.py
backend/tests/unit/test_source_receipt_context.py
backend/tests/unit/test_source_size_budget.py
backend/tests/unit/test_state_machine.py
backend/tests/unit/test_structured_final_contract.py
backend/tests/unit/test_subprocess_env.py
backend/tests/unit/test_timeline_v2.py
backend/tests/unit/test_tools_hardening.py
backend/tests/unit/test_trace_progress_cli.py
backend/tests/unit/test_v31_contracts.py
backend/tests/unit/test_v31_extension_runtime.py
backend/tests/unit/test_validator.py
backend/tests/unit/test_windows_docker_deployment.py
backend/tests/unit/test_workflow_service.py
backend/tests/unit/test_writing_agent_metadata.py
configs/agent_contexts/coding.yaml
configs/agent_contexts/commander.yaml
configs/agent_contexts/execution.yaml
configs/agent_contexts/experiment.yaml
configs/agent_contexts/idea.yaml
configs/agent_contexts/writing.yaml
configs/agents.yaml
configs/cli_research.yaml
configs/context.yaml
configs/evaluation.yaml
configs/evaluation/commander_attribution_cases.yaml
configs/evaluation/idea_2d_lut_deepseek_react.yaml
configs/evaluation/idea_2d_lut_public_urls_real.yaml
configs/evaluation/idea_2d_lut_real.yaml
configs/evaluation/idea_delivery_real.yaml
configs/evaluation/idea_research_delegated_real.yaml
configs/evaluation/idea_research_per_insight_real.yaml
configs/evaluation/idea_research_publisher_real.yaml
configs/evaluation/idea_research_single_publication_real.yaml
configs/evaluation/idea_research_thinking_json_real.yaml
configs/evaluation/idea_roles_component_real.yaml
configs/evaluation_datasets/mars_multi_agent_trajectory_example.annotated.jsonc
configs/evaluation_datasets/mars_multi_agent_trajectory_example.json
configs/evaluation_rubrics/code_spec.v1.yaml
configs/evaluation_rubrics/diagnosis.v1.yaml
configs/evaluation_rubrics/experiment_plan.v1.yaml
configs/evaluation_rubrics/proposal.v1.yaml
configs/evaluation_rubrics/report.v1.yaml
configs/evaluation_rubrics/run_log.v1.yaml
configs/evaluation_suites/mars_live_smoke_v0.yaml
configs/evaluation_suites/mars_run_replay_v0.yaml
configs/execution.yaml
configs/frontend.yaml
configs/gates.yaml
configs/idea_focused.yaml
configs/idea_runtime_profiles.yaml
configs/knowledge.yaml
configs/memory.yaml
configs/models.yaml
configs/observability.yaml
configs/reporting.yaml
configs/tools.yaml
configs/workflow.yaml
deploy/nginx/mars-api.conf
deploy/nginx/mars-fullstack.conf
deploy/windows/Common.ps1
deploy/windows/Dockerfile.backend
deploy/windows/Dockerfile.frontend
deploy/windows/Export-MarsImages.ps1
deploy/windows/README.md
deploy/windows/Start-Mars.ps1
deploy/windows/Status-Mars.ps1
deploy/windows/Stop-Mars.ps1
deploy/windows/Test-Mars.ps1
deploy/windows/VALIDATION.md
deploy/windows/compose.production.yaml
deploy/windows/compose.yaml
deploy/windows/repo_link.demo.yaml
deploy/windows/repo_link.production.yaml
deploy/windows/start-mars-offline.cmd
deploy/windows/start-mars-production-offline.cmd
deploy/windows/start-mars-production.cmd
deploy/windows/start-mars.cmd
deploy/windows/status-mars.cmd
deploy/windows/stop-mars.cmd
deploy/windows/tests/Test-DeploymentScripts.ps1
deploy/windows/windows.env.example
docker-compose.prod.yml
docker-compose.yml
docs/MARS_V3_DEVELOPMENT_REQUIREMENTS.md
docs/MARS_V3_WIRELESS_LIGHTWEIGHT_DISCOVERY_PLAN.md
docs/V2_AGENT_TODO.md
docs/V2_RELEASE_STATUS.md
docs/V30_PUBLIC_RELEASE_GATE.md
docs/V30_RELEASE_MIGRATION_PLAN.md
docs/V30_V31_COMPATIBILITY_ACCEPTANCE.md
docs/agent_context_blueprints.md
docs/agent_io_schema.md
docs/architecture 2.md
docs/architecture.md
docs/assets/readme/mars-hero.png
docs/assets/readme/mars-workbench-full.png
docs/cli-research.md
docs/core-code-map.md
docs/deployment_runbook.md
docs/engineering/idea_research_development.md
docs/engineering/native_idea_loop.md
docs/evaluation/idea_context_intake_20260908.md
docs/evaluation/idea_delivery_20260907.md
docs/evaluation/idea_delivery_proposal_20260907.json
docs/evaluation/idea_delivery_proposal_20260907.md
docs/evaluation/idea_multiagent_20260908/README.md
docs/evaluation/idea_multiagent_20260908/feedback_replay/child_drafts.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/child_records.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/input/request.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/reviewed_children.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/reviews.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0001_evolution/record.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0001_evolution/request.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0001_evolution/response.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0002_reflection/record.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0002_reflection/request.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/role_calls/0002_reflection/response.json
docs/evaluation/idea_multiagent_20260908/feedback_replay/role_trace/events.jsonl
docs/evaluation/idea_multiagent_20260908/feedback_replay/summary.json
docs/evaluation/idea_multiagent_20260908/inspect_previous_candidate.py
docs/evaluation/idea_multiagent_20260908/previous_candidate/numerical_inspection.json
docs/evaluation/idea_multiagent_20260908/previous_candidate/proposal.md
docs/evaluation/idea_multiagent_20260908/previous_candidate/provenance.json
docs/evaluation/idea_multiagent_20260908/previous_candidate/review_issues.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/generation_raw.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/checkpoint.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/hypotheses.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/hypothesis_pool.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/meta_reviews.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/pairwise_matches.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/proximity_graphs.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/reflections.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/idea/discovery/state.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/input/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/input/scenario.yaml
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/role_calls/0001_generation/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/role_calls/0001_generation/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/role_calls/0001_generation/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/role_trace/events.jsonl
docs/evaluation/idea_multiagent_20260908/roles_attempt_01/summary.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/full_trace.zip
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/checkpoint.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/hypotheses.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/hypothesis_pool.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/meta_reviews.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/pairwise_matches.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/proximity_graphs.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/reflections.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/idea/discovery/state.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/input/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/input/scenario.yaml
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0001_generation/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0001_generation/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0001_generation/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0002_reflection/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0002_reflection/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0002_reflection/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0003_pairwise_judge/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0003_pairwise_judge/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0003_pairwise_judge/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0004_meta_review/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0004_meta_review/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0004_meta_review/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0005_evolution/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0005_evolution/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0005_evolution/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0006_reflection/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0006_reflection/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0006_reflection/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0007_pairwise_judge/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0007_pairwise_judge/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0007_pairwise_judge/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0008_meta_review/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0008_meta_review/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_calls/0008_meta_review/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/role_trace/events.jsonl
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/summary.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_02/trace_manifest.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/checkpoint.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/hypotheses.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/hypothesis_pool.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/meta_reviews.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/pairwise_matches.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/proximity_graphs.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/reflections.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/idea/discovery/state.v1.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/input/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/input/scenario.yaml
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0001_generation/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0001_generation/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0001_generation/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0002_reflection/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0002_reflection/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0002_reflection/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0003_meta_review/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0003_meta_review/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0003_meta_review/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0004_meta_review/record.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0004_meta_review/request.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_calls/0004_meta_review/response.json
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/role_trace/events.jsonl
docs/evaluation/idea_multiagent_20260908/roles_attempt_03/summary.json
docs/evaluation/idea_protocol_20260908.md
docs/evaluation/idea_quality_20260908/README.md
docs/evaluation/idea_quality_20260908/api_verification_20260909.json
docs/evaluation/idea_quality_20260908/api_verification_20260909_attempt02.json
docs/evaluation/idea_quality_20260908/api_verification_20260909_attempt03.json
docs/evaluation/idea_quality_20260908/api_verification_20260909_attempt04.json
docs/evaluation/idea_quality_20260908/api_verification_20260909_attempt05.json
docs/evaluation/idea_quality_20260908/api_verification_20260909_attempt06.json
docs/evaluation/idea_quality_20260908/arxiv_transport_component_20260909.json
docs/evaluation/idea_quality_20260908/cvf_source_component_20260909.json
docs/evaluation/idea_quality_20260908/json_actions_format_component_20260909.json
docs/evaluation/idea_quality_20260908/neurips_registered_component_20260909.json
docs/evaluation/idea_quality_20260908/review_evidence_scope_v4_20260909.json
docs/evaluation/idea_quality_20260908/verification.json
docs/evaluation/idea_quality_20260908/verification_errata_20260909.json
docs/evaluation/idea_react_20260907/RESULTS.md
docs/evaluation/idea_react_20260907/external_review.md
docs/evaluation/idea_research_20260908.md
docs/evaluation/idea_research_20260908/attempt_01/all_traces_audit.json
docs/evaluation/idea_research_20260908/attempt_01/idea_candidate.md
docs/evaluation/idea_research_20260908/attempt_01/idea_checkpoint.json
docs/evaluation/idea_research_20260908/attempt_01/idea_research_candidate.md
docs/evaluation/idea_research_20260908/attempt_01/idea_research_checkpoint.json
docs/evaluation/idea_research_20260908/attempt_01/input/request.json
docs/evaluation/idea_research_20260908/attempt_01/research_findings.json
docs/evaluation/idea_research_20260908/attempt_01/review.md
docs/evaluation/idea_research_20260908/attempt_01/summary.json
docs/evaluation/idea_research_20260908/attempt_02/ASSESSMENT.md
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea/612a400c539546538a263ac664e4aafc/candidate.md
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea/612a400c539546538a263ac664e4aafc/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/50b4f21ee4564ff498f91a16628c91e0/candidate.md
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/50b4f21ee4564ff498f91a16628c91e0/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/527b962fd6104392b2d2d5406577c92a/candidate.md
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/527b962fd6104392b2d2d5406577c92a/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/5634b15ad474460da1fbf09072a308db/candidate.md
docs/evaluation/idea_research_20260908/attempt_02/agent_traces/idea_research/5634b15ad474460da1fbf09072a308db/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_02/all_traces_audit.json
docs/evaluation/idea_research_20260908/attempt_02/archive_manifest.json
docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/50b4f21ee4564ff498f91a16628c91e0/failure.json
docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/50b4f21ee4564ff498f91a16628c91e0/request.json
docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/527b962fd6104392b2d2d5406577c92a/failure.json
docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/527b962fd6104392b2d2d5406577c92a/request.json
docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/5634b15ad474460da1fbf09072a308db/failure.json
docs/evaluation/idea_research_20260908/attempt_02/idea/research_delegations/5634b15ad474460da1fbf09072a308db/request.json
docs/evaluation/idea_research_20260908/attempt_02/input/request.json
docs/evaluation/idea_research_20260908/attempt_02/research_findings.json
docs/evaluation/idea_research_20260908/attempt_02/review.md
docs/evaluation/idea_research_20260908/attempt_02/summary.json
docs/evaluation/idea_research_20260908/attempt_03_interrupted/README.md
docs/evaluation/idea_research_20260908/attempt_03_interrupted/agent_traces/idea/21cfb0564d934b54958ae80181d3987f/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_03_interrupted/agent_traces/idea/21cfb0564d934b54958ae80181d3987f/events.jsonl
docs/evaluation/idea_research_20260908/attempt_03_interrupted/agent_traces/idea/21cfb0564d934b54958ae80181d3987f/facts.json
docs/evaluation/idea_research_20260908/attempt_03_interrupted/archive_manifest.json
docs/evaluation/idea_research_20260908/attempt_03_interrupted/idea/progress.jsonl
docs/evaluation/idea_research_20260908/attempt_03_interrupted/input/request.json
docs/evaluation/idea_research_20260908/attempt_03_interrupted/summary.json
docs/evaluation/idea_research_20260908/attempt_04/ASSESSMENT.md
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea/251dcd370ed44b74ad21151d8eb390b7/candidate.md
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea/251dcd370ed44b74ad21151d8eb390b7/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/1d6f3119c34f44fc814a006f30528cd7/candidate.md
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/1d6f3119c34f44fc814a006f30528cd7/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/523fc5ed2dc448e1bb4a912271aecb65/candidate.md
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/523fc5ed2dc448e1bb4a912271aecb65/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/69f760d68e43470f8d566cc1cdb4cd3b/candidate.md
docs/evaluation/idea_research_20260908/attempt_04/agent_traces/idea_research/69f760d68e43470f8d566cc1cdb4cd3b/checkpoint.json
docs/evaluation/idea_research_20260908/attempt_04/all_traces_audit.json
docs/evaluation/idea_research_20260908/attempt_04/archive_manifest.json
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/1d6f3119c34f44fc814a006f30528cd7/manifest.json
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/1d6f3119c34f44fc814a006f30528cd7/report.md
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/1d6f3119c34f44fc814a006f30528cd7/request.json
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/523fc5ed2dc448e1bb4a912271aecb65/manifest.json
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/523fc5ed2dc448e1bb4a912271aecb65/report.md
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/523fc5ed2dc448e1bb4a912271aecb65/request.json
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/69f760d68e43470f8d566cc1cdb4cd3b/manifest.json
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/69f760d68e43470f8d566cc1cdb4cd3b/report.md
docs/evaluation/idea_research_20260908/attempt_04/idea/research_delegations/69f760d68e43470f8d566cc1cdb4cd3b/request.json
docs/evaluation/idea_research_20260908/attempt_04/input/request.json
docs/evaluation/idea_research_20260908/attempt_04/research_findings.json
docs/evaluation/idea_research_20260908/attempt_04/review.md
docs/evaluation/idea_research_20260908/attempt_04/summary.json
docs/evaluation/native_loop_20260907/a.json
docs/evaluation/native_loop_20260907/b.json
docs/evaluation/native_loop_20260907/react/checkpoint.json
docs/evaluation/native_loop_20260907/react/events.jsonl
docs/evaluation/native_loop_20260907/react/facts.json
docs/evaluation/native_loop_20260907/react/tools/0001.json
docs/evaluation/native_loop_20260907/react/tools/0002.json
docs/evaluation/native_loop_20260907/reflection/checkpoint.json
docs/evaluation/native_loop_20260907/reflection/events.jsonl
docs/evaluation/native_loop_20260907/reflection/facts.json
docs/evaluation/native_loop_20260907/reflection/tools/0001.json
docs/evaluation/native_loop_20260907/reflection/tools/0002.json
docs/evaluation/native_loop_20260907/source.json
docs/evaluation/native_loop_20260907/summary.json
docs/evaluation_system.md
docs/folder-projects.md
docs/frontend_ux.md
docs/idea-agent-live-verification-2026-09-14.md
docs/idea-agent-usable-workflow.md
docs/idea_delivery_contract.md
docs/idea_live_review_20260907.md
docs/idea_materials_workbench.md
docs/idea_reliability_20260908.md
docs/idea_research_quality.md
docs/idea_service_runtime_profiles.md
docs/implementation_report.md
docs/interview/interview_script_EN.md
docs/interview/面试稿_中文.md
docs/interview/项目评估_assessment.md
docs/mars-v3-latest-architecture-editable.svg
docs/mars_evaluation_harness_overview.svg
docs/mars_evaluation_self_evolution_loop.svg
docs/mars_memory_development_roadmap.svg
docs/mars_memory_system_overview.svg
docs/memory_management_summary_副本.html
docs/memory_system.md
docs/minimal_agent_loop.md
docs/no_mock_rebuild_2026_09_07.md
docs/observability_design.md
docs/phase_0_status.md
docs/phase_1_status.md
docs/phase_2_status.md
docs/phase_3_status.md
docs/phase_4_status.md
docs/phase_5_status.md
docs/phase_6_status.md
docs/phase_7_status.md
docs/real_only_test_migration.md
docs/repository-inventory.md
docs/run_lifecycle.md
docs/tool_security.md
docs/tools_catalog.md
frontend/.dockerignore
frontend/.eslintrc.json
frontend/.gitignore
frontend/Dockerfile
frontend/Dockerfile.prod
frontend/next-env.d.ts
frontend/next.config.mjs
frontend/package-lock.json
frontend/package.json
frontend/pnpm-lock.yaml
frontend/pnpm-workspace.yaml
frontend/postcss.config.mjs
frontend/public/personal/intro.png
frontend/public/personal/mars.png
frontend/public/personal/ops-llm.png
frontend/public/personal/self-evolving-agent.png
frontend/scripts/clean-next-types.cjs
frontend/scripts/context-workbench-smoke.ts
frontend/src/app/config/agents/page.tsx
frontend/src/app/config/page.tsx
frontend/src/app/config/yaml/page.tsx
frontend/src/app/context/page.tsx
frontend/src/app/discovery/[id]/page.tsx
frontend/src/app/discovery/candidates/[candidateId]/page.tsx
frontend/src/app/entries/page.tsx
frontend/src/app/globals.css
frontend/src/app/layout.tsx
frontend/src/app/page.tsx
frontend/src/app/personal/page.tsx
frontend/src/app/runs/[id]/idea-discovery/page.tsx
frontend/src/app/runs/[id]/multi/page.tsx
frontend/src/app/runs/[id]/page.tsx
frontend/src/app/runs/new/page.tsx
frontend/src/app/runs/page.tsx
frontend/src/app/v31/runs/new/page.tsx
frontend/src/components/AgentContextPanel.tsx
frontend/src/components/ChatPanel.tsx
frontend/src/components/CodingWorkspacePanel.tsx
frontend/src/components/ConfigWorkbench.tsx
frontend/src/components/DataSourcePrepPanel.tsx
frontend/src/components/EventLog.tsx
frontend/src/components/FolderProjectDialog.tsx
frontend/src/components/HumanFeedback.tsx
frontend/src/components/IdeaProposalDetails.tsx
frontend/src/components/IdeaRunMaterials.tsx
frontend/src/components/KBPanel.tsx
frontend/src/components/PipelineOverview.tsx
frontend/src/components/ProjectContextFiles.tsx
frontend/src/components/ProjectSwitcher.tsx
frontend/src/components/ProjectsPanel.tsx
frontend/src/components/ReportsPanel.tsx
frontend/src/components/ResearchPdfPreview.tsx
frontend/src/components/RuntimeOpsPanel.tsx
frontend/src/components/SidebarToggleButton.tsx
frontend/src/components/TimelinePanel.tsx
frontend/src/components/TopBar.tsx
frontend/src/contracts/v31/compatibility.ts
frontend/src/contracts/v31/idea-discovery.ts
frontend/src/contracts/v31/model-discovery.ts
frontend/src/contracts/v31/project-pack.ts
frontend/src/features/discovery/api.ts
frontend/src/features/discovery/components/AuditPanel.tsx
frontend/src/features/discovery/components/BudgetPanel.tsx
frontend/src/features/discovery/components/CandidateTable.tsx
frontend/src/features/discovery/components/CandidateWorkbench.tsx
frontend/src/features/discovery/components/LineagePanel.tsx
frontend/src/features/discovery/components/ParetoPanel.tsx
frontend/src/features/discovery/components/Primitives.tsx
frontend/src/features/discovery/components/RunWorkbench.tsx
frontend/src/features/discovery/selectors.ts
frontend/src/features/discovery/types.ts
frontend/src/features/idea-discovery/AddHypothesisForm.tsx
frontend/src/features/idea-discovery/DiscoveryStagePanels.tsx
frontend/src/features/idea-discovery/HypothesisReviewPanel.tsx
frontend/src/features/idea-discovery/IdeaDiscoveryWorkbench.tsx
frontend/src/features/idea-discovery/ProximityMap.tsx
frontend/src/features/idea-discovery/api.ts
frontend/src/features/idea-discovery/normalize.ts
frontend/src/features/idea-discovery/types.ts
frontend/src/features/project-pack/DynamicProjectPackForm.tsx
frontend/src/features/project-pack/ProjectPackRunCreator.tsx
frontend/src/features/project-pack/api.ts
frontend/src/features/project-pack/schema.ts
frontend/src/features/project-pack/types.ts
frontend/src/lib/api.ts
frontend/src/lib/contextWorkbench.ts
frontend/src/lib/dashboard.ts
frontend/src/lib/dataSourceSelection.ts
frontend/src/lib/i18n.tsx
frontend/src/lib/project.tsx
frontend/src/lib/socket.ts
frontend/src/lib/utils.ts
frontend/src/pages/_document.tsx
frontend/src/stores/run-store.ts
frontend/src/types/react-jsx.d.ts
frontend/tailwind.config.ts
frontend/tsconfig.json
frontend/tsconfig.tsbuildinfo
frontend/vercel.json
posttrain/README.md
posttrain/src/mars_posttrain/__init__.py
posttrain/src/mars_posttrain/py.typed
projects/pimc/AGENTS.md
projects/pimc/context/public_context.md
projects/pimc/context/task_pimc_static_algorithm.md
projects/pimc/data_gen.py
projects/pimc/diagnostics.yaml
projects/pimc/project.yaml
projects/pimc/repo_link.yaml
projects/synthetic_regression/AGENTS.md
projects/synthetic_regression/README.md
projects/synthetic_regression/project.yaml
projects/synthetic_regression/project_pack.yaml
projects/synthetic_regression/pyproject.toml
projects/synthetic_regression/repo_link.yaml
projects/synthetic_regression/src/synthetic_regression_adapter/__init__.py
projects/synthetic_regression/src/synthetic_regression_adapter/__main__.py
projects/synthetic_regression/src/synthetic_regression_adapter/adapter.py
projects/synthetic_regression/src/synthetic_regression_adapter/resources/dataset.json
projects/synthetic_regression/src/synthetic_regression_adapter/resources/discovery.yaml
projects/synthetic_regression/src/synthetic_regression_adapter/resources/metrics.yaml
projects/synthetic_regression/tests/test_adapter_contract.py
projects/synthetic_regression/ui_schema.json
projects/synthetic_regression/workflow.yaml
pyproject.toml
scripts/audit_focused_idea.py
scripts/audit_idea_agent_run.py
scripts/audit_idea_run.py
scripts/audit_real_test_migration.py
scripts/check_idea_models.py
scripts/check_native_loop_live.py
scripts/cli_validate.py
scripts/dev.sh
scripts/evaluate_run.py
scripts/export_evaluation_calibration.py
scripts/idea_live_resume.py
scripts/idea_research_continuation.py
scripts/ingest_pdfs.py
scripts/ingest_repo.py
scripts/release/README.md
scripts/release/__init__.py
scripts/release/export_v30.py
scripts/release/gitleaks_wrapper.py
scripts/release/run_synthetic_smoke.py
scripts/release/v30_tree_allowlist.txt
scripts/repair_idea_candidate_live.py
scripts/replay_idea_feedback_live.py
scripts/revise_idea_candidate.py
scripts/run_evaluation_suite.py
scripts/run_idea_lut_live.py
scripts/run_idea_research_live.py
scripts/run_idea_roles_live.py
scripts/run_real_commander_e2e.py
scripts/v2_release_check.sh
scripts/verify_focused_idea.py
scripts/verify_tools_v2_acceptance.py
scripts/watch_agent_trace.py
start-mars-windows-offline.cmd
start-mars-windows-production-offline.cmd
start-mars-windows-production.cmd
start-mars-windows.cmd
status-mars-windows.cmd
stop-mars-windows.cmd
templates/artifacts/code_spec.v1.md
templates/artifacts/diagnosis.v1.md
templates/artifacts/evaluation_report.v1.md
templates/artifacts/experiment_plan.v1.md
templates/artifacts/feedback_packet.v1.md
templates/artifacts/proposal.v1.md
templates/artifacts/report.v1.md
templates/artifacts/report_bundle.v1.md
templates/artifacts/research_report.v1.md
templates/artifacts/run_log.v1.md
templates/code_rules/pimc_python.md
uv.lock
workspace/repos/README.md
```
