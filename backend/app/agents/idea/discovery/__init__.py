"""Co-Scientist deep hypothesis discovery inside the Idea Agent."""

from app.agents.idea.discovery.backend import (
    DeterministicRoleBackend,
    DiscoveryProtocolError,
    DiscoveryRoleBackend,
    LLMRoleBackend,
)
from app.agents.idea.discovery.models import (
    DeepDiscoveryConfig,
    DeepDiscoveryState,
    IdeaBudgetProfile,
    IdeaMode,
)
from app.agents.idea.discovery.storage import RunLocalDiscoveryStore
from app.agents.idea.discovery.workflow import (
    CoScientistWorkflow,
    DeepDiscoveryInsufficientPool,
    build_discovery_context,
    discovery_root_for_request,
    resolve_idea_mode,
)

__all__ = [
    "CoScientistWorkflow",
    "DeepDiscoveryConfig",
    "DeepDiscoveryInsufficientPool",
    "DeepDiscoveryState",
    "DeterministicRoleBackend",
    "DiscoveryProtocolError",
    "DiscoveryRoleBackend",
    "IdeaBudgetProfile",
    "IdeaMode",
    "LLMRoleBackend",
    "RunLocalDiscoveryStore",
    "build_discovery_context",
    "discovery_root_for_request",
    "resolve_idea_mode",
]
