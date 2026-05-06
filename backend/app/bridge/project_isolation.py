"""Project isolation surface (V0 single-project; interface preserved for V1)."""
from __future__ import annotations

from dataclasses import dataclass

from app.settings import get_settings


@dataclass(frozen=True)
class ProjectContext:
    name: str


def current_project(name: str | None = None) -> ProjectContext:
    if name:
        return ProjectContext(name=name)
    return ProjectContext(name=get_settings().mars_default_project)
