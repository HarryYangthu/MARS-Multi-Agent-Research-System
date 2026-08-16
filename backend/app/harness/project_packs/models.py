"""Public Project Pack v1 manifest contract."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PackFiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str = "project.yaml"
    rules: str = "AGENTS.md"
    repo_link: str = "repo_link.yaml"
    metrics: str = "metrics.yaml"
    discovery: str = "discovery.yaml"
    workflow: str = "workflow.yaml"
    ui_schema: str = "ui_schema.json"


class AdapterDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["adapter.v1"] = "adapter.v1"
    argv: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float = Field(default=900.0, gt=0.0)
    trusted: bool = True


class ProjectPackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["project_pack.v1"] = "project_pack.v1"
    project_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=1)
    pack_version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    requires_core: str = ">=3.0.0,<3.1.0"
    distribution: Literal["public", "private"] = "public"
    capabilities: tuple[str, ...] = ()
    files: PackFiles = Field(default_factory=PackFiles)
    adapters: dict[str, AdapterDeclaration] = Field(default_factory=dict)
