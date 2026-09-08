"""Real delegated Idea research evaluation; explicit development bypass of Bridge."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research import canonical_source, evidence_inventory
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
from app.harness.llm.model_registry import AgentConfig, get_agent_config
from app.harness.schema.validator import validate_document
from app.harness.schema.frontmatter_parser import parse
from app.agents.idea.research_dossier import dossier_errors
from app.settings import env_or_local, reset_settings_cache
from scripts.idea_research_continuation import check_configuration, fork_continuation, load_continuation

LEAD_TOOLS = {"idea.research_delegate", "knowledge.kb_query"}
CHILD_TOOLS = {"search.arxiv_search", "search.openalex_search", "search.fetch_sources", "knowledge.kb_query"}


def git_value(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def source_limit(scenario: dict[str, Any]) -> int:
    value = scenario.get("source_max_mib", 12)
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError("source_max_mib must be an integer in [1, 64]")
    return value


def evaluation_request(scenario: dict[str, Any], root: Path) -> RunRequest:
    if scenario.get("data_scope") != "public_research" or scenario.get("scope") != "method_proposal":
        raise ValueError("requires public_research method_proposal; private project inputs are not loaded")
    requirements = scenario.get("requirements", {})
    if not requirements.get("require_research_dossier") or requirements.get("min_sources", 0) < 2 or requirements.get("min_pdfs", 0) < 2:
        raise ValueError("delegated evaluation requires a dossier and at least two retrieved/read papers")
    return RunRequest(project=str(scenario["project"]), user_request=str(scenario["question"]),
                      extra={"run_id": root.name, "run_root": str(root), "scope": "method_proposal",
                             "idea_requirements": requirements, "idea_mode": "fast",
                             "context_sources": {"project_rules": False, "code_repositories": False}})


def public_config(config: AgentConfig) -> dict[str, Any]:
    # Only credential variable names are exposed; never provider instances or secret values.
    return {"name": config.name, "model_provider": config.model_provider, "model_name": config.model_name,
            "api_key_env": config.api_key_env, "thinking_enabled": config.thinking_enabled,
            "reasoning_effort": config.reasoning_effort, "max_tokens": config.max_tokens,
            "temperature": config.temperature, "tools": list(config.tools),
            "request_timeout_seconds": config.request_timeout_seconds, "max_retries": config.max_retries,
            "loop": dict(config.raw.get("loop", {}))}


def aggregate_traces(root: Path) -> dict[str, Any]:
    """Include every recorded child, including failed children and pending calls."""
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    usage: dict[str, int | float] = {}
    for facts_path in sorted(root.rglob("facts.json")):
        try:
            audit = audit_trace(facts_path.parent)
            facts = audit["facts"]
            row = {"trace": str(facts_path.parent.relative_to(root)), "consistent": audit["consistent"],
                   "status": facts["status"], "counts": facts["counts"], "usage": facts["usage"],
                   "usage_complete": facts["usage_complete"]}
            for key, value in facts["counts"].items():
                counts[key] = counts.get(key, 0) + int(value)
            for key, value in facts["usage"].items():
                if type(value) in {int, float}:
                    usage[key] = usage.get(key, 0) + value
            rows.append(row)
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            rows.append({"trace": str(facts_path.parent.relative_to(root)), "consistent": False,
                         "usage_complete": False, "error": str(exc)})
    return {"traces": rows, "counts": counts, "usage": usage,
            "trace_consistent": bool(rows) and all(row["consistent"] for row in rows),
            "usage_complete": bool(rows) and all(row["usage_complete"] for row in rows),
            "interpretation": "Totals include lead and all child traces, including failed delegations. Pending usage remains unknown."}


def research_findings(root: Path) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    checkpoint_observations: dict[str, list[dict[str, Any]]] = {}
    for checkpoint in sorted(root.glob("agent_traces/idea_research/*/checkpoint.json")):
        state = json.loads(checkpoint.read_text())
        history = state.get("history", [])
        observations.extend(history)
        checkpoint_observations[checkpoint.parent.name] = history
    inventory = evidence_inventory(observations)
    search_ids = {canonical_source(row["url"]) for row in inventory["papers"]}
    read_ids = {canonical_source(row["url"]) for row in inventory["reads"]
                if row.get("source_type") == "pdf" and row.get("visible_pages")}
    verified: set[str] = set()
    reports: list[dict[str, Any]] = []
    for report in sorted(root.glob("idea/research_delegations/*/report.md")):
        metadata = parse(report.read_text()).metadata
        errors = dossier_errors(metadata, checkpoint_observations.get(report.parent.name, []))
        if not errors:
            verified.update(canonical_source(source["url"]) for source in metadata["sources"]
                            if source["decision"] == "use")
        reports.append({"path": str(report.relative_to(root)), "provenance_valid": not errors,
                        "errors": errors, "metadata": metadata})
    return {"counts": {"unique_retrieved_publications": len(search_ids),
                        "unique_downloaded_contents": len(inventory["downloads"]),
                        "unique_pdf_publications_with_visible_pages": len(read_ids),
                        "unique_publications_with_verified_extractions": len(verified),
                        "read_windows": len(inventory["reads"])},
            "reports": reports, "inventory": inventory,
            "interpretation": "Visible pages are not proof of full-paper reading or correct understanding. Counts exclude separate tool preflight."}


def archive_report(root: Path, summary: dict[str, Any]) -> None:
    """Point to exact model outputs without synthesizing reasons or missing research."""
    artifacts = [str(p.relative_to(root)) for p in sorted((root / "idea").rglob("*"))
                 if p.is_file() and p.suffix in {".json", ".md"} and "downloads" not in p.parts]
    summary["research_artifacts"] = artifacts
    lines = ["# Real delegated Idea evaluation", "", str(summary["status"]), "",
             "This run uses IdeaAgent.run_loop directly (development bypass of Bridge). "
             "No approval, downstream delivery or PIMC simulation is performed.", "",
             "Human summary: " + str(summary.get("human_summary") or "No accepted proposal."), "",
             "All lead/child trace totals: `" + json.dumps(summary.get("counts", {})) + "`", "",
             "Scientific validation: false. Model review and structural acceptance are separate from measured gains.", "",
             "## Original research records", ""]
    findings = research_findings(root)
    summary["research_counts"] = findings["counts"]
    atomic_json(root / "research_findings.json", findings)
    lines += ["Research counts: `" + json.dumps(findings["counts"]) + "`", ""]
    if summary.get("continuation_source"):
        lines += ["Continuation of: " + str(summary["continuation_source"]), "",
                  "The totals above include inherited history once. Only these increments belong to this attempt:", "",
                  "New calls: `" + json.dumps(summary.get("attempt_counts", {})) + "`", "",
                  "New known usage: `" + json.dumps(summary.get("attempt_known_usage", {})) + "`", "",
                  "Earlier unknown usage remains unknown. Original source receipts and review records were not rewritten.", ""]
    if summary.get("delivery_root"):
        brief = Path(summary["delivery_root"]) / "research_brief.md"
        lines += ["[中文研究说明](" + str(brief.relative_to(root)) + ")", ""]
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    for report in findings["reports"]:
        metadata = report["metadata"]
        lines += ["", "Selection principles: " + "; ".join(metadata.get("selection_principles", [])), "",
                  "| Paper | Decision and reason | Original page / excerpt | Finding / transfer / limits |",
                  "|---|---|---|---|"]
        for source in metadata.get("sources", []):
            insights = [item for item in metadata.get("insights", []) if item["source_id"] == source["source_id"]]
            quote = "; ".join(str(item["page"]) + ": " + item["quote"] for item in insights)
            extracted = "; ".join(item["paper_finding"] + " / " + item["transfer_idea"] + " / " + "; ".join(item["limitations"]) for item in insights)
            lines.append("| " + " | ".join(cell(value) for value in (source["title"], source["decision"] + ": " + source["selection_reason"], quote, extracted)) + " |")
    lines += ["", "Proposal research links: `" + json.dumps(summary.get("research_links"), ensure_ascii=False) + "`", ""]
    lines.extend("- [" + path + "](" + path + ")" for path in artifacts)
    (root / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.max_seconds) or not 0 < args.max_seconds <= 1200:
        raise ValueError("max-seconds must be finite and in (0, 1200]")
    source_commit, source_tree = git_value("rev-parse", "HEAD"), git_value("rev-parse", "HEAD^{tree}")
    source = (load_continuation(args.resume_from, source_commit=source_commit, source_tree=source_tree)
              if getattr(args, "resume_from", None) else None)
    scenario_path = args.scenario or Path("configs/evaluation/idea_research_delegated_real.yaml")
    scenario = source.scenario if source else yaml.safe_load(scenario_path.read_text())
    if not isinstance(scenario, dict):
        raise ValueError("scenario must be an object")
    maximum_source_mib = source_limit(scenario)
    run_id = "idea_research_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
    root = (args.runs_root / run_id).resolve()
    request = evaluation_request(scenario, root)
    if source:
        request.extra["resume_invocation"] = source.checkpoint.parent.name
    selected_tools = tuple(scenario["tools"])
    if "idea.research_delegate" not in selected_tools or set(selected_tools) - LEAD_TOOLS:
        raise ValueError("lead must delegate research and may only additionally query memory")
    child_original = get_agent_config("idea_research")
    child_model = scenario["child_model"]
    if child_model.get("provider") != "deepseek" or child_model.get("thinking") is not False or child_model.get("reasoning_effort") is not None:
        raise ValueError("child must explicitly configure non-thinking DeepSeek")
    child_policy = AgentLoopPolicy.from_mapping(scenario["child_loop"])
    child_tools = tuple(scenario["child_tools"])
    child = replace(child_original, tools=child_tools, model_provider="deepseek", model_name=str(child_model["name"]),
                    api_key_env="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com/v1", base_url_env="",
                    thinking_enabled=False, reasoning_effort=None, max_tokens=int(child_model["max_tokens"]),
                    temperature=float(child_model["temperature"]), max_retries=int(child_model["max_retries"]),
                    request_timeout_seconds=float(child_model["timeout_seconds"]),
                    raw={**child_original.raw, "loop": asdict(child_policy)})
    request.runtime["idea_research_config"] = child
    if not child.tools or set(child.tools) - CHILD_TOOLS:
        raise ValueError("research child must use only audited public research tools")
    model = scenario["model"]
    if model.get("provider") != "deepseek" or model.get("thinking") is not False or model.get("reasoning_effort") is not None:
        raise ValueError("scenario must explicitly use non-thinking DeepSeek without inherited reasoning effort")
    policy = AgentLoopPolicy.from_mapping(scenario["loop"])
    original = get_agent_config("idea")
    config = replace(original, model_provider="deepseek", model_name=str(model["name"]),
                     api_key_env="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com/v1", base_url_env="",
                     max_tokens=int(model["max_tokens"]), temperature=float(model["temperature"]),
                     thinking_enabled=False, reasoning_effort=None, max_retries=int(model["max_retries"]),
                     request_timeout_seconds=float(model["timeout_seconds"]), debate_enabled=False,
                     tools=selected_tools, raw={**original.raw, "loop": asdict(policy),
                                               "research": scenario["research"]})
    if source or not args.prepare_only:
        if git_value("status", "--porcelain", "--", "backend/app", "configs", "scripts"):
            raise RuntimeError("commit source/config/script changes before real evaluation or continuation preparation")
    if not args.prepare_only:
        if not env_or_local("DEEPSEEK_API_KEY") or not env_or_local(child.api_key_env):
            raise RuntimeError("real lead/child credential missing; no request made")
    os.environ["MARS_SOURCE_MAX_MIB"] = str(maximum_source_mib)
    os.environ["MARS_ENABLE_NETWORK_TOOLS"] = "true"
    os.environ["MARS_WEB_SEARCH_ALLOWLIST"] = ",".join(scenario["domains"])
    os.environ["MARS_MEMORY_PROFILE"] = "research"
    os.environ["MARS_KNOWLEDGE_ROOT"] = str(root / "memory")
    os.environ["MARS_MOCK_MODE"] = "never"
    reset_settings_cache()
    from app.harness.kb.stores import reset_for_tests as reset_stores
    agent = IdeaAgent(agent_config=config)
    context = await agent.build_context(request)
    messages = agent._messages_for_context(request, context, purpose="live_preflight")
    continuation = None
    if source:
        check_configuration(source, lead=public_config(config), child=public_config(child), messages=messages)
        continuation = fork_continuation(source, root)
    else:
        root.mkdir(parents=True, exist_ok=False)
    reset_stores(root / "memory")
    attempt_seconds = min(args.max_seconds, source.remaining_seconds) if source else args.max_seconds
    cumulative_seconds = float(continuation["inherited_duration_seconds"]) if continuation else 0.0
    atomic_json(root / "input" / "request.json", {
        "run_id": run_id, "scenario": scenario, "source_commit": source_commit,
        "source_tree": source_tree, "source_dirty": bool(git_value("status", "--porcelain")),
        "lead_config": public_config(config), "child_config": public_config(child),
        "resource_limits": {"source_max_mib": maximum_source_mib,
                            "max_seconds": source.initial["resource_limits"]["max_seconds"] if source else args.max_seconds},
        "continuation": continuation is not None,
        "credential_persisted": False, "development_bypass_bridge": True,
        "context_sources": context.metadata.get("context_sources"),
        "messages": [asdict(m) for m in messages]})
    summary: dict[str, Any] = {"run_id": run_id, "run_root": str(root), "status": "prepared",
                               "schema_valid": False, "material_ready": False, "model_review_passed": False,
                               "scientific_validated": False, "simulation_executed": False,
                               "downstream_delivered": False, "development_bypass_bridge": True, "preflight_results_supplied_to_model": False}
    if continuation:
        summary.update(continuation_source=str(source.root) if source else None,
                       inherited_counts=continuation["inherited_counts"],
                       inherited_known_usage=continuation["inherited_usage"],
                       budgets_reset=False, attempt_max_seconds=attempt_seconds,
                       cumulative_duration_seconds=cumulative_seconds,
                       preparation_note="Only source/configuration/byte-copy checks completed; no resumed model call or quality acceptance.")
        (root / "continuation_preparation.md").write_text(
            "# Continuation preparation\n\n"
            f"Source run: {continuation['source_run_root']}\n\n"
            "The source archive was copied and verified by file hashes. Original absolute read receipts remain unchanged. "
            "No model call was made during preparation; this does not establish successful execution or research quality.\n\n"
            f"Remaining total runtime budget: {attempt_seconds:.3f} seconds for this attempt. "
            "Model/tool counters, evidence, candidate and unknown usage are inherited without reset.\n\n"
            "The current outcome is recorded in summary.json. Earlier copied reports describe the original run.\n",
            encoding="utf-8")
    atomic_json(root / "summary.json", summary)
    logger.info("LIVE_RUN_ROOT={}", root)
    if args.prepare_only:
        return 0
    started = time.monotonic()
    try:
        artifact = await asyncio.wait_for(agent.run_loop(request, context), timeout=attempt_seconds)
        target = root / "idea" / "idea_proposal.v1.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.text, encoding="utf-8")
        summary.update(status="accepted_method_proposal", schema_valid=validate_document(artifact.text, expected_schema="proposal.v1").valid,
                       material_ready=True, proposal_path=str(target), proposal_sha256=digest(artifact.text),
                       human_summary=artifact.metadata.get("human_summary"), handoff=artifact.metadata.get("handoff"),
                       research_links=artifact.metadata.get("research_links"),
                       research_assessment=artifact.metadata.get("research_assessment"),
                       delivery_root=context.metadata.get("idea_delivery_root"),
                       model_review_passed=bool(context.metadata.get("reflection_accepted")))
    except (Exception, asyncio.CancelledError) as exc:
        summary.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:2000])
        logger.error("Real delegated run stopped: {}", type(exc).__name__)
    finally:
        summary["duration_seconds"] = time.monotonic() - started
        summary["cumulative_duration_seconds"] = cumulative_seconds + summary["duration_seconds"]
        aggregate = aggregate_traces(root)
        summary.update(aggregate)
        inherited_counts = continuation["inherited_counts"] if continuation else {}
        inherited_usage = continuation["inherited_usage"] if continuation else {}
        summary["attempt_counts"] = {key: value - inherited_counts.get(key, 0) for key, value in aggregate["counts"].items()}
        summary["attempt_known_usage"] = {key: value - inherited_usage.get(key, 0) for key, value in aggregate["usage"].items()}
        summary["accounting_note"] = "counts/usage include inherited history once; attempt_counts/attempt_known_usage contain only this invocation's increments. Unknown earlier usage stays unknown."
        atomic_json(root / "all_traces_audit.json", aggregate)
        archive_report(root, summary)
        atomic_json(root / "summary.json", summary)
        logger.info("LIVE_RESULT {}", json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "accepted_method_proposal" and summary["trace_consistent"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--scenario", type=Path)
    source_group.add_argument("--resume-from", type=Path,
                              help="Fork a same-source interrupted/model-error run; preserve old artifacts and remaining budgets")
    parser.add_argument("--runs-root", type=Path, default=Path("runs/real_idea_research"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=1200)
    try:
        return asyncio.run(run(parser.parse_args()))
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        logger.error("preflight failed: {}", str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
