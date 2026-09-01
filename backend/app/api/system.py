"""Distribution and capability introspection for V3 clients."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.bridge.extension_runtime import get_extension_runtime

router = APIRouter(prefix="/api/system", tags=["system"])


class ProjectPackVersionView(BaseModel):
    project_id: str
    pack_version: str
    distribution: str
    capabilities: list[str] = Field(default_factory=list)


class SystemVersionView(BaseModel):
    schema_id: str = "system_version.v1"
    distribution: str
    version: str
    core_version: str
    capabilities: list[str] = Field(default_factory=list)
    project_packs: list[ProjectPackVersionView] = Field(default_factory=list)
    adapters: list[str] = Field(default_factory=list)


@router.get("/version", response_model=SystemVersionView)
async def get_system_version() -> SystemVersionView:
    runtime = get_extension_runtime()
    return SystemVersionView(
        distribution=runtime.profile.name,
        version=runtime.profile.version,
        core_version=runtime.profile.core_version,
        capabilities=list(runtime.profile.capabilities),
        project_packs=[
            ProjectPackVersionView(
                project_id=pack.manifest.project_id,
                pack_version=pack.manifest.pack_version,
                distribution=pack.manifest.distribution,
                capabilities=list(pack.manifest.capabilities),
            )
            for pack in runtime.project_packs.list()
        ],
        adapters=list(runtime.adapters.names()),
    )
