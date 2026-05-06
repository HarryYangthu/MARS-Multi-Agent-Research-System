"""BaseAgent abstract class.

Per DESIGN §5.1, every Agent exposes ``build_context`` / ``draft`` /
``revise`` / ``validate_output`` / ``submit_for_review``. V0's BaseAgent
provides default implementations for the latter three; subclasses only have
to implement ``draft`` (and optionally override ``build_context``).

The Agent does NOT depend on bridge/ or api/ — by .importlinter contract.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.harness.llm.model_registry import AgentConfig, get_agent_config, select_provider
from app.harness.llm.provider_base import Completion, LLMConfig, LLMProvider, Message
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import (
    ValidationResult,
    validate_document,
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
        # Pull the schema's reference template (templates/artifacts/<schema>.md)
        # so the LLM has a concrete example of the EXACT format we accept.
        # This is what fixes "DeepSeek replied prose without YAML frontmatter".
        from pathlib import Path

        try:
            from app.settings import repo_root

            tpl_path = repo_root() / "templates" / "artifacts" / f"{output_schema}.md"
            schema_template = tpl_path.read_text(encoding="utf-8") if tpl_path.exists() else ""
        except Exception:
            schema_template = ""

        sys_text = (
            self.system
            + "\n\n"
            + f"You are the **{agent_name}** Agent in a research pipeline. "
            + f"Your output MUST validate against the JSON Schema named `{output_schema}`.\n\n"
            + "FORMAT RULES (strict):\n"
            + "1. Reply with a single markdown document.\n"
            + "2. The very first line of your reply MUST be `---` (no leading prose, no code fences).\n"
            + "3. The document begins with YAML frontmatter delimited by `---` lines.\n"
            + "4. The frontmatter MUST contain every required field for the schema.\n"
            + "5. Below the closing `---` write the body in markdown.\n"
            + "6. NEVER wrap the whole document in ```markdown ... ``` fences.\n\n"
            + (
                "REFERENCE TEMPLATE for this schema (copy the structure, replace values):\n\n"
                + schema_template
                + "\n\n"
                if schema_template
                else ""
            )
            + "Now produce a fresh, schema-conforming document for the user's task below."
        )
        msgs = [Message(role="system", content=sys_text)]
        if self.project:
            msgs.append(Message(role="system", content=self.project))
        for label, content in self.upstream.items():
            msgs.append(
                Message(
                    role="user",
                    content=f"[upstream:{label}]\n{content}",
                )
            )
        if self.task:
            msgs.append(Message(role="user", content=self.task))
        return msgs


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
    """Common Agent skeleton.

    Subclasses set ``name`` and ``output_schema`` (string) and override
    ``draft`` to actually call the LLM.
    """

    name: str = "base"
    output_schema: str = ""

    def __init__(self, *, agent_config: AgentConfig | None = None) -> None:
        self._config = agent_config or get_agent_config(self.name)
        if not self.output_schema:
            self.output_schema = self._config.output_schema

    @property
    def config(self) -> AgentConfig:
        return self._config

    # ------------------------------------------------------------- defaults

    async def build_context(self, request: RunRequest) -> ContextPack:
        return ContextPack(
            system=f"MARS {self.name} agent. Output schema: {self.output_schema}.",
            project=f"Project: {request.project}.",
            task=request.user_request,
            upstream=dict(request.upstream_artifacts),
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
            user_request=feedback.comment or "Please incorporate the human edits.",
            upstream_artifacts={"previous_version": artifact.text},
        )
        ctx = await self.build_context(request)
        return await self.draft(request, ctx)

    # ------------------------------------------------------------- helpers

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
            completion = await provider.complete(list(messages), cfg)
            return completion
        except Exception as exc:
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
        messages = context.to_messages(
            agent_name=self.name, output_schema=self.output_schema
        )
        completion = await self._call_llm(messages, debate_role=debate_role)
        return self._artifact_from_completion(completion)

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
        return s


__all__ = [
    "Artifact",
    "BaseAgent",
    "ContextPack",
    "HumanFeedback",
    "RunRequest",
]


# Run loop helper (pure asyncio; not used by orchestrator yet)
async def _ensure_loop() -> None:
    await asyncio.sleep(0)
