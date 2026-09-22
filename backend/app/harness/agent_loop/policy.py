"""Validated budgets; no implicit provider/framework fallback."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from collections.abc import Mapping
import re
from typing import Any, Literal


@dataclass(frozen=True)
class AgentLoopPolicy:
    protocol: Literal["json_actions", "native_tools"] = "json_actions"
    mode: Literal["react", "reflection"] = "react"
    trace: Literal["full", "metadata", "off"] = "full"
    reflection_reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    reflection_thinking_enabled: bool | None = None
    max_model_calls: int | None = 36
    max_tool_steps: int = 18
    max_protocol_repairs: int = 4
    max_validation_repairs: int = 6
    max_reflections: int = 3
    input_token_budget: int = 24000
    observation_chars: int = 6000
    reflection_format_repair_enabled: bool = False
    author_empty_completion_repair_enabled: bool = False
    native_observation_history: bool = False
    document_revisions_enabled: bool = False
    deduplicate_evidence_enabled: bool = False
    submission_body_field: str = ""

    def __post_init__(self) -> None:
        if self.protocol not in {"json_actions", "native_tools"}:
            raise ValueError("unsupported loop protocol")
        if self.mode not in {"react", "reflection"}:
            raise ValueError("mode must be react or reflection")
        if self.trace not in {"full", "metadata", "off"}:
            raise ValueError("trace must be full, metadata or off")
        if self.reflection_reasoning_effort not in {None, "low", "medium", "high", "max"}:
            raise ValueError("unsupported reflection_reasoning_effort")
        if self.reflection_thinking_enabled is not None and not isinstance(self.reflection_thinking_enabled, bool):
            raise ValueError("reflection_thinking_enabled must be a boolean or null")
        if not isinstance(self.reflection_format_repair_enabled, bool):
            raise ValueError("reflection_format_repair_enabled must be a boolean")
        if not isinstance(self.author_empty_completion_repair_enabled, bool):
            raise ValueError("author_empty_completion_repair_enabled must be a boolean")
        if self.author_empty_completion_repair_enabled and self.trace != "full":
            raise ValueError("author_empty_completion_repair_enabled requires full auditable traces")
        if not isinstance(self.native_observation_history, bool):
            raise ValueError("native_observation_history must be a boolean")
        if self.native_observation_history and self.protocol != "native_tools":
            raise ValueError("native_observation_history requires native_tools")
        for name in ("document_revisions_enabled", "deduplicate_evidence_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if self.document_revisions_enabled and self.protocol != "native_tools":
            raise ValueError("document_revisions_enabled requires native_tools")
        if not isinstance(self.submission_body_field, str) or (self.submission_body_field
                and (self.protocol != "native_tools" or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.submission_body_field))):
            raise ValueError("submission_body_field requires native_tools and a simple metadata field name")
        for item in fields(self):
            if item.name in {"mode", "trace", "reflection_reasoning_effort", "reflection_thinking_enabled", "protocol",
                             "reflection_format_repair_enabled", "author_empty_completion_repair_enabled", "native_observation_history",
                             "document_revisions_enabled", "deduplicate_evidence_enabled", "submission_body_field"}:
                continue
            value = getattr(self, item.name)
            if item.name == "max_model_calls" and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{item.name} must be a nonnegative integer")
        if self.max_model_calls is not None and not 1 <= self.max_model_calls <= 128:
            raise ValueError("max_model_calls must be null (unlimited) or in [1,128]")
        if not 4000 <= self.input_token_budget <= 512000:
            raise ValueError("input_token_budget must be in [4000,512000]")
        if self.observation_chars < 512 or self.max_reflections < 1:
            raise ValueError("observation_chars >=512 and max_reflections >=1 required")

    def remaining_model_calls(self, used: int) -> int | None:
        """None means no count limit; usage counters still accumulate normally."""
        return None if self.max_model_calls is None else max(0, self.max_model_calls - used)

    def allows_model_calls(self, used: int, required: int = 1) -> bool:
        remaining = self.remaining_model_calls(used)
        return remaining is None or remaining >= required

    def fingerprint_data(self) -> dict[str, Any]:
        """Keep pre-feature checkpoints compatible when repair is not enabled."""
        data = asdict(self)
        if self.reflection_format_repair_enabled:
            data["reflection_format_repair_contract_version"] = 1
        else:
            data.pop("reflection_format_repair_enabled")
        if self.author_empty_completion_repair_enabled:
            data["author_empty_completion_repair_contract_version"] = 1
        else:
            data.pop("author_empty_completion_repair_enabled")
        if not self.native_observation_history:
            data.pop("native_observation_history")
        for name in ("document_revisions_enabled", "deduplicate_evidence_enabled", "submission_body_field"):
            if not data[name]:
                data.pop(name)
        return data

    @classmethod
    def from_mapping(cls, raw: object) -> AgentLoopPolicy:
        data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        allowed = {f.name for f in fields(cls)}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown loop settings: {sorted(unknown)}")
        return cls(**dict(data))
