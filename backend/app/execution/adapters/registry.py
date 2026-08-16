"""In-process composition registry for trusted ProjectAdapter instances."""
from __future__ import annotations

from app.execution.adapters.base import ProjectAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProjectAdapter] = {}

    def register(self, name: str, adapter: ProjectAdapter) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("adapter name must not be empty")
        if normalized in self._adapters:
            raise ValueError(f"duplicate adapter '{normalized}'")
        self._adapters[normalized] = adapter

    def get(self, name: str) -> ProjectAdapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise KeyError(f"unknown adapter '{name}'") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
