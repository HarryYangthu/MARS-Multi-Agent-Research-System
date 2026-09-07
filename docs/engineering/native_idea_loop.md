# Idea native tool loop — stage 1

This change implements native tool calling for the existing bounded executor. It is not a completed framework migration or a claim of scientific proposal acceptance.

## Current boundary

- Idea uses `protocol: native_tools`; other agents retain explicit `json_actions` compatibility.
- Framework-neutral ToolCall and Message carry assistant calls and matched tool results. Provider SDK objects do not enter the loop state.
- NativeProtocol translates registry names into stable provider-safe aliases and parses arguments strictly. Native final content is Markdown, not a JSON wrapper around Markdown.
- Context packing retains or omits each call/result pair together. Tool schemas count against the input budget; an over-budget pinned context fails explicitly.
- Existing validation, optional Reflection, cumulative budgets, receipt checks and terminal failure states remain enforced. Reflection acceptance is not independent scientific acceptance.
- Native tools currently use explicit non-thinking mode. Providers needing reasoning continuation need a separate adapter; unsupported combinations fail rather than silently dropping required state. Zhipu native streaming is not supported in this stage.
- One native call per turn is supported. Multiple calls are rejected before any execution and repaired within the protocol budget; no silently discarded actions. Batch support remains outstanding.
- Full traces preserve public calls and outputs; metadata traces exclude their content. Provider reasoning content is never logged.

## Verification

Run with Python 3.11 and the ignored `.env.local` credential configuration:

```
PYTHONPATH=backend:posttrain/src:projects/synthetic_regression/src .venv311/bin/python scripts/check_native_loop_live.py
```

The test uses actual DeepSeek requests and reads real synthetic arithmetic files. The host system tool context is confined to the test directory. It verifies native ReAct/Reflection mechanics only, not Idea permissions, research quality, or proposal.v1 compliance. It never replaces a model or service. All failures remain failures.

First test configuration used the Idea context without authorizing its verification-only tool, so both modes exhausted their budgets. After correcting the harness test context, real ReAct passed in 4 model calls / 2 file reads / 1 protocol repair; Reflection passed in 5 model calls / 2 reads / 1 protocol repair / 1 review. Both repairs rejected multiple calls; they did not repair long-document JSON. These exploratory runs preceded the stage commit; the script now records source commit and tracked changes for subsequent runs.

## Migration plan and remaining acceptance

The existing AgentLoopExecutor interface remains usable through BaseAgent injection. Native protocol parsing and message packing are framework-neutral reusable functions. The executor still owns the state machine; further extraction of typed state and independently callable transitions is necessary before a genuine LangGraph adapter. No one-click migration is claimed.

Map state to graph state; model/dispatch/validation/reflection to nodes; context and trace to hooks. LangChain create_agent can reuse policy middleware. Preserve tool result identity, budgets and candidate review digests. Do not import framework classes into Idea business rules. Do not attempt to resume an old JSON/Zhipu checkpoint with native/DeepSeek settings.

Remaining tests: live schema revision and error recovery; compression and interruption/resume; comparable old/new task runs; native Idea proposal evaluation; LangGraph adapter parity. Paper MCP integration is outside this stage.

## Sources inspected

- nanoAgent source tree 7c1dd1dab39d6a4b722e2ab8583341050bf79563: https://github.com/sanbuphy/nanoAgent/tree/7c1dd1dab39d6a4b722e2ab8583341050bf79563
- Codex engineering: https://openai.com/index/unrolling-the-codex-agent-loop/
- Anthropic context engineering: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- LangChain middleware: https://docs.langchain.com/oss/python/langchain/middleware/overview
- LangGraph loop: https://docs.langchain.com/oss/python/langgraph/quickstart
