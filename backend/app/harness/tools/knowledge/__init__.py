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

from app.harness.kb.stores import MAIN_ZONES, QUARANTINE_ZONE
from app.harness.tools.registry import ToolContext, ToolResult


def _source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {"schema", "artifact_schema", "record_id", "zone", "memory_type",
               "source_path", "url", "title", "run_id", "agent", "project", "content_hash",
               "approved", "scientific_validated", "source_sha256", "valid_from", "ttl_days",
               "embedding_version"}
    return {key: value for key, value in metadata.items() if key in allowed}


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
                    "excerpt": h.record.text[:700],
                    "meta": _source_metadata(h.record.metadata),
                    "metadata": _source_metadata(h.record.metadata),
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
    from app.harness.research_memory import ResearchMemory
    try:
        memory = ResearchMemory(ctx.project)
        retained = memory.retrieve(str(plan), protocol=plan.get("protocol"), limit=10)
        compatible = next((h for h in retained["hits"] if h["comparison"]["reuse_allowed"] and h["roles"]), None)
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(
        ok=True,
        output={
            "matched_run_id": compatible["id"] if compatible else None,
            "match_score": 1.0 if compatible else 0.0,
            "reuse_recommended": compatible is not None,
            "matched_artifacts": [compatible["detail_ref"]] if compatible else [],
            "recommended_action": "reuse" if compatible else "inspect_missing_conditions",
            "research_memory": retained,
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
    reported_mock = bool(metadata.get("is_mock", False))
    metadata = {
        "title": str(metadata.get("title", "")),
        "source": metadata.get("source", "tool"),
        "run_id": ctx.run_id,
        "agent": ctx.agent,
        "project": ctx.project,
        "origin": "agent_unreviewed",
        "proposed_zone": zone,
        "scientific_validated": False,
    }
    count = write_to_zone(
        zone=QUARANTINE_ZONE,
        text=text,
        metadata=metadata,
        source_path="",
        run_id=ctx.run_id,
        agent=ctx.agent,
        is_mock=reported_mock,
        approved=False,
    )
    return ToolResult(
        ok=True,
        output={"zone": QUARANTINE_ZONE, "requested_zone": zone, "records_written": count,
                "approval_status": "pending", "recall_eligible": False},
        evidence_refs=[f"knowledge/{QUARANTINE_ZONE}/_index.json"],
    )


# zone-bound query tools
experiment_memory_tool = _zone_tool("run_archive")
code_assets_tool = _zone_tool("code_assets")
methodology_tool = _zone_tool("methodology")
run_archive_tool = _zone_tool("run_archive")


async def research_memory_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.harness.research_memory import ResearchMemory
    try:
        output = ResearchMemory(ctx.project).retrieve(str(args.get("query") or args.get("q") or ""),
                limit=int(args.get("limit", 4)), protocol=args.get("protocol"))
        return ToolResult(ok=True, output=output, evidence_refs=[h["detail_ref"] for h in output["hits"]])
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, error=str(exc))


async def research_memory_read_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from app.harness.research_memory import ResearchMemory
    try:
        row = ResearchMemory(ctx.project).get(str(args.get("record_id", "")))
        offset = int(args.get("evidence_offset", 0))
        if offset < 0:
            raise ValueError("evidence_offset不能为负数")
        assets = row.pop("evidence")
        row["evidence"] = assets[offset:offset+20]
        row["evidence_count"] = len(assets)
        row["next_evidence_offset"] = offset+20 if offset+20 < len(assets) else None
        return ToolResult(ok=True, output=row, evidence_refs=[f"research_memory/{row['id']}"])
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, error=str(exc))
