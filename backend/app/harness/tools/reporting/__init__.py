"""Reporting tools exposed through the ToolRegistry dispatch path.

The harness-level registry must stay agent/storage agnostic. The concrete
report bundle generator is injected by bridge/API through ``ToolContext.extra``.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.harness.tools.registry import ToolContext, ToolResult


async def report_bundle_tool(args: dict[str, object], ctx: ToolContext) -> ToolResult:
    run_id = str(args.get("run_id") or ctx.run_id)
    generator_raw = ctx.extra.get("generate_report_bundle") if ctx.extra else None
    if not callable(generator_raw):
        return ToolResult(
            ok=False,
            error="reporting.generate_bundle requires a bridge-provided generator",
        )
    generator = _as_generator(generator_raw)
    result = generator(run_id, ctx.agent or "tool")
    metadata = result.get("metadata") if isinstance(result, dict) else {}
    return ToolResult(
        ok=True,
        output=result,
        artifacts=[
            {"path": str(result.get("manifest", "")), "kind": "report_bundle"},
        ],
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _as_generator(raw: Callable[..., Any]) -> Callable[[str, str], dict[str, Any]]:
    def _wrapped(run_id: str, actor: str) -> dict[str, Any]:
        result = raw(run_id, actor)
        if not isinstance(result, dict):
            return {"exists": False, "run_id": run_id, "error": "generator returned non-dict"}
        return result

    return _wrapped
