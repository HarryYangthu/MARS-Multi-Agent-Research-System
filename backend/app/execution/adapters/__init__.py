"""Generic execution adapter protocol and registry."""

from app.execution.adapters.base import (
    AdapterAction,
    AdapterRequest,
    AdapterResponse,
    ProjectAdapter,
)
from app.execution.adapters.registry import AdapterRegistry

__all__ = [
    "AdapterAction",
    "AdapterRegistry",
    "AdapterRequest",
    "AdapterResponse",
    "ProjectAdapter",
]
