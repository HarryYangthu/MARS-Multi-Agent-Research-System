"""Tool registry + dispatch with Gate 5 hook.

★ CLAUDE.md hard constraint: Gate 5 (baseline_compatibility) sits **here**,
on the dispatch path — not as a RunGraph checkpoint. Every tool call goes
through ``dispatch()`` and is screened by the Gate before the tool runs.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Awaitable

from loguru import logger


ToolFn = Callable[[dict[str, Any], "ToolContext"], Awaitable["ToolResult"]]


@dataclass
class ToolContext:
    run_id: str
    project: str
    agent: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None
    blocked_by_gate: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}
        self._gates: list[Callable[[str, dict[str, Any], ToolContext], Awaitable["GateDecision"]]] = []

    def register(self, name: str, fn: ToolFn) -> None:
        if name in self._tools:
            raise ValueError(f"tool '{name}' already registered")
        self._tools[name] = fn

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def install_gate(
        self,
        check: Callable[[str, dict[str, Any], ToolContext], Awaitable["GateDecision"]],
    ) -> None:
        self._gates.append(check)

    async def dispatch(
        self, tool_name: str, args: dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        # ★ Gate 5 (and any future gate hooks) run here, before the tool fn.
        for gate in self._gates:
            decision = await gate(tool_name, args, ctx)
            if decision.action == "block":
                logger.warning(
                    "Gate '{}' blocked tool '{}' (run={}, agent={}): {}",
                    decision.gate_id, tool_name, ctx.run_id, ctx.agent, decision.reason,
                )
                return ToolResult(
                    ok=False,
                    error=decision.reason,
                    blocked_by_gate=decision.gate_id,
                )
        if tool_name not in self._tools:
            return ToolResult(ok=False, error=f"unknown tool '{tool_name}'")
        try:
            return await self._tools[tool_name](args, ctx)
        except Exception as exc:
            logger.exception("tool '{}' raised", tool_name)
            return ToolResult(ok=False, error=str(exc))


@dataclass
class GateDecision:
    gate_id: str
    action: str  # "allow" | "block" | "require_human"
    reason: str = ""


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _install_default_gates(_registry)
    return _registry


def reset_for_tests() -> ToolRegistry:
    global _registry
    _registry = ToolRegistry()
    _install_default_gates(_registry)
    return _registry


def _install_default_gates(reg: ToolRegistry) -> None:
    # Lazy import to avoid pulling gates/ during the very early bootstrap.
    from app.harness.gates.baseline_compatibility import gate_check

    reg.install_gate(gate_check)
