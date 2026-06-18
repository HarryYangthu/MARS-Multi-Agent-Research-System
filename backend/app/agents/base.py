"""BaseAgent abstract class.

Per DESIGN §5.1, every Agent exposes ``build_context`` / ``draft`` /
``revise`` / ``validate_output`` / ``submit_for_review``. V0's BaseAgent
provides default implementations for the latter three; subclasses only have
to implement ``draft`` (and optionally override ``build_context``).

The Agent does NOT depend on bridge/ or api/ — by .importlinter contract.
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from loguru import logger

from app.harness.llm.model_registry import AgentConfig, get_agent_config, select_provider
from app.harness.llm.provider_base import Completion, LLMConfig, LLMProvider, Message
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.frontmatter_parser import close_unclosed_frontmatter
from app.harness.schema.validator import (
    ValidationResult,
    validate_document,
)
from app.settings import get_settings
from app.storage.agent_context_store import (
    SUPPORTED_AGENTS,
    list_agent_context_files,
    load_agent_memory_items,
    load_agent_research_sites,
)


@dataclass
class RunRequest:
    project: str
    user_request: str
    upstream_artifacts: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextPack:
    system: str
    project: str
    task: str
    upstream: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_messages(self, *, agent_name: str, output_schema: str) -> list[Message]:
        from app.harness.context.compiler import (
            compile_agent_context,
            load_schema_template,
        )

        compiled = compile_agent_context(
            system=self.system,
            project=self.project,
            task=self.task,
            upstream=self.upstream,
            agent_name=agent_name,
            output_schema=output_schema,
            schema_template=load_schema_template(output_schema),
        )
        self.metadata["last_compiled_manifest"] = compiled.manifest
        return compiled.messages


@dataclass
class Artifact:
    text: str
    schema_id: str
    metadata: dict[str, Any]
    body: str
    debate_role: str | None = None


@dataclass
class HumanFeedback:
    comment: str = ""
    edits: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentLoopPolicy:
    max_validation_repairs: int = 1
    max_tool_steps: int = 3


class BaseAgent(ABC):
    """Common Agent skeleton.

    Subclasses set ``name`` and ``output_schema`` (string) and override
    ``draft`` to actually call the LLM.
    """

    name: str = "base"
    output_schema: str = ""
    # Role-specific system guidance folded into the context by ``build_context``.
    # Each concrete Agent sets this so its prompt/context differs from the others
    # without every subclass having to override ``build_context``.
    agent_brief: str = ""
    # Max ReAct iterations the agent may spend calling tools before it must
    # produce its final artifact. Only used when the agent has tools configured
    # AND a real (non-mock) provider is active.
    max_tool_steps: int = 3

    def __init__(self, *, agent_config: AgentConfig | None = None) -> None:
        self._config = agent_config or get_agent_config(self.name)
        if not self.output_schema:
            self.output_schema = self._config.output_schema
        self._loop_policy = self._load_loop_policy(self._config.raw.get("loop", {}))
        self.max_tool_steps = self._loop_policy.max_tool_steps

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def loop_policy(self) -> AgentLoopPolicy:
        return self._loop_policy

    # ------------------------------------------------------------- defaults

    async def build_context(self, request: RunRequest) -> ContextPack:
        system = f"MARS {self.name} agent. Output schema: {self.output_schema}."
        if self.agent_brief:
            system += "\n\n" + self.agent_brief.strip()
        if self._config.tools:
            system += "\n\n可用工具: " + ", ".join(self._config.tools)
        upstream = dict(request.upstream_artifacts)
        metadata: dict[str, Any] = {}
        if self.name in SUPPORTED_AGENTS and self.name != "idea":
            context_files = list_agent_context_files(self.name)
            if context_files:
                upstream[f"{self.name}_self_context"] = "\n\n".join(
                    f"## {item.path}\n\n{item.content}" for item in context_files
                )
                metadata[f"{self.name}_self_context_files"] = [
                    item.path for item in context_files
                ]
            research_sites = load_agent_research_sites(self.name)
            if research_sites:
                upstream[f"{self.name}_research_sites"] = "\n".join(
                    "- [{status}] {label}: {url}".format(
                        status="enabled" if site.enabled else "disabled",
                        label=site.label,
                        url=site.url,
                    )
                    for site in research_sites
                )
                metadata[f"{self.name}_research_site_count"] = len(
                    [site for site in research_sites if site.enabled]
                )
            memory_items = load_agent_memory_items(self.name)
            if memory_items:
                selected_memory = memory_items[:5]
                upstream[f"{self.name}_approved_memory"] = "\n\n".join(
                    "## {label}\n{text}\nEvidence: {evidence}".format(
                        label=item.label,
                        text=item.text,
                        evidence=", ".join(item.evidence_refs) or "(none)",
                    )
                    for item in selected_memory
                )
                metadata[f"{self.name}_approved_memory_count"] = len(selected_memory)
                metadata[f"{self.name}_approved_memory_ids"] = [
                    item.id for item in selected_memory
                ]
        return ContextPack(
            system=system,
            project=f"Project: {request.project}.",
            task=request.user_request,
            upstream=upstream,
            metadata=metadata,
        )

    async def validate_output(self, artifact: Artifact) -> ValidationResult:
        return validate_document(artifact.text, expected_schema=self.output_schema)

    async def submit_for_review(self, artifact: Artifact) -> Artifact:
        # In V0 phases 3 & 4 a real review session lives in app.hitl;
        # the base class just returns the artifact unchanged so phase 3 tests
        # can verify the draft → validate path without HITL plumbing.
        return artifact

    # ------------------------------------------------------------- override

    @abstractmethod
    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact: ...

    async def revise(
        self, artifact: Artifact, feedback: HumanFeedback
    ) -> Artifact:
        # Default revise: re-run draft with feedback added to the user prompt.
        request = RunRequest(
            project=artifact.metadata.get("project", ""),
            user_request=feedback.comment or "请合并人工编辑意见，并保持中文输出。",
            upstream_artifacts={"previous_version": artifact.text},
        )
        ctx = await self.build_context(request)
        return await self.draft(request, ctx)

    async def run_loop(self, request: RunRequest, context: ContextPack) -> Artifact:
        """Run one Agent loop with schema-aware self-repair.

        The bridge still owns HITL and persistence. This method only improves
        the Agent-local draft path: if the first artifact fails JSON Schema
        validation, ask the model for a full corrected document before handing
        the result back to the bridge. If repair still fails, the invalid
        artifact is preserved for HITL, matching the existing V0 safety path.
        """
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

    async def repair_after_validation_failure(
        self,
        *,
        request: RunRequest,
        context: ContextPack,
        artifact: Artifact,
        validation: ValidationResult,
        attempt: int,
    ) -> Artifact:
        repair_context = self._validation_repair_context(
            context=context,
            artifact=artifact,
            validation=validation,
            attempt=attempt,
        )
        messages = self._messages_for_context(
            request,
            repair_context,
            purpose=f"schema_repair_{attempt}",
        )
        completion = await self._call_llm(messages)
        return self._artifact_from_completion(completion)

    # ------------------------------------------------------------- helpers

    @classmethod
    def _load_loop_policy(cls, raw: object) -> AgentLoopPolicy:
        data: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
        return AgentLoopPolicy(
            max_validation_repairs=_bounded_int(
                data.get("max_validation_repairs"),
                default=1,
                minimum=0,
                maximum=3,
            ),
            max_tool_steps=_bounded_int(
                data.get("max_tool_steps"),
                default=cls.max_tool_steps,
                minimum=0,
                maximum=8,
            ),
        )

    def _select_provider(self) -> tuple[LLMProvider, LLMConfig]:
        return select_provider(self._config)

    async def _call_llm(
        self,
        messages: Sequence[Message],
        *,
        debate_role: str | None = None,
    ) -> Completion:
        provider, cfg = self._select_provider()
        cfg.extra = dict(cfg.extra or {})
        if debate_role is not None:
            cfg.extra["debate_role"] = debate_role
        try:
            completion = await asyncio.wait_for(
                provider.complete(list(messages), cfg),
                timeout=get_settings().mars_llm_timeout_seconds,
            )
            return completion
        except Exception as exc:
            settings = get_settings()
            if settings.is_production or settings.mars_mock_mode == "never":
                raise
            logger.warning(
                "agent {} provider {} failed ({}); falling back to mock",
                self.name,
                provider.name,
                exc,
            )
            from app.harness.llm.mock_provider import MockProvider

            mock = MockProvider(default_schema=self.output_schema)
            cfg.extra["debate_role"] = debate_role
            return await mock.complete(list(messages), cfg)

    async def _draft_via_llm(
        self,
        request: RunRequest,
        context: ContextPack,
        *,
        debate_role: str | None = None,
    ) -> Artifact:
        # Optional ReAct-style tool gathering before the final draft. When the
        # agent has tools configured and a real provider is active, it may call
        # tools to enrich context; findings are folded back in as an upstream
        # block. No-op under mock / no-tools, so the zero-dependency demo and
        # existing single-call tests are unaffected.
        observations = await self._gather_with_tools(
            request, context, debate_role=debate_role
        )
        if observations:
            context = self._augment_context(context, observations)
        purpose = "draft" if debate_role is None else f"draft_{debate_role}"
        messages = self._messages_for_context(
            request,
            context,
            purpose=purpose,
        )
        completion = await self._call_llm(messages, debate_role=debate_role)
        return self._artifact_from_completion(completion)

    def _messages_for_context(
        self,
        request: RunRequest,
        context: ContextPack,
        *,
        purpose: str,
    ) -> list[Message]:
        try:
            from app.harness.context.engine import (
                CompileContextInput,
                compile_context,
            )

            result = compile_context(
                CompileContextInput(
                    agent=self.name,
                    node_key=str(request.extra.get("node_key", self.name)),
                    project=request.project,
                    output_schema=self.output_schema,
                    system=context.system,
                    project_context=context.project,
                    task=context.task,
                    upstream=context.upstream,
                    metadata=context.metadata,
                    run_id=str(request.extra.get("run_id", "")),
                    run_root=_run_root_from_request(request),
                    purpose=purpose,
                    tool_names=self._config.tools,
                )
            )
            return result.messages
        except Exception as exc:
            logger.warning(
                "agent {} context v2 compile failed; falling back to legacy messages: {}",
                self.name,
                exc,
            )
            return context.to_messages(
                agent_name=self.name,
                output_schema=self.output_schema,
            )

    # --------------------------------------------------------- tool gathering

    def _tools_enabled(self) -> bool:
        """True only when this agent has tools AND a real provider is active.

        Under mock the gather loop is skipped entirely: the MockProvider returns
        a finished artifact rather than a tool-call decision, so looping would
        waste a call and never yield observations.
        """
        if not self._config.tools:
            return False
        provider, _ = self._select_provider()
        return provider.name != "mock"

    async def _gather_with_tools(
        self,
        request: RunRequest,
        context: ContextPack,
        *,
        debate_role: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self._tools_enabled():
            return []
        from app.harness.tools.registry import ToolContext, get_registry

        registry = get_registry()
        tool_ctx = ToolContext(
            run_id=str(request.extra.get("run_id", "")),
            project=request.project,
            agent=self.name,
            extra={"run_root": str(request.extra.get("run_root", ""))},
        )
        convo: list[Message] = self._tool_gather_messages(context)
        observations: list[dict[str, Any]] = []
        for _step in range(self.max_tool_steps):
            self._write_messages_manifest(
                request,
                convo,
                purpose=f"tool_gather_{_step + 1}",
            )
            completion = await self._call_llm(convo, debate_role=debate_role)
            calls = _parse_tool_calls(completion.text)
            if not calls:
                break
            for call in calls:
                tool_name = call["tool"]
                args = call.get("args", {})
                result = await registry.dispatch(tool_name, args, tool_ctx)
                raw_ref = _write_tool_raw_result(
                    request=request,
                    agent=self.name,
                    tool_name=tool_name,
                    step=_step + 1,
                    args=args,
                    result=result,
                )
                obs = {
                    "tool": tool_name,
                    "args": args,
                    "ok": result.ok,
                    "output": _compact_tool_output(result.output),
                    "error": result.error,
                    "blocked_by_gate": result.blocked_by_gate,
                    "raw_ref": raw_ref,
                }
                observations.append(obs)
                convo.append(
                    Message(
                        role="user",
                        content="[observation] "
                        + json.dumps(obs, ensure_ascii=False, default=str)[:1200],
                    )
                )
        if observations:
            logger.info(
                "agent {} gathered {} tool observation(s)",
                self.name,
                len(observations),
            )
        return observations

    def _tool_gather_messages(self, context: ContextPack) -> list[Message]:
        tool_lines = "\n".join(f"- {t}" for t in self._config.tools)
        sys = (
            f"你是 MARS 的 **{self.name}** Agent，正在为最终产物收集信息。\n"
            "你可以先调用工具检索资料，再撰写产物。本阶段**只决定是否调用工具**，"
            "不要写最终产物。\n\n"
            f"可用工具:\n{tool_lines}\n\n"
            "输出协议(严格):只输出一个 JSON 对象，不要任何额外文字或代码围栏。\n"
            '需要调用工具时:{"tool_calls": [{"tool": "工具名", "args": {...}}]}\n'
            '已无需更多信息时:{"done": true}\n'
            "每个工具的 args 用最相关的查询词。最多调用几轮后必须收尾。"
        )
        msgs = [Message(role="system", content=sys)]
        if context.project:
            msgs.append(Message(role="system", content=context.project))
        for label, content in context.upstream.items():
            msgs.append(Message(role="user", content=f"[upstream:{label}]\n{content}"))
        if context.task:
            msgs.append(Message(role="user", content=context.task))
        return msgs

    def _write_messages_manifest(
        self,
        request: RunRequest,
        messages: list[Message],
        *,
        purpose: str,
        diagnostics_extra: Mapping[str, Any] | None = None,
    ) -> None:
        run_root = _run_root_from_request(request)
        if run_root is None:
            return
        try:
            from app.harness.context.engine import write_messages_manifest

            write_messages_manifest(
                run_root=run_root,
                run_id=str(request.extra.get("run_id", "")),
                agent=self.name,
                node_key=str(request.extra.get("node_key", self.name)),
                project=request.project,
                output_schema=self.output_schema,
                purpose=purpose,
                messages=messages,
                diagnostics_extra=diagnostics_extra,
            )
        except Exception as exc:
            logger.warning("agent {} message manifest write failed: {}", self.name, exc)

    def _augment_context(
        self, context: ContextPack, observations: list[dict[str, Any]]
    ) -> ContextPack:
        """Fold tool observations into a new ContextPack as an upstream block."""
        useful = [o for o in observations if o.get("ok")]
        rendered = json.dumps(
            useful or observations, ensure_ascii=False, indent=2, default=str
        )
        upstream = dict(context.upstream)
        upstream["tool_findings"] = (
            "以下是本 Agent 调用工具检索到的资料，请在撰写产物时充分利用，"
            "并保持中文输出:\n" + rendered
        )
        return replace(context, upstream=upstream)

    def _validation_repair_context(
        self,
        *,
        context: ContextPack,
        artifact: Artifact,
        validation: ValidationResult,
        attempt: int,
    ) -> ContextPack:
        upstream = dict(context.upstream)
        upstream[f"{self.name}_schema_invalid_attempt_{attempt}"] = _truncate_text(
            artifact.text,
            limit=12000,
        )
        upstream[f"{self.name}_schema_errors_attempt_{attempt}"] = (
            _render_validation_errors(validation)
        )
        task = (
            context.task
            + "\n\n[Schema repair]\n"
            + "上一次输出没有通过 JSON Schema 校验。请只返回一个完整的 markdown "
            + f"文档，schema 必须是 `{self.output_schema}`，第一行必须是 `---`，"
            + "不得解释、不得使用代码围栏，并保留原任务的研究意图。\n"
            + "需要修复的校验错误:\n"
            + _render_validation_errors(validation)
        )
        metadata = dict(context.metadata)
        metadata["schema_repair_attempt"] = attempt
        return replace(context, task=task, upstream=upstream, metadata=metadata)

    def _artifact_from_completion(self, completion: Completion) -> Artifact:
        # Real LLMs sometimes wrap the document in a ```markdown ... ``` fence,
        # or emit a sentence of preamble before the YAML frontmatter starts.
        # Strip both before handing to the parser.
        cleaned = self._unwrap_llm_text(completion.text)
        try:
            parsed = parse_frontmatter(cleaned)
        except Exception:
            parsed = None
        metadata: dict[str, Any] = parsed.metadata if parsed else {}
        body: str = parsed.body if parsed else cleaned
        return Artifact(
            text=cleaned,
            schema_id=str(metadata.get("schema", self.output_schema)),
            metadata=metadata,
            body=body,
            debate_role=completion.debate_role,
        )

    @staticmethod
    def _unwrap_llm_text(text: str) -> str:
        """Strip code fences and pre-frontmatter preamble from an LLM reply."""
        s = text.strip()
        # ```markdown\n...\n``` or ```yaml ... ``` wrappers
        if s.startswith("```"):
            # find first newline after the opening fence info string
            nl = s.find("\n")
            if nl >= 0:
                s = s[nl + 1 :]
            if s.endswith("```"):
                s = s[:-3].rstrip()
        # If the doc has frontmatter but with prose before it, jump to the first `---`
        # (only if the very first line isn't already `---`).
        first = s.split("\n", 1)[0].strip()
        if first != "---":
            idx = s.find("\n---\n")
            if idx == -1:
                idx = s.find("\n---")
            if idx > 0:
                s = s[idx + 1 :]
        return close_unclosed_frontmatter(s)


def _parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract a tool-call decision from an LLM reply.

    Accepts ``{"tool_calls": [{"tool", "args"}]}`` and returns the normalized
    call list. Returns ``[]`` for ``{"done": true}``, unparseable text, or a
    reply that already looks like a finished artifact (e.g. mock output) — the
    caller treats an empty list as "stop gathering".
    """
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl >= 0:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    raw = obj.get("tool_calls")
    if not isinstance(raw, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("tool"):
            args = item.get("args", {})
            calls.append(
                {"tool": str(item["tool"]), "args": args if isinstance(args, dict) else {}}
            )
    return calls


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        parsed = default
    elif isinstance(value, (int, float, str)):
        try:
            parsed = int(value)
        except ValueError:
            parsed = default
    elif value is None:
        parsed = default
    else:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _render_validation_errors(result: ValidationResult, *, limit: int = 12) -> str:
    if not result.errors:
        return "- /: unknown validation error"
    lines = [
        f"- {_specific_error_path(err.path, err.message)}: {err.message}"
        for err in result.errors[:limit]
    ]
    remaining = len(result.errors) - limit
    if remaining > 0:
        lines.append(f"- ... {remaining} more validation error(s)")
    return "\n".join(lines)


def _specific_error_path(path: str, message: str) -> str:
    marker = "' is a required property"
    if path == "/" and message.startswith("'") and marker in message:
        missing = message.split("'", 2)[1]
        return f"/{missing}"
    return path


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[truncated]"


def _run_root_from_request(request: RunRequest) -> Path | None:
    raw = request.extra.get("run_root")
    if not raw:
        return None
    return Path(str(raw))


def _write_tool_raw_result(
    *,
    request: RunRequest,
    agent: str,
    tool_name: str,
    step: int,
    args: dict[str, Any],
    result: Any,
) -> str | None:
    run_root = _run_root_from_request(request)
    if run_root is None or not get_settings().mars_context_tool_raw_externalize:
        return None
    try:
        from app.harness.context.raw_store import write_raw_context

        return write_raw_context(
            run_root=run_root,
            agent=agent,
            label=f"{tool_name}_{step}",
            payload={
                "tool": tool_name,
                "step": step,
                "args": args,
                "ok": getattr(result, "ok", False),
                "status": getattr(result, "status", None),
                "output": getattr(result, "output", None),
                "error": getattr(result, "error", None),
                "blocked_by_gate": getattr(result, "blocked_by_gate", None),
                "metadata": getattr(result, "metadata", {}),
                "artifacts": getattr(result, "artifacts", []),
                "events": getattr(result, "events", []),
                "metrics": getattr(result, "metrics", {}),
            },
        )
    except Exception as exc:
        logger.warning("tool raw context write failed for {}: {}", tool_name, exc)
        return None


def _compact_tool_output(output: Any) -> Any:
    try:
        from app.harness.context.raw_store import compact_tool_output

        return compact_tool_output(output)
    except Exception:
        text = json.dumps(output, ensure_ascii=False, default=str)
        return text[:900]


__all__ = [
    "AgentLoopPolicy",
    "Artifact",
    "BaseAgent",
    "ContextPack",
    "HumanFeedback",
    "RunRequest",
]


# Run loop helper (pure asyncio; not used by orchestrator yet)
async def _ensure_loop() -> None:
    await asyncio.sleep(0)
