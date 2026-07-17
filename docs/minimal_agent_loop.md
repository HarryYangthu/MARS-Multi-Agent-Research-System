# MARS 最小 Agent Loop 走读

> 目标:说明当前代码仓里最小可工作的 Agent Loop 是怎么跑起来的,以及哪一段代码是核心。

## 1. 一句话结论

MARS 当前最小 Agent Loop 分两层:

```text
Orchestrator._advance()
  -> bridge.agent_runner.run_agent_node()
    -> agent.build_context()
    -> agent.run_loop()
      -> agent.draft()
      -> validate_output()
      -> repair_after_validation_failure()  # schema fail 时
    -> ArtifactStore.write()
    -> HITL / auto approve
```

其中最核心的一段是 `BaseAgent.run_loop()`。它只做三件事:

1. 让具体 Agent 生成 draft。
2. 用对应 JSON Schema 校验 artifact。
3. 如果失败,最多按配置做 schema-aware repair;仍失败就保留原 artifact 交给 HITL。

这就是 MARS Agent Loop 的最小闭环: **draft -> validate -> repair or handoff**。

## 2. 最核心代码

文件: `backend/app/agents/base.py`

```python
async def run_loop(self, request: RunRequest, context: ContextPack) -> Artifact:
    """Run one Agent loop with schema-aware self-repair."""
    artifact = await self.draft(request, context)
    validation = await self.validate_output(artifact)
    if validation.valid:
        return artifact

    max_repairs = self._loop_policy.max_validation_repairs
    if max_repairs <= 0:
        logger.warning(
            "agent {} output failed schema validation; repair disabled: {}",
            self.name,
            validation.first_error(),
        )
        return artifact

    for attempt in range(1, max_repairs + 1):
        logger.warning(
            "agent {} output failed schema validation; repair attempt {}/{}: {}",
            self.name,
            attempt,
            max_repairs,
            validation.first_error(),
        )
        try:
            artifact = await self.repair_after_validation_failure(
                request=request,
                context=context,
                artifact=artifact,
                validation=validation,
                attempt=attempt,
            )
        except Exception as exc:
            logger.warning(
                "agent {} schema repair failed; preserving artifact for HITL: {}",
                self.name,
                exc,
            )
            return artifact
        validation = await self.validate_output(artifact)
        if validation.valid:
            logger.info(
                "agent {} schema repair succeeded on attempt {}",
                self.name,
                attempt,
            )
            return artifact

    logger.warning(
        "agent {} schema repair exhausted after {} attempt(s); handing to HITL: {}",
        self.name,
        max_repairs,
        validation.first_error(),
    )
    return artifact
```

## 3. 为什么这是最小 Loop

这段代码没有写死 Idea / Experiment / Coding / Execution / Writing 任一 Agent,只依赖三个抽象:

| 抽象 | 作用 |
|---|---|
| `draft(request, context)` | 具体 Agent 负责实现的生成逻辑 |
| `validate_output(artifact)` | 用 `output_schema` 做结构校验 |
| `repair_after_validation_failure(...)` | 校验失败后让模型输出完整修正版 |

所以它是 agent-agnostic 的。任何具体 Agent 只要继承 `BaseAgent` 并实现 `draft()`,就自动拥有这个 loop。

## 4. Bridge 如何调用它

文件: `backend/app/bridge/agent_runner.py`

```python
context = await agent.build_context(request)

run_loop = getattr(agent, "run_loop", None)
if callable(run_loop):
    artifact = await run_loop(request, context)
else:
    artifact = await agent.draft(request, context)

validation = await agent.validate_output(artifact)
art_store = ArtifactStore(run)
ref = art_store.write(text=artifact.text)
```

这里 Bridge 不知道具体 Agent 内部怎么生成,只做编排职责:

- 构造 `RunRequest`
- 调 `agent.build_context()`
- 调 `agent.run_loop()`
- 校验并写入 artifact
- 触发 evaluation event
- 后续交给 HITL 或 auto-approval

## 5. Orchestrator 如何驱动节点

文件: `backend/app/bridge/orchestrator.py`

```python
async def _advance(self, session: RunSession, node_key: str) -> None:
    await self._transition(session, node_key, NodeState.RUNNING)
    if not await self._run_node_runner(session, node_key):
        return

    if session.graph.state(node_key) == NodeState.RUNNING:
        await self._transition(session, node_key, NodeState.WAITING_REVIEW)

    if session.graph.state(node_key) == NodeState.WAITING_REVIEW:
        await self._await_hitl_or_auto(session, node_key)
        await self._complete_approved_node(session, node_key)
```

这层是 RunGraph 状态机:

```text
PENDING -> RUNNING -> WAITING_REVIEW -> APPROVED -> DONE
```

Agent Loop 只负责产出 artifact;是否进入人工审核、是否 auto approve、是否继续下一个节点,都由 Orchestrator 负责。

## 6. 具体 Agent 做什么

以 `IdeaAgent` 为例,文件: `backend/app/agents/idea/agent.py`。

它的 `draft()` 会:

1. 加载 Idea 自身上下文。
2. 收集 research tools / research pack。
3. 把 research pack 注入 context。
4. 如果开启 debate,跑 `run_debate()`。
5. 否则走 `_draft_via_llm()`。
6. 最后 `_finalize_artifact()` 补 metadata、quality warnings、frontmatter。

最小情况下,一个普通 Agent 可以只实现:

```python
async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
    artifact = await self._draft_via_llm(request, context)
    return artifact
```

## 7. LLM 调用在哪里

文件: `backend/app/agents/base.py`

`_draft_via_llm()` 是通用 LLM draft 路径:

```text
_draft_via_llm()
  -> _gather_with_tools()        # 有工具 + 非 mock provider 时
  -> _messages_for_context()     # Context Engine V2 编译 prompt messages
  -> _call_llm()                 # provider.complete()
  -> _artifact_from_completion()
```

`_call_llm()` 负责 provider 选择和 fallback:

- 正常走 `select_provider()`
- 超时受 `mars_llm_timeout_seconds` 控制
- development/mock-allowed 时 provider 失败会 fallback 到 `MockProvider`
- production 或 `MARS_MOCK_MODE=never` 时失败直接抛出

## 8. 当前设计的关键边界

| 层 | 做什么 | 不做什么 |
|---|---|---|
| `BaseAgent.run_loop()` | 单 Agent draft/validate/repair | 不写文件、不做 HITL、不推进图 |
| `agent_runner.run_agent_node()` | 把 Agent 输出落盘并发 evaluation event | 不决定完整 workflow |
| `Orchestrator._advance()` | 节点状态机、HITL/auto approve、继续下游 | 不理解具体 Agent 业务 |
| 具体 Agent | 实现 `draft()` 业务逻辑 | 不控制 run graph |

这个拆分是当前 MARS 最重要的工程骨架: **Agent 只生成,Bridge 只编排,Harness 只提供可信机制,Storage 只落盘。**
