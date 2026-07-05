# Tool Security Notes

- `ToolRegistry.dispatch()` is the mandatory path for agent tools. It checks
  `configs/tools.yaml`, agent allowlists, Gate hooks, then writes
  `runs/<run_id>/events/tool_events.jsonl` and
  `runs/<run_id>/events/tool_calls.jsonl`.
- Coding writes are never performed during drafting. `code.apply_patch` only
  runs from the approval path and still rechecks Gate 5 inputs.
- `repo_link.yaml` controls linked repo access. Code tools enforce
  `allowed_paths`, `ignore_patterns`, `read_only`, relative paths, and text
  file limits.
- Patch application is two-phase: `git apply --check` must pass before
  `git apply` runs. Failed checks write `patch.<version>.approved.json` with
  `applied: false`.
- Test/lint commands come only from `configs/execution.yaml`; tool args may
  select a configured command but cannot supply arbitrary shell strings.
- Execution runner callbacks are injected by bridge/system code. `harness/`
  does not import `app.execution`, preserving the dependency boundary.
- MCP adapters are optional implementation details behind MARS tool names.
  They must not bypass Gate 5, HITL, schema validation, or run sedimentation.

## Dispatch Order

`ToolRegistry.dispatch()` applies checks in this order:

1. Unknown tool guard.
2. `configs/tools.yaml::enabled` gate.
3. Agent allowlist and `configs/agents.yaml` membership.
4. JSON Schema input validation.
5. Approval/large-refactor preflight for mutating tools.
6. Gate hooks, including Gate 5 baseline compatibility.
7. Timeout-wrapped tool implementation.
8. Audit event, trace span, and invocation record.

This means disabled tools, invalid args, unauthorized agents, large refactors,
and Gate 5 violations are blocked before the implementation function runs.

## Gate 5 And Code Mutation

Gate 5 monitors:

- `code.patch_generator`
- `code.apply_patch`
- `code.write_file`
- `code.delete_file`

It rejects protected paths from `projects/<project>/repo_link.yaml`, including
`baseline/`, `production_interface/`, class-level protected entries such as
`libs/Model.py:Paper_Total_0327`, and frozen `forward(self, x, stream_label, …)`
interfaces. Gate 5 returns `blocked`, not `requires_approval`; humans cannot
approve their way around it.

Large refactors are separate from Gate 5. If a write touches more files than
`configs/gates.yaml::large_refactor.files_changed_threshold`, dispatch returns
`requires_approval` and writes a pending approval record under
`events/tool_approvals/`.

## Rollback

Every mutating code tool saves before-state snapshots with hashes under
`runs/<id>/coding/tool_applications/`. `code.rollback_patch` restores from that
snapshot and emits `tool.rolled_back`. Rollback still resolves the project repo
through `repo_link.yaml` and rejects paths that escape the linked repository.

## Network Tools

Network tools are opt-in at runtime through `MARS_ENABLE_NETWORK_TOOLS=true`.
`search.arxiv_search` is enabled in `configs/tools.yaml` but still refuses to
call the network unless that runtime flag is set. `search.web_search` is
disabled by default and additionally requires:

- `MARS_WEB_SEARCH_PROVIDER=brave|tavily|serper`
- a provider API key
- `MARS_WEB_SEARCH_ALLOWLIST`
- request domains that are a subset of the allowlist

Network failures return structured `ToolResult(ok=False, status="error")` and
must not break the zero-external-dependency demo.

## Redaction

Audit logs redact default sensitive keys:

- `api_key`
- `apikey`
- `authorization`
- `cookie`
- `password`
- `secret`
- `token`

Tools can add more redaction keys through `configs/tools.yaml::redaction` or
`ToolPolicy.redaction`. Redaction applies to `tool_events.jsonl`,
`tool_calls.jsonl`, trace attributes, and the optional LangSmith mirror.

## Static Bypass Guard

`backend/tests/unit/test_tools_hardening.py` contains a static guard that scans
Python sources for direct calls to `apply_patch_tool`, `write_file_tool`,
`delete_file_tool`, and `rollback_patch_tool` outside the registry/code-tool
implementation boundary. New mutating tools should add the same kind of guard.
