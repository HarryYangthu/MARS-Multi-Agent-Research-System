# MARS Tools Catalog

MARS agents call stable MARS tool names through `ToolRegistry.dispatch()`.
Optional MCP backends are adapters behind those names; agents never call MCP
servers directly.

| Tool | Owner | Shared | Function | Implementation |
|---|---|---:|---|---|
| `run.create` | Commander | no | Create a run without starting it | MARS Orchestrator |
| `run.start` | Commander | no | Start a linked or explicit run | MARS Orchestrator |
| `run.status` | Commander | no | Read node states | MARS RunGraph |
| `run.feedback_loop` | Commander | no | Append and start diagnosis-driven repair attempt | MARS Orchestrator |
| `artifact.read` | Commander | no | Read markdown body/frontmatter | MARS ArtifactStore |
| `artifact.review` | Commander | no | Approve/reject/comment pending HITL review | MARS HITL |
| `metrics.evaluate` | Commander | no | Compare `metrics.json` to target metrics | MARS metrics reader |
| `diagnosis.failure_analysis` | Commander | no | Read latest diagnosis artifact | MARS diagnosis artifact |
| `user.approval` | Commander | no | Record approve/reject intent | MARS HITL |
| `search.local_docs` | Idea | no | Query literature KB and uploaded docs | Local KB; optional filesystem adapter |
| `search.arxiv_search` | Idea | no | Query arXiv Atom API | Network-gated; caches to literature KB |
| `search.web_search` | Idea | no | Provider-backed web search with domain allowlist | Network-gated; Brave/Tavily/Serper; disabled in `tools.yaml` by default |
| `knowledge.kb_query` | Idea/Experiment | yes | Query one KB zone | Local JSON KB; optional Chroma adapter |
| `knowledge.baseline_match` | Idea/Experiment | yes | Match plan against run archive fingerprints | MARS baseline matcher |
| `knowledge.experiment_memory` | Experiment | no | Query `run_archive` | Local KB |
| `knowledge.code_assets` | Coding | no | Query code asset memory | Local KB |
| `knowledge.methodology` | Writing | no | Query methodology memory | Local KB |
| `knowledge.run_archive` | Writing | no | Query archived run memory | Local KB |
| `knowledge.ingest_document` | Bridge/System | no | Ingest text into one approved KB zone | Local KB writer; never touches linked repo |
| `code.repo_reader` | Idea/Coding | yes | Read allowed text files from linked project repo | MARS repo_link resolver |
| `code.patch_generator` | Coding | no | Extract/persist unified diff | MARS patch artifact |
| `code.apply_patch` | Coding | no | Apply approved diff after Gate/HITL | `git apply --check` then `git apply` |
| `code.write_file` | Coding/Bridge | yes | Write an allowed project repo text file | `repo_link.yaml` resolver + rollback snapshot |
| `code.delete_file` | Coding/Bridge | yes | Delete an allowed project repo file | HITL-gated + rollback snapshot |
| `code.rollback_patch` | Coding/Bridge | yes | Restore files from a rollback snapshot | MARS rollback snapshot |
| `code.test_runner` | Coding | no | Run configured test commands | `configs/execution.yaml` whitelist |
| `code.lint` | Coding | no | Run configured lint commands | `configs/execution.yaml` whitelist |
| `execution.simulation_runner` | Execution/Bridge | no | Run one simulation via injected callback | Bridge callback |
| `execution.batch_runner` | Execution/Bridge | no | Run approved execution batch via injected callback | Bridge callback |
| `execution.log_streamer` | Execution | no | Read execution log tail | Run artifact reader |
| `execution.metrics_collector` | Execution | no | Read `metrics.json` | Run artifact reader |

## ToolSpec Contract

Every tool visible to an agent or Commander must have a `ToolSpec` generated
from `configs/tools.yaml`. The config file is the single control plane for:

- `enabled`
- `mutation_level`
- `allowed_agents`
- `timeout_seconds`
- `requires_approval`
- `network`
- `command_allowlist`
- `redaction`
- `input_schema`
- `output_schema`

Tool arguments are JSON-Schema validated before the implementation function is
called. A failed schema, disabled tool, missing registration, or agent
permission failure returns a standard `ToolResult` instead of raising into the
agent loop.

The wire result shape is:

```json
{
  "ok": true,
  "status": "success",
  "output": {},
  "error": null,
  "blocked_by_gate": null,
  "requires_approval": false,
  "artifacts": [],
  "events": [],
  "metrics": {},
  "rollback_ref": null,
  "evidence_refs": []
}
```

`status` is one of `success`, `error`, `blocked`, `requires_approval`,
`disabled`, `not_allowed`, or `unknown_tool`.

## Audit And APIs

Every registry-dispatched tool writes:

- `runs/<id>/events/tool_events.jsonl`: lifecycle events such as
  `tool.started`, `tool.completed`, `tool.failed`, `tool.blocked`,
  `tool.requires_approval`, and `tool.rolled_back`.
- `runs/<id>/events/tool_calls.jsonl`: compact invocation audit rows with
  redacted arguments.
- `runs/<id>/coding/tool_applications/*.json`: per-call records for mutating
  code tools and rollback lookups.
- `runs/<id>/context/trace_manifest.v1.json`: a `tool:<name>` span for each
  dispatch when a run directory is available.

Public APIs:

- `GET /api/tools`
- `GET /api/tools/{name}`
- `GET /api/runs/{run_id}/tools?tool=&status=&event=&call_id=&limit=`
- `GET /api/runs/{run_id}/tools/approvals`
- `POST /api/runs/{run_id}/tools/{call_id}/approve`
- `POST /api/runs/{run_id}/tools/{call_id}/reject`
- `POST /api/runs/{run_id}/tools/{call_id}/rollback`

The run detail Commander panel exposes tool/status/event/call-id/limit filters,
pending approvals, rollback buttons, and MCP adapter status.

## MCP Adapter Status

MCP is optional in V1. `/api/tools/adapters` exposes health/configuration for
`chroma`, `filesystem`, `git`, and `github`, `/api/tools/adapters/{kind}/tools`
lists tools through a configured MCP stdio server, and
`/api/tools/adapters/{kind}/call` invokes one MCP tool through the same guarded
adapter boundary. `/api/tools` includes each tool's mapped `mcp_adapter`.
Agents still call MARS tool names, not MCP server tools directly. If an adapter
is unavailable, the local MARS implementation remains the fallback.

The run detail Commander panel shows recent `tool_calls.jsonl` entries,
rollback-capable calls, and current MCP adapter status for operator audit.

## Production Smoke

Offline acceptance is the default:

```bash
bash scripts/acceptance.sh
```

External web search is opt-in and skipped unless explicitly enabled:

```bash
MARS_RUN_EXTERNAL_TOOL_SMOKE=true \
MARS_ENABLE_NETWORK_TOOLS=true \
MARS_WEB_SEARCH_PROVIDER=tavily \
MARS_WEB_SEARCH_ALLOWLIST=arxiv.org \
TAVILY_API_KEY=... \
PYTHONPATH=backend pytest backend/tests/unit/test_search_tools_v1.py::test_web_search_provider_external_smoke_when_configured -q
```

LangSmith is also optional. File-backed traces remain mandatory even when the
external mirror is disabled. Enable the mirror with:

```bash
MARS_LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=mars-dev
```
