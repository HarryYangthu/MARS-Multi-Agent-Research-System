"""Stop an evidence-starved lead without spending calls on impossible submissions."""
from __future__ import annotations

from pathlib import Path

from app.agents.idea.research import canonical_source
from app.agents.idea.research_delegate import TOOL, load_delegated_research
from app.harness.agent_loop.stop import LoopStop, LoopStopView

LEAD_STOP_CONTRACT = "idea.lead_research_stop.v1"


def lead_evidence_stop(view: LoopStopView, *, run_root: Path, min_sources: int,
                       max_delegations: int, max_tool_steps: int, can_delegate: bool) -> LoopStop | None:
    if view.stage != "before_model":
        return None
    started = {row["output"]["delegation_id"] for row in view.observations
               if row.get("tool") == TOOL and isinstance(row.get("output"), dict)
               and isinstance(row["output"].get("delegation_id"), str)}
    exhausted = (not can_delegate or len(started) >= max_delegations
                 or view.counts["tool_dispatches"] >= max_tool_steps)
    if not exhausted:
        return None
    reports, _ = load_delegated_research(run_root, view.observations)
    sources = {canonical_source(source["url"]) for item in reports
               for source in item["report"]["sources"] if source["decision"] == "use"}
    if len(sources) >= min_sources:
        return None
    reason = (f"合格调研报告仅覆盖 {len(sources)} 篇论文，要求至少 {min_sources} 篇；"
              "当前取证预算已用尽或没有可用委派工具，无法通过改写方案补足原文证据。")
    return LoopStop("evidence_unavailable", reason,
                    {"required_publications": min_sources, "verified_report_publications": len(sources),
                     "started_delegations": sorted(started), "max_delegations": max_delegations,
                     "tool_dispatches": view.counts["tool_dispatches"], "max_tool_steps": max_tool_steps,
                     "usable_as_final_evidence": False, "scientific_validated": False})
