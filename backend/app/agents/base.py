"""Shared real-agent interface; execution is delegated to a replaceable loop."""
from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from app.harness.agent_loop import AgentLoopExecutor, AgentLoopPolicy, LoopInput, NativeAgentLoop
from app.harness.agent_loop.executor import ProgressSink
from app.harness.agent_loop.stop import StopCondition
from app.harness.llm.model_registry import AgentConfig, get_agent_config, select_provider
from app.harness.llm.provider_base import Completion, LLMConfig, LLMProvider, Message, llm_call_deadline_seconds
from app.harness.llm.accounting import guarded_complete, run_resource_scope
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import ValidationResult, validate_document
from app.settings import repo_root


if TYPE_CHECKING:
    from app.harness.tools.registry import ToolRegistry


@dataclass
class RunRequest:
    project: str
    user_request: str
    upstream_artifacts: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    progress_sink: ProgressSink | None = field(default=None, repr=False, compare=False)
    runtime: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


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
            preserve_upstream=True,
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


class BaseAgent(ABC):
    name = "base"
    output_schema = ""
    agent_brief = ""
    max_tool_steps = 18
    native_structured_delivery = False
    project_knowledge_enabled = False

    def __init__(self, *, agent_config: AgentConfig | None = None,
                 loop_executor: AgentLoopExecutor | None = None) -> None:
        self._config = agent_config or get_agent_config(self.name)
        self.output_schema = self.output_schema or self._config.output_schema
        self._loop_policy = AgentLoopPolicy.from_mapping(self._config.raw.get("loop", {}))
        self.max_tool_steps = self._loop_policy.max_tool_steps
        self._executor = loop_executor or NativeAgentLoop()

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def loop_policy(self) -> AgentLoopPolicy:
        return self._loop_policy

    @classmethod
    def _load_loop_policy(cls, raw: object) -> AgentLoopPolicy:
        return AgentLoopPolicy.from_mapping(raw)

    async def build_context(self, request: RunRequest) -> ContextPack:
        from app.storage.agent_context_store import load_agent_code_repositories
        sources = {"project_rules": True, "code_repositories": True}
        configured = request.extra.get("context_sources", {})
        if (not isinstance(configured, dict) or set(configured) - (set(sources) | {"agent_resources", "memory", "project_references"})
                or any(not isinstance(value, bool) for value in configured.values())):
            raise ValueError("context_sources accepts only project_rules/code_repositories/agent_resources/memory/project_references booleans")
        sources.update(configured)
        required = request.extra.get("required_upstream_refs", [])
        if not isinstance(required, list) or any(x not in request.upstream_artifacts for x in required):
            raise ValueError("required_upstream_refs must name supplied upstream artifacts")
        from app.harness.project_workspace import folder_project, project_root
        from app.harness.context.folder_context import load_folder_context, render_folder_context
        project_path = project_root(request.project)
        folder_context = (load_folder_context(
            request.project, Path(str(request.extra["run_root"])) if request.extra.get("run_root") else None)
            if sources.get("project_references", True) else None)
        folder = folder_project(request.project) if sources["project_rules"] else None
        rules_root = folder.root if folder is not None else project_path
        rules_path = rules_root / "AGENTS.md"
        if sources["project_rules"] and not rules_path.resolve().is_relative_to(rules_root.resolve()):
            raise ValueError("project rules must remain inside the project folder")
        rules = (rules_path.read_text() if sources["project_rules"] and rules_path.is_file()
                 else "No project-specific rules supplied in this context.")
        repositories = (load_agent_code_repositories(self.name, project=request.project)
                        if sources["code_repositories"] else ())
        upstream = dict(request.upstream_artifacts)
        metadata: dict[str, Any] = {"required_upstream_refs": required, "context_sources": sources}
        if folder_context is not None:
            rules = render_folder_context(folder_context, include_instructions=sources["project_rules"])
            metadata["folder_context"] = {k: v for k, v in folder_context.items() if k != "files"}
            metadata["folder_context"]["files"] = [{k: v for k, v in f.items() if k != "content"} for f in folder_context["files"]]
        from app.harness.context.project_knowledge import load_project_knowledge
        knowledge, knowledge_record = (load_project_knowledge(
            project_path, Path(str(request.extra["run_root"])) if request.extra.get("run_root") else None)
            if self.project_knowledge_enabled and sources.get("project_references", True)
            and folder_context is None else ("", {}))
        if knowledge:
            rules_path_label = knowledge_record["source"]
            rules += f"\n\nProject knowledge ({rules_path_label}; reference material):\n" + knowledge
            metadata["project_knowledge"] = {key: value for key, value in knowledge_record.items() if key != "content"}
        if repositories:
            upstream[f"{self.name}_code_repositories"] = json.dumps(
                [asdict(repository) for repository in repositories], ensure_ascii=False)
            metadata[f"{self.name}_code_repository_count"] = len(repositories)
        self._prepare_runtime_context(request, upstream, metadata)
        return ContextPack(
            system=f"MARS {self.name} agent. {self.agent_brief}",
            project=f"Project: {request.project}.\nProject constraints:\n{rules}",
            task=request.user_request, upstream=upstream, metadata=metadata,
        )

    def _prepare_runtime_context(self, request: RunRequest, upstream: dict[str, str],
                                 metadata: dict[str, Any]) -> None:
        from app.storage.agent_context_store import SUPPORTED_AGENTS, load_agent_runtime_resources
        from app.harness.skills import load_selected_skills
        from app.harness.context.injection_runtime import prepare_memory_context
        from app.harness.agent_loop.trace import atomic_json, digest
        from app.harness.persistence import path_lock
        from app.harness.tools.config import tool_config

        root = Path(str(request.extra.get("run_root") or request.runtime.get("run_root") or
                        repo_root() / "runs" / ("agent_" + uuid.uuid4().hex))).resolve()
        request.runtime["run_root"] = str(root)
        invocation = str(request.extra.get("resume_invocation") or request.extra.get("invocation_id")
                         or request.runtime.get("invocation_id") or digest({"root": str(root), "agent": self.name,
                             "task": request.user_request, "upstream": request.upstream_artifacts})[:32])
        if not invocation.replace("-", "").isalnum():
            raise ValueError("invalid invocation ID")
        request.runtime["invocation_id"] = invocation
        metadata["run_root"] = str(root)
        tools = tuple(name for name in self.config.tools if tool_config(name).enabled)
        selected = request.extra.get("skills", [])
        if not isinstance(selected, list) or any(not isinstance(value, str) for value in selected):
            raise ValueError("skills must be a list of registered skill IDs")
        selection = load_selected_skills(selected, granted_tools=tools, project=request.project)
        resources = (load_agent_runtime_resources(self.name,
                     overrides=request.runtime.get("agent_resource_overrides"))
                     if self.name in SUPPORTED_AGENTS and metadata["context_sources"].get("agent_resources", True) else None)
        frozen = {"schema": "agent.context_resources.v1", "skills": selection.manifest,
                  "resources": resources.manifest if resources else None}
        path = root / "context" / "resources" / (digest({"agent": self.name, "invocation": invocation}) + ".json")
        with path_lock(path.with_suffix(".lock")):
            if path.exists():
                if json.loads(path.read_text()) != frozen:
                    raise ValueError("agent resources or skill version changed; start a new invocation")
            else:
                atomic_json(path, frozen)
        if selection.context:
            upstream["selected_skills"] = selection.context
        if resources and resources.context:
            upstream["agent_resources"] = resources.context
        metadata["skills"] = selection.manifest
        metadata["agent_resources"] = resources.manifest if resources else None
        metadata["resource_snapshot"] = str(path)
        request.runtime["skill_selection"] = selection
        memory = prepare_memory_context(run_root=root, agent=self.name, node_key=invocation,
                                        project=request.project, task=request.user_request,
                                        max_tokens=None if metadata["context_sources"].get("memory", True) else 0)
        if memory.text:
            upstream["approved_memory"] = memory.text
        metadata["memory"] = {"digest": memory.digest, "manifest_path": str(memory.manifest_path),
                              "record_ids": [item["record_id"] for item in memory.manifest["records"]]}
        request.runtime["memory_snapshot"] = memory

    async def validate_output(self, artifact: Artifact) -> ValidationResult:
        return validate_document(artifact.text, expected_schema=self.output_schema)

    async def submit_for_review(self, artifact: Artifact) -> Artifact:
        # Bridge owns actual human approval; this method does not approve anything.
        return artifact

    @abstractmethod
    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact: ...

    async def revise(self, artifact: Artifact, feedback: HumanFeedback) -> Artifact:
        request = RunRequest(
            project=str(artifact.metadata.get("project", "")),
            user_request=feedback.comment or "Apply the supplied edits and return a complete document.",
            upstream_artifacts={"previous_version": artifact.text, "human_edits": json.dumps(feedback.edits)},
            extra={"required_upstream_refs": ["previous_version", "human_edits"]},
        )
        return await self.run_loop(request, await self.build_context(request))

    async def run_loop(self, request: RunRequest, context: ContextPack) -> Artifact:
        from app.harness.context.injection_runtime import complete_memory_usage
        outcome = "failed"
        try:
            root = Path(str(request.extra.get("run_root") or request.runtime.get("run_root") or
                            repo_root() / "runs" / ("agent_" + uuid.uuid4().hex)))
            request.extra["run_root"] = str(root)
            with run_resource_scope(root):
                artifact = await self.draft(request, context)
            result = await self.validate_output(artifact)
            if not result.valid:
                raise RuntimeError("Agent output is not schema-valid: " + str(result.first_error()))
            selection = request.runtime.get("skill_selection")
            if selection is not None:
                from app.harness.skills import skill_acceptance_errors
                errors = skill_acceptance_errors(selection, output_schema=self.output_schema,
                    observations=request.runtime.get("observations", []))
                if errors:
                    raise RuntimeError("Agent skill acceptance failed: " + "; ".join(errors))
            outcome = "completed"
            return artifact
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            snapshot = request.runtime.get("memory_snapshot")
            if snapshot is not None:
                try:
                    complete_memory_usage(snapshot=snapshot, outcome=outcome)
                except Exception as exc:
                    # Usage feedback is ancillary. Preserve the original
                    # execution exception/cancellation and expose the write
                    # failure rather than turning a completed artifact into
                    # an unexplained task failure.
                    from loguru import logger
                    request.runtime["memory_usage_error"] = str(exc)
                    context.metadata["memory_usage_error"] = str(exc)
                    logger.error("memory usage persistence failed: {}", exc)

    def _select_provider(self) -> tuple[LLMProvider, LLMConfig]:
        return select_provider(self._config)

    def _select_review_provider(self) -> tuple[LLMProvider, LLMConfig] | None:
        return None

    async def _call_llm(self, messages: Sequence[Message], *,
                        debate_role: str | None = None) -> Completion:
        provider, config = self._select_provider()
        if debate_role:
            config.extra["debate_role"] = debate_role
        try:
            return await asyncio.wait_for(guarded_complete(provider, list(messages), config),
                                          timeout=llm_call_deadline_seconds(config))
        finally:
            await provider.close()

    def _messages_for_context(self, request: RunRequest, context: ContextPack, *,
                              purpose: str) -> list[Message]:
        schema_path = repo_root() / "backend/app/harness/schema/schemas" / (self.output_schema + ".json")
        schema = json.loads(schema_path.read_text())
        output_instruction = (
            "Return the complete Markdown document directly, beginning with YAML frontmatter matching this schema. "
            "Do not wrap the document in JSON or a code fence. JSON Schema:\n"
            if self.loop_policy.protocol == "native_tools" else
            "Return final.metadata as a native JSON object matching this schema, "
            "and final.body as Markdown. The host serializes the artifact's YAML frontmatter. JSON Schema:\n"
        )
        schema_instruction = output_instruction + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        if self.loop_policy.protocol == "native_tools" and self.native_structured_delivery:
            schema_instruction = (
                "Submit the complete candidate with mars_submit_document. Its metadata argument must match "
                + self.output_schema + ", whose JSON Schema is supplied in that function's parameters. "
                "Pass native JSON metadata and a Markdown body; never write YAML in arguments. "
                "Do not return a proposal in assistant text. Submission is followed by host validation and, "
                "when configured, review; it does not claim approval or experimental success."
            )
        elif self.native_structured_delivery:
            schema_instruction = (
                "Return final.metadata as a complete native JSON object and final.body as Markdown. "
                "The loop supplies the complete final.metadata JSON Schema, including this request's requirements. "
                "The host serializes the artifact's YAML frontmatter and validates it before review."
            )
        messages = [Message(role="system", content=context.system),
                    Message(role="system", content=context.project),
                    Message(role="system", content=schema_instruction),
                    Message(role="user", content=context.task)]
        for label, content in context.upstream.items():
            # Complete upstream artifacts stay pinned. Fail on budget overflow rather than
            # quietly dropping task-critical evidence; label does not elevate data trust.
            messages.append(Message(role="user", content=f"[untrusted upstream:{label}]\n{content}"))
        return messages

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        if self.loop_policy.protocol == "native_tools" and not text.startswith("---\n"):
            return ["/format: the first four characters must be --- followed by a newline. "
                    "Remove ALL leading explanations, JSON wrappers, and Markdown code fences. "
                    "A schema field inside a fenced block is not frontmatter. Return only the complete raw "
                    "Markdown document, beginning with its YAML frontmatter; preserve the existing content."]
        validation = validate_document(text, expected_schema=self.output_schema)
        errors = [f"{error.path}: {error.message}" for error in validation.errors]
        if validation.valid:
            metadata = parse_frontmatter(text).metadata
            if metadata.get("project") != request.project:
                errors.append("/project: must equal the requested project")
            submission_schema = self.submission_schema(request)
            if submission_schema is not None:
                errors.extend("/" + "/".join(str(p) for p in error.absolute_path) + ": " + error.message
                              for error in Draft202012Validator(submission_schema).iter_errors(validation.metadata))
        selection = request.runtime.get("skill_selection")
        if selection is not None:
            from app.harness.skills import skill_acceptance_errors
            errors.extend(skill_acceptance_errors(selection, output_schema=self.output_schema,
                                                   observations=observations))
        return errors

    def reflection_rubric(self) -> str:
        return "Verify evidence, definitions, every numerical claim, falsifiability, and downstream implementation completeness."

    def loop_progress_sink(self, request: RunRequest, invocation: str) -> ProgressSink | None:
        sink = request.progress_sink
        if sink is None:
            return None

        async def emit(event: dict[str, Any]) -> None:
            kind = str(event.get("kind", ""))
            messages = {
                "started": "开始处理任务。", "action": "正在调用工具。",
                "observation": "已收到工具观察。", "candidate": "候选产物已生成，等待验收。",
                "validation": "正在检查产物约束。", "review": "已收到独立审查结果。",
                "review_unit": "已完成一项审查。", "review_format_repaired": "审查格式已修复，等待独立复核。",
                "finished": "本次执行已结束，结果以验收状态为准。",
            }
            if kind not in messages:
                return
            payload = {"kind": "review" if kind.startswith("review_") else kind,
                       "phase": str(event.get("phase", kind)), "invocation": invocation,
                       "message": messages[kind]}
            try:
                await sink(payload)
            except Exception as exc:
                # The loop's durable trace is authoritative; UI delivery must
                # not turn a completed operation into a failed/replayed one.
                from loguru import logger
                logger.warning("Agent progress delivery failed: {}", type(exc).__name__)

        return emit

    def review_messages(self, request: RunRequest, context: ContextPack) -> list[Message] | None:
        return None

    def submission_schema(self, request: RunRequest) -> dict[str, Any] | None:
        if not self.native_structured_delivery:
            return None
        schema: dict[str, Any] = json.loads((repo_root() / "backend/app/harness/schema/schemas" / (self.output_schema + ".json")).read_text())
        return schema

    def required_review_tools(self, request: RunRequest) -> tuple[str, ...]:
        return ()

    def loop_stop_condition(self, request: RunRequest) -> StopCondition | None:
        return None

    def loop_stop_contract_id(self, request: RunRequest) -> str | None:
        return None

    def loop_registry(self, request: RunRequest, context: ContextPack) -> "ToolRegistry":
        from app.harness.tools.registry import get_registry
        return get_registry()

    def configured_read_tools(self) -> tuple[str, ...] | None:
        return None

    async def _draft_via_llm(self, request: RunRequest, context: ContextPack, *,
                             debate_role: str | None = None) -> Artifact:
        from app.harness.tools.registry import ToolContext
        from app.harness.tools.config import tool_config
        from app.harness.agent_loop.review import ExternalReview
        run_root = Path(str(request.extra.get("run_root") or request.runtime.get("run_root") or
                            repo_root() / "runs" / ("agent_" + uuid.uuid4().hex))).resolve()
        request.extra["run_root"] = str(run_root)
        invocation = str(request.extra.get("resume_invocation") or request.extra.get("invocation_id")
                         or request.runtime.get("invocation_id") or uuid.uuid4().hex)
        if not invocation.replace("-", "").isalnum():
            raise ValueError("invalid invocation ID")
        trace_root = run_root / "agent_traces" / self.name / invocation
        context.metadata["loop_trace_root"] = str(trace_root)
        correlation = {key: str(request.extra[key]) for key in
                       ("task_id", "parent_task_id", "parent_invocation_id", "trace_id", "node_id")
                       if request.extra.get(key) is not None}
        correlation.update({str(key): str(value) for key, value in request.extra.get("correlation", {}).items()})
        correlation.update(invocation_id=invocation)
        correlation.setdefault("trace_id", str(request.extra.get("run_id", run_root.name)))
        correlation.setdefault("node_id", str(request.extra.get("node_key", self.name)))
        correlation.setdefault("task_id", correlation["node_id"])
        tools = tuple(name for name in self.config.tools if tool_config(name).enabled)
        registry = self.loop_registry(request, context)
        read_tools = self.configured_read_tools()
        read_scope = registry.scope_for_read_tools(self.name, read_tools) if read_tools else None
        provider, config = self._select_provider()

        async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
            return await self.validate_candidate(request, text, observations)

        try:
            review = self._select_review_provider()
        except Exception:
            await provider.close()
            raise
        result = await self._executor.run(LoopInput(
            messages=self._messages_for_context(request, context, purpose="loop"),
            provider=provider, config=config, registry=registry,
            review_provider=review[0] if review else None, review_config=review[1] if review else None,
            tool_context=ToolContext(run_id=str(request.extra.get("run_id", run_root.name)),
                                     project=request.project, agent=self.name,
                                     extra={"run_root": str(run_root), "correlation": correlation}, configured_read_scope=read_scope),
            tools=tools, policy=self.loop_policy, trace_root=trace_root, validate=validate,
            correlation=correlation, context_metadata=dict(context.metadata),
            reflection_rubric=self.reflection_rubric(), resume=bool(request.extra.get("resume_invocation")),
            progress_sink=self.loop_progress_sink(request, invocation),
            review_messages=self.review_messages(request, context),
            required_review_tools=self.required_review_tools(request),
            stop_condition=self.loop_stop_condition(request),
            stop_contract_id=self.loop_stop_contract_id(request),
            final_schema=self.submission_schema(request),
            external_review=(ExternalReview.from_mapping(request.extra["external_review"])
                             if "external_review" in request.extra else None),
        ))
        context.metadata["loop_status"] = result.status
        request.runtime["observations"] = result.observations
        context.metadata["reflection_accepted"] = result.reflection_accepted
        if result.status != "passed":
            raise RuntimeError(f"{self.name} loop {result.status}; evidence: {trace_root}")
        return self._artifact_from_completion(Completion(text=result.text, provider=config.provider,
                                                         model=config.model, debate_role=debate_role))

    def _artifact_from_completion(self, completion: Completion) -> Artifact:
        # The loop already validated this exact text. Preserve even trailing
        # whitespace so the published artifact stays bound to its review digest.
        cleaned = completion.text
        try:
            parsed = parse_frontmatter(cleaned)
        except Exception:
            parsed = None
        metadata = parsed.metadata if parsed else {}
        return Artifact(text=cleaned, schema_id=str(metadata.get("schema", self.output_schema)),
                        metadata=metadata, body=parsed.body if parsed else cleaned,
                        debate_role=completion.debate_role)

    @staticmethod
    def _unwrap_llm_text(text: str) -> str:
        # Never add fields, close incomplete frontmatter, or discard a preamble to make
        # an invalid model document appear valid.
        return text.strip()
