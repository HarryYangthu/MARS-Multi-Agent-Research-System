"""Knowledge-base tools registered through the generic tool registry.

These wrap the 4-zone ChromaDB layer (``harness/kb``) so any Agent whose
``configs/agents.yaml`` ``tools:`` list references a ``knowledge.*`` tool can
retrieve from the corresponding zone during its ReAct gather loop. Each tool
returns a structured ``ToolResult`` — never raises — so a missing/empty store
degrades to ``ok=True`` with zero hits rather than breaking the run.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.harness.kb.stores import MAIN_ZONES
from app.harness.tools.registry import ToolContext, ToolResult


def _render_hits(
    query: str,
    zone: str,
    top_k: int,
    *,
    project: str = "",
    memory_type: str | None = None,
    include_mock: bool = False,
    include_superseded: bool = False,
) -> ToolResult:
    from app.harness.kb.retriever import query as kb_query

    if not query:
        return ToolResult(ok=False, error="query (q) is required")
    if zone not in MAIN_ZONES:
        return ToolResult(ok=False, error=f"unknown zone '{zone}'")
    hits = kb_query(
        query=query,
        zones=[zone],
        top_k=top_k,
        project=project or None,
        memory_type=memory_type,
        include_mock=include_mock,
        include_superseded=include_superseded,
    )
    return ToolResult(
        ok=True,
        output={
            "zone": zone,
            "query": query,
            "hits": [
                {
                    "score": round(h.score, 4),
                    "excerpt": h.record.metadata.get("summary") or h.record.text[:280],
                    "meta": h.record.metadata,
                    "metadata": h.record.metadata,
                    "evidence_ref": f"knowledge/{zone}/{h.record.id}",
                }
                for h in hits
            ],
        },
    )


def _zone_tool(zone: str) -> Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]:
    async def _tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("q") or args.get("query") or "")
        top_k = int(args.get("top_k", 3) or 3)
        memory_type_raw = args.get("memory_type")
        memory_type = str(memory_type_raw) if memory_type_raw else None
        return _render_hits(
            query,
            zone,
            top_k,
            project=ctx.project,
            memory_type=memory_type,
            include_mock=bool(args.get("include_mock", False)),
            include_superseded=bool(args.get("include_superseded", False)),
        )

    return _tool


async def kb_query_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Cross-zone query; ``zone`` arg selects the KB partition (default literature)."""
    query = str(args.get("q") or args.get("query") or "")
    zone = str(args.get("zone", "literature"))
    top_k = int(args.get("top_k", 3) or 3)
    memory_type_raw = args.get("memory_type")
    memory_type = str(memory_type_raw) if memory_type_raw else None
    return _render_hits(
        query,
        zone,
        top_k,
        project=ctx.project,
        memory_type=memory_type,
        include_mock=bool(args.get("include_mock", False)),
        include_superseded=bool(args.get("include_superseded", False)),
    )


async def baseline_match_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Match an experiment plan against archived runs for reuse decisions."""
    from app.harness.kb.baseline_matcher import find_match

    plan = args.get("plan")
    if not isinstance(plan, dict):
        return ToolResult(ok=False, error="plan (object) is required")
    threshold = float(args.get("threshold", 0.85) or 0.85)
    match = find_match(plan=plan, threshold=threshold)
    return ToolResult(
        ok=True,
        output={
            "matched_run_id": match.matched_run_id,
            "match_score": round(match.match_score, 4),
            "reuse_recommended": match.above(threshold),
            "matched_artifacts": (
                [f"runs/{match.matched_run_id}/execution/metrics.json"]
                if match.matched_run_id
                else []
            ),
            "recommended_action": "reuse" if match.above(threshold) else "rerun",
        },
    )


async def ingest_document_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Ingest user-provided text into one of the four KB zones."""
    from app.harness.kb.memory_writer import write_to_zone

    zone = str(args.get("zone", "literature"))
    text = str(args.get("text", ""))
    if zone not in MAIN_ZONES:
        return ToolResult(ok=False, error=f"unknown zone '{zone}'")
    if not text.strip():
        return ToolResult(ok=False, error="text is required")
    metadata_raw = args.get("metadata", {})
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata = {
        **metadata,
        "source": metadata.get("source", "tool"),
        "run_id": ctx.run_id,
        "agent": ctx.agent,
    }
    count = write_to_zone(
        zone=zone,
        text=text,
        metadata=metadata,
        source_path=str(metadata.get("source_path", "")),
        run_id=ctx.run_id,
        agent=ctx.agent,
        schema=str(metadata.get("schema", "")),
        is_mock=bool(metadata.get("is_mock", False)),
        approved=bool(metadata.get("approved", True)),
    )
    return ToolResult(
        ok=True,
        output={"zone": zone, "records_written": count},
        evidence_refs=[f"knowledge/{zone}/_index.json"],
    )


# zone-bound query tools
experiment_memory_tool = _zone_tool("run_archive")
code_assets_tool = _zone_tool("code_assets")
methodology_tool = _zone_tool("methodology")
run_archive_tool = _zone_tool("run_archive")
