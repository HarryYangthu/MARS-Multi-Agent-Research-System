"""Distribution identity shared by public Core and private overlays."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DistributionName = Literal["v30-core", "v31-wireless"]


@dataclass(frozen=True)
class DistributionProfile:
    name: DistributionName
    version: str
    core_version: str
    capabilities: tuple[str, ...]


V30_CORE = DistributionProfile(
    name="v30-core",
    version="3.0.0-dev",
    core_version="3.0.0-dev",
    capabilities=(
        "multi_agent_research",
        "idea_deep_discovery",
        "model_discovery",
        "project_packs",
    ),
)


def profile_for(name: str) -> DistributionProfile:
    if name == "v30-core":
        return V30_CORE
    if name == "v31-wireless":
        return DistributionProfile(
            name="v31-wireless",
            version="3.1.0-internal.1-dev",
            core_version=V30_CORE.core_version,
            capabilities=V30_CORE.capabilities + ("wireless_overlay",),
        )
    raise ValueError(f"unknown MARS distribution '{name}'")
