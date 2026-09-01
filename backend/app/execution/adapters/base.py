"""Stable adapter.v1 request/response contract."""
from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AdapterAction(str, Enum):
    READINESS = "readiness"
    PREFLIGHT = "preflight"
    EXECUTE = "execute"
    EVALUATE = "evaluate"
    PROFILE = "profile"


class AdapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: str = "adapter.v1"
    action: AdapterAction
    request_id: str = Field(min_length=1)
    project: str = Field(min_length=1)
    run_id: str = ""
    candidate_id: str = ""
    fidelity: str = "F0"
    seed: int = 0
    repo_snapshot_ref: str = ""
    data_manifest_ref: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    output_dir: str = ""


class AdapterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: str = "adapter.v1"
    request_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(ready|ok|blocked|failed)$")
    raw_metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    resource_usage: dict[str, float] = Field(default_factory=dict)
    findings: tuple[str, ...] = ()
    error_code: str = ""
    error: str = ""


class ProjectAdapter(Protocol):
    @property
    def name(self) -> str: ...

    async def invoke(self, request: AdapterRequest) -> AdapterResponse: ...
