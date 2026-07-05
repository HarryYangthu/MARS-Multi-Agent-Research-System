# Phase 3 Status — LLM Backend + Agent Skeletons

## Sub-acceptance

| Item | Status | Evidence |
|---|---|---|
| `harness/llm/provider_base.py` (LLMProvider ABC) | ✓ | `complete()` + async-generator `stream()`; `Message` / `LLMConfig` / `Completion` / `Delta` |
| Anthropic / OpenAI / Qwen / Gemini providers | ✓ | `anthropic_provider.py` + `openai_provider.py` (covers OpenAI + Qwen + local_vllm + custom) + `gemini_provider.py` |
| `local_vllm_provider` (OpenAI-compatible) | ✓ | `LocalVllmProvider` w/ `base_url`+`api_key` (default empty so it's "available" only when configured) |
| `custom_endpoint_provider` | ✓ | `CustomEndpointProvider` |
| ★ `mock_provider.py` | ✓ | Per-schema fake builders for all 5 schemas; **every output validates 100% against its schema** (regression test enforces this) |
| `post_training_loader.py` (load-only) | ✓ | 4 modes: `load_only` / `adapter` / `endpoint` / `fine_tuned_id`; defensive validation per mode |
| `model_registry.py` | ✓ | Reads `configs/agents.yaml`; `select_provider()` falls back to mock when API key missing; logs warning |
| `agents.yaml` / `models.yaml` / `tools.yaml` | ✓ | 5 agents declared; matches PRODUCT §11.1 |
| `BaseAgent` ABC | ✓ | `build_context` / `draft` / `revise` / `validate_output` / `submit_for_review`; `_call_llm` with mock fallback on provider error |
| 5 concrete Agents | ✓ | `IdeaAgent` (debate-on) / `ExperimentAgent` / `CodingAgent` (post_training handle) / `ExecutionAgent` / `WritingAgent` (debate-on) |
| `agents/debate/` 3 modes | ✓ | `real_multi_model` / `single_model_simulated` / `mock_debate`; `_auto_mode()` matches DESIGN §16.3 |
| Bridge `agent_runner.py` | ✓ | NodeRunner that builds RunRequest from on-disk state, drafts, validates, persists; auto-promotes to approved (Phase 4 will wire HITL) |
| Mock fallback regression | ✓ | `tests/unit/test_mock_provider.py` parametrized over all 5 schemas — 100% validate |
| 5 Agent standalone draft | ✓ | `tests/integration/test_agent_standalone_mock.py` |
| Full pipeline e2e under mock | ✓ | `tests/integration/test_pipeline_full_mock_run.py` walks Idea→Writing under mock_provider; every artifact validates |
| `mypy --strict` clean | ✓ | "Success: no issues found in 64 source files" |
| `lint-imports` 4/4 KEPT | ✓ | `agents/` does NOT import `bridge/`; agent registration lives in `app.main` |
| Pytest all green | ✓ | 183 passed |
| HTTP e2e | ✓ | `POST /api/runs` + `start` produces `runs/<id>/{idea,experiment,coding,execution,writing}/<artifact>.{v1,approved}.md`, all schema-valid |

## Test counts (cumulative)

```
backend/tests/schema/                                      ≈ 100
backend/tests/unit/                                            41
backend/tests/integration/                                      8
total: 183 passed
```

## How to verify

```
source .venv/bin/activate
mypy --strict backend/app/                # → clean
PYTHONPATH=backend lint-imports           # → 4 kept, 0 broken
PYTHONPATH=backend pytest backend/tests/  # → 183 passed
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8765 &
curl -sX POST http://127.0.0.1:8765/api/runs -H 'Content-Type: application/json' \
  -d '{"task":"e2e","project":"pimc","entrypoint":"pipeline","user_request":"test"}'
# → {"run_id": "...", ...}
curl -sX POST http://127.0.0.1:8765/api/runs/<RID>/start
# wait ~3s
ls runs/<RID>/*/                          # → all 5 agent dirs populated
python scripts/cli_validate.py --check runs/<RID>/*/*.approved.md  # all OK
```

## End-to-end checkpoint (Phase 3)

E2E at this Phase = "API → Bridge → real BaseAgent subclasses → mock_provider →
schema-validated artifact written to runs/<id>/<agent>/<stem>.{v1,approved}.md".

The HTTP smoke run above demonstrates this. **No real LLM API key, no GPU, no
manual fix-ups required** — this is the Dev E2E acceptance lane (ACCEPTANCE §1.1).
Phase 4 wires the front-end HITL (currently the orchestrator auto-approves
`waiting_review` to keep the chain moving).

## Notes / decisions

- **Auto-fallback path**: missing API key → mock_provider with a clear warning. Provider build failures (network, malformed key) also drop to mock with a warning rather than failing the run. This matches the strict V0 rule that Dev E2E must complete with zero external dependencies.
- **Debate mode auto-degrade**: matches DESIGN §16.3 verbatim; covered by `test_debate_runner.py` for all three branches.
- **Agent registration location**: `register_default_agents()` lives in `app.main` (not `agents/base.py`) so the .importlinter contract `agents/ must not import bridge/` stays clean. The orchestrator looks agents up via `bridge/agent_registry`, which Agents themselves never reference.
- **Auto-approve in Phase 3**: orchestrator self-promotes `WAITING_REVIEW → APPROVED → DONE` to let downstream nodes consume the upstream handoff. This gets replaced by a real HITL session in Phase 4 (frontend-driven).
- **Stream signature fix**: `LLMProvider.stream` is declared `def` (not `async def`) returning `AsyncIterator[Delta]` — required for mypy to accept async-generator implementations.
