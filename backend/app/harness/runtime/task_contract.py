"""Portable task, handoff and outcome contracts consumed by runtime drivers."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TaskEnvelope(Contract):
    schema_id: Literal["task.v1"] = "task.v1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    parent_task_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    invocation_id: str = Field(pattern=r"^[A-Za-z0-9-]+$")
    parent_invocation_id: str | None = None
    predecessor_task_ids: list[str] = Field(default_factory=list)
    agent: str = Field(min_length=1)
    project: str = Field(min_length=1)
    goal: str
    attempt: int = Field(ge=1)
    output_schema: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_context_refs: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=lambda: ["schema", "agent_validation", "approval"])


class FailureEnvelope(Contract):
    schema_id: Literal["task.failure.v1"] = "task.failure.v1"
    task_id: str = Field(min_length=1)
    invocation_id: str | None = None
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    outcome_known: bool = True
    evidence_refs: list[str] = Field(default_factory=list)


class ResultEnvelope(Contract):
    schema_id: Literal["task.result.v1"] = "task.result.v1"
    task_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    status: Literal["awaiting_review", "invalid", "failed"]
    artifact_ref: str | None = None
    artifact_sha256: str | None = None
    schema_valid: bool = False
    failure: FailureEnvelope | None = None


class HandoffPrerequisite(Contract):
    kind: Literal["background", "baseline_code", "data_description", "analysis_results",
                  "metric_definition", "execution_environment", "other"]
    description: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    blocks_execution: bool


class HandoffEnvelope(Contract):
    schema_id: Literal["task.handoff.v1"] = "task.handoff.v1"
    source_ref: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    destination_task_id: str = Field(min_length=1)
    prerequisites: list[HandoffPrerequisite] = Field(default_factory=list)
    supplied_context_refs: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)


class HandoffBlockedError(ValueError):
    def __init__(self, handoffs: list[HandoffEnvelope]) -> None:
        self.handoffs = handoffs
        missing = sorted({kind for handoff in handoffs for kind in handoff.missing_context})
        super().__init__("Execution prerequisites missing: " + ", ".join(missing))
        self.reason = {"code": "handoff_context_missing", "missing_context": missing, "retryable": False}


def missing_prerequisites(prerequisites: list[HandoffPrerequisite], *, stage: str,
                          supplied_context: dict[str, str]) -> list[str]:
    """Planning may proceed with gaps; writing code needs code, execution needs all."""
    if stage not in {"coding", "execution"}:
        return []
    return sorted({item.kind for item in prerequisites if item.blocks_execution
                   and (stage == "execution" or item.kind == "baseline_code")
                   and not supplied_context.get(item.kind, "").strip()})
