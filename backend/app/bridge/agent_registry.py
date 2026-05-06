"""Inversion-of-dependency hub for Agents.

`bridge/` is forbidden by .importlinter from importing concrete agent
modules (``app.agents.idea`` etc.). Instead, Agents register themselves at
startup via ``register()`` and the Bridge looks them up by name.

Structural typing only — the registry doesn't depend on a BaseAgent class
that lives in ``agents/``. Anything that satisfies the ``RunnableAgent``
Protocol is acceptable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class RunnableAgent(Protocol):
    name: str
    output_schema: str

    async def draft(self, request: Any, context: Any) -> Any: ...
    async def revise(self, artifact: Any, feedback: Any) -> Any: ...


@dataclass
class AgentRegistry:
    _entries: dict[str, RunnableAgent] = field(default_factory=dict)

    def register(self, name: str, agent: RunnableAgent) -> None:
        if name in self._entries:
            raise ValueError(f"agent '{name}' already registered")
        self._entries[name] = agent

    def unregister(self, name: str) -> None:
        self._entries.pop(name, None)

    def get(self, name: str) -> RunnableAgent:
        if name not in self._entries:
            raise KeyError(f"no agent registered for '{name}'")
        return self._entries[name]

    def has(self, name: str) -> bool:
        return name in self._entries

    def names(self) -> list[str]:
        return list(self._entries.keys())


_registry = AgentRegistry()


def get_registry() -> AgentRegistry:
    return _registry


def reset_registry_for_tests() -> None:
    _registry._entries.clear()
