"""Validated budgets; no implicit provider/framework fallback."""
from __future__ import annotations

from dataclasses import dataclass, fields
from collections.abc import Mapping
from typing import Any, Literal


@dataclass(frozen=True)
class AgentLoopPolicy:
    mode: Literal["react", "reflection"] = "react"
    trace: Literal["full", "metadata", "off"] = "full"
    reflection_reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    max_model_calls: int = 36
    max_tool_steps: int = 18
    max_protocol_repairs: int = 4
    max_validation_repairs: int = 6
    max_reflections: int = 3
    input_token_budget: int = 24000
    observation_chars: int = 6000

    def __post_init__(self) -> None:
        if self.mode not in {"react", "reflection"}:
            raise ValueError("mode must be react or reflection")
        if self.trace not in {"full", "metadata", "off"}:
            raise ValueError("trace must be full, metadata or off")
        if self.reflection_reasoning_effort not in {None, "low", "medium", "high", "max"}:
            raise ValueError("unsupported reflection_reasoning_effort")
        for item in fields(self):
            if item.name in {"mode", "trace", "reflection_reasoning_effort"}:
                continue
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{item.name} must be a nonnegative integer")
        if not 1 <= self.max_model_calls <= 128:
            raise ValueError("max_model_calls must be in [1,128]")
        if not 4000 <= self.input_token_budget <= 128000:
            raise ValueError("input_token_budget must be in [4000,128000]")
        if self.observation_chars < 512 or self.max_reflections < 1:
            raise ValueError("observation_chars >=512 and max_reflections >=1 required")

    @classmethod
    def from_mapping(cls, raw: object) -> AgentLoopPolicy:
        data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        allowed = {f.name for f in fields(cls)}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown loop settings: {sorted(unknown)}")
        return cls(**dict(data))
