"""Execution-owned seam for trusted code-workspace resolution."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.execution.adapters.base import AdapterRequest
from app.harness.discovery.code_workspace_transfer import (
    CodeWorkspaceTransferPackage,
    CodeWorkspaceTransferReceipt,
)

WORKSPACE_CONFIG_KEY = "_mars_code_workspace"
WORKSPACE_ARCHIVE_UPLOAD_NAME = "code_workspace_archive"
WORKSPACE_ARCHIVE_REMOTE_PATH = "inputs/code_workspace.tar"
WORKSPACE_RECEIPT_UPLOAD_NAME = "code_workspace_receipt"
WORKSPACE_RECEIPT_REMOTE_PATH = "inputs/code_workspace_receipt.json"


class AdapterWorkspaceBinding(BaseModel):
    """Serializable content identity; it intentionally contains no local path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["adapter_workspace.v1"] = "adapter_workspace.v1"
    relative_path: Literal["workspace"] = "workspace"
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    workspace_id: str = Field(pattern=r"^codews_[0-9a-f]{24}$")
    base_snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    snapshot_manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    code_spec_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    archive_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WorkspaceResolver(Protocol):
    """Composition-provided resolver; execution never imports its implementation."""

    async def resolve(
        self,
        request: AdapterRequest,
    ) -> CodeWorkspaceTransferPackage | None: ...


def bind_workspace_request(
    request: AdapterRequest,
    binding: AdapterWorkspaceBinding,
) -> AdapterRequest:
    if WORKSPACE_CONFIG_KEY in request.config:
        raise ValueError(f"AdapterRequest.config reserves {WORKSPACE_CONFIG_KEY!r}")
    return request.model_copy(
        update={
            "config": {
                **request.config,
                WORKSPACE_CONFIG_KEY: binding.model_dump(mode="json"),
            }
        }
    )


def workspace_binding_from_request(
    request: AdapterRequest,
) -> AdapterWorkspaceBinding | None:
    raw = request.config.get(WORKSPACE_CONFIG_KEY)
    if raw is None:
        return None
    return AdapterWorkspaceBinding.model_validate(raw)


def workspace_binding_for_receipt(
    receipt: CodeWorkspaceTransferReceipt,
    *,
    receipt_sha256: str,
) -> AdapterWorkspaceBinding:
    return AdapterWorkspaceBinding(
        candidate_id=receipt.candidate_id,
        workspace_id=receipt.workspace_id,
        base_snapshot_id=receipt.base_snapshot_id,
        snapshot_manifest_sha256=receipt.snapshot_manifest_sha256,
        code_spec_sha256=receipt.code_spec_sha256,
        bundle_sha256=receipt.bundle_sha256,
        workspace_manifest_sha256=receipt.workspace_manifest_sha256,
        archive_sha256=receipt.archive_sha256,
        receipt_sha256=receipt_sha256,
    )
