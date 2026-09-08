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
        if (not isinstance(configured, dict) or set(configured) - set(sources)
                or any(not isinstance(value, bool) for value in configured.values())):
            raise ValueError("context_sources accepts only project_rules/code_repositories booleans")
        sources.update(configured)
        required = request.extra.get("required_upstream_refs", [])
        if not isinstance(required, list) or any(x not in request.upstream_artifacts for x in required):
            raise ValueError("required_upstream_refs must name supplied upstream artifacts")
        project_path = repo_root() / "projects" / request.project
        if not project_path.resolve().is_relative_to((repo_root() / "projects").resolve()):
            raise ValueError("invalid project path")
        rules_path = project_path / "AGENTS.md"
        rules = (rules_path.read_text() if sources["project_rules"] and rules_path.is_file()
                 else "No project-specific rules supplied in this context.")
        repositories = (load_agent_code_repositories(self.name, project=request.project)
                        if sources["code_repositories"] else ())
        upstream = dict(request.upstream_artifacts)
        metadata: dict[str, Any] = {"required_upstream_refs": required, "context_sources": sources}
        if repositories:
            upstream[f"{self.name}_code_repositories"] = json.dumps(
                [asdict(repository) for repository in repositories], ensure_ascii=False)
            metadata[f"{self.name}_code_repository_count"] = len(repositories)
        return ContextPack(
            system=f"MARS {self.name} agent. {self.agent_brief}",
            project=f"Project: {request.project}.\nProject constraints:\n{rules}",
            task=request.user_request, upstream=upstream, metadata=metadata,
        )

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
        artifact = await self.draft(request, context)
        result = await self.validate_output(artifact)
        if not result.valid:
            raise RuntimeError("Agent output is not schema-valid: " + str(result.first_error()))
        return artifact

    def _select_provider(self) -> tuple[LLMProvider, LLMConfig]:
        return select_provider(self._config)

    async def _call_llm(self, messages: Sequence[Message], *,
                        debate_role: str | None = None) -> Completion:
        provider, config = self._select_provider()
        if debate_role:
            config.extra["debate_role"] = debate_role
        try:
            return await asyncio.wait_for(provider.complete(list(messages), config),
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
        return errors

    def reflection_rubric(self) -> str:
        return "Verify evidence, definitions, every numerical claim, falsifiability, and downstream implementation completeness."

    def loop_progress_sink(self, request: RunRequest, invocation: str) -> ProgressSink | None:
        return request.progress_sink

    def review_messages(self, request: RunRequest, context: ContextPack) -> list[Message] | None:
        return None

    def submission_schema(self, request: RunRequest) -> dict[str, Any] | None:
        if not self.native_structured_delivery or self.loop_policy.protocol != "native_tools":
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

    async def _draft_via_llm(self, request: RunRequest, context: ContextPack, *,
                             debate_role: str | None = None) -> Artifact:
        from app.harness.tools.registry import ToolContext
        from app.harness.tools.config import tool_config
        from app.harness.agent_loop.review import ExternalReview
        run_root = Path(str(request.extra.get("run_root") or
                            repo_root() / "runs" / ("agent_" + uuid.uuid4().hex))).resolve()
        request.extra["run_root"] = str(run_root)
        invocation = str(request.extra.get("resume_invocation") or uuid.uuid4().hex)
        if not invocation.replace("-", "").isalnum():
            raise ValueError("invalid invocation ID")
        trace_root = run_root / "agent_traces" / self.name / invocation
        context.metadata["loop_trace_root"] = str(trace_root)
        tools = tuple(name for name in self.config.tools if tool_config(name).enabled)
        provider, config = self._select_provider()

        async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
            return await self.validate_candidate(request, text, observations)

        result = await self._executor.run(LoopInput(
            messages=self._messages_for_context(request, context, purpose="loop"),
            provider=provider, config=config, registry=self.loop_registry(request, context),
            tool_context=ToolContext(run_id=str(request.extra.get("run_id", run_root.name)),
                                     project=request.project, agent=self.name,
                                     extra={"run_root": str(run_root)}),
            tools=tools, policy=self.loop_policy, trace_root=trace_root, validate=validate,
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
