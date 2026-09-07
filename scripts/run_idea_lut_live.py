"""Headless real IdeaAgent evaluation. Requires explicit real API credentials."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import math
import os
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
from app.harness.agent_loop.review import ExternalReview
from app.harness.llm.model_registry import get_agent_config
from app.harness.schema.validator import validate_document
from app.settings import reset_settings_cache, env_or_local
from scripts.idea_live_resume import exclusive_run, load_resume, record_resumption, resume_scenario
from scripts.watch_agent_trace import monitor


PUBLIC_RESEARCH_TOOLS = (
    "knowledge.kb_query", "search.arxiv_search", "search.web_search", "search.fetch_sources",
)


def evaluation_request(scenario: dict[str, Any], root: Path) -> RunRequest:
    """Method-only evaluation never imports project rules, linked repos or uploads.

    The supplied question remains caller-owned input. The CLI selects a fresh KB
    per run; only its actual public-source retrievals can populate that memory.
    Old runs with a different data scope cannot silently acquire new inputs.
    """
    if scenario.get("data_scope") != "public_research" or scenario.get("scope") != "method_proposal":
        raise ValueError("this CLI requires a public_research method_proposal scenario; "
                         "legacy inputs need a new evaluation, not a changed resume")
    return RunRequest(project=scenario["project"], user_request=scenario["question"],
                      extra={"run_id": root.name, "run_root": str(root), "scope": scenario["scope"],
                             "idea_requirements": scenario["requirements"],
                             "context_sources": {"project_rules": False, "code_repositories": False}})


def git_value(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


async def run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        raise ValueError("max-seconds must be positive and finite")
    if args.review_file and not args.resume_run:
        raise ValueError("review-file requires resume-run")
    if args.resume_run:
        if args.prepare_only:
            raise ValueError("prepare-only cannot be combined with resume-run")
        root = args.resume_run.resolve()
    else:
        run_id = "idea_lut_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
        root = (args.runs_root / run_id).resolve()
        root.mkdir(parents=True, exist_ok=False)
    with exclusive_run(root):
        return await _run(args, root)


async def _run(args: argparse.Namespace, root: Path) -> int:
    review = ExternalReview.from_mapping(json.loads(args.review_file.read_text())) if args.review_file else None
    prior_summary: dict[str, Any] = {}
    checkpoint: Path | None = None
    initial: dict[str, Any] = {}
    if args.resume_run:
        initial, prior_summary, checkpoint, _ = load_resume(root, review=review)
        scenario = resume_scenario(initial)
        if args.mode and args.mode != initial["loop_policy"]["mode"]:
            raise ValueError("resume cannot change the original loop mode")
    else:
        scenario = yaml.safe_load(args.scenario.read_text())
    if not isinstance(scenario, dict):
        raise ValueError("scenario must be an object")
    request = evaluation_request(scenario, root)
    model = scenario["model"]
    provider_name = str(model.get("provider", "zhipu"))
    if provider_name not in {"deepseek", "zhipu"}:
        raise ValueError("evaluation provider must be deepseek or zhipu")
    key_env = "DEEPSEEK_API_KEY" if provider_name == "deepseek" else "ZHIPU_API_KEY"
    base_url = "https://api.deepseek.com/v1" if provider_name == "deepseek" else "https://open.bigmodel.cn/api/paas/v4"
    selected_tools = tuple(scenario.get("tools", PUBLIC_RESEARCH_TOOLS))
    if not selected_tools or set(selected_tools) - set(PUBLIC_RESEARCH_TOOLS):
        raise ValueError("evaluation tools must be a nonempty subset of public research tools")
    if args.prompt_key:
        key = getpass.getpass(f"{provider_name} API key (hidden, not saved): ")
        if not key:
            raise ValueError("empty API key")
        os.environ[key_env] = key
    if not args.prepare_only and not env_or_local(key_env):
        raise RuntimeError(f"{key_env} is missing; no request made")
    runtime_changes = git_value("diff", "HEAD", "--name-only", "--", "backend/app", "configs", "scripts/run_idea_lut_live.py")
    if runtime_changes and not args.prepare_only:
        raise RuntimeError("commit runtime/config changes before a live evaluation so the source is reproducible")
    run_id = root.name
    os.environ["MARS_ENABLE_NETWORK_TOOLS"] = "true"
    os.environ["MARS_WEB_SEARCH_PROVIDER"] = "zhipu"
    os.environ["MARS_WEB_SEARCH_ALLOWLIST"] = ",".join(scenario["domains"])
    os.environ["MARS_MEMORY_PROFILE"] = "research"
    os.environ["MARS_KNOWLEDGE_ROOT"] = str(root / "memory")
    os.environ["MARS_MOCK_MODE"] = "never"
    reset_settings_cache()
    from app.harness.kb.stores import reset_for_tests as reset_stores
    reset_stores(root / "memory")
    model = scenario["model"]
    loop_raw = dict(initial["loop_policy"] if checkpoint else scenario["loop"])
    if args.mode:
        loop_raw["mode"] = args.mode
    policy = AgentLoopPolicy.from_mapping(loop_raw)
    original = get_agent_config("idea")
    config = replace(original, model_provider=provider_name, model_name=str(model["name"]),
                     api_key_env=key_env, base_url=base_url, base_url_env="",
                     max_tokens=int(model["max_tokens"]), temperature=float(model["temperature"]),
                     thinking_enabled=bool(model.get("thinking", True)), reasoning_effort=model.get("reasoning_effort"),
                     request_timeout_seconds=float(model["timeout_seconds"]), max_retries=int(model["max_retries"]),
                     debate_enabled=False, tools=selected_tools,
                     raw={**original.raw, "loop": asdict(policy)})
    agent = IdeaAgent(agent_config=config)
    if checkpoint:
        request.extra["resume_invocation"] = checkpoint.parent.name
    if review:
        request.extra["external_review"] = asdict(review)
    context = await agent.build_context(request)
    messages = agent._messages_for_context(request, context, purpose="live_preflight")
    source = {"source_commit": git_value("rev-parse", "HEAD"),
               "source_tree": git_value("rev-parse", "HEAD^{tree}"),
               "source_dirty": bool(git_value("status", "--porcelain"))}
    journal: Path | None = None
    if checkpoint:
        if [asdict(m) for m in messages] != initial["messages"]:
            raise ValueError("resume prompt/context differs from the original input; start a new evaluation")
        journal = record_resumption(root, checkpoint, source, review=review)
    else:
        initial = {"run_id": run_id, **source,
               "scenario": scenario, "loop_policy": asdict(policy),
               "messages": [asdict(m) for m in messages],
               "scope": scenario["scope"], "credential_persisted": False,
               "data_scope": {"profile": "public_research", "tools": list(config.tools),
                              "context_sources": context.metadata["context_sources"],
                              "memory": "isolated_run_directory", "upstream_artifacts": []}}
        atomic_json(root / "input" / "request.json", initial)
    summary: dict[str, Any] = {"run_id": run_id, "run_root": str(root), "status": "prepared",
                               "source_commit": initial["source_commit"], "source_dirty": initial["source_dirty"],
                               "schema_valid": False, "material_ready": False, "scientific_validated": False,
                               "project_ready": False, "simulation_executed": False}
    summary["source_resumptions"] = [*prior_summary.get("source_resumptions", [])]
    if journal:
        summary["source_resumptions"].append({**source, "journal": str(journal.relative_to(root))})
    atomic_json(root / "summary.json", summary)
    logger.info("LIVE_RUN_ROOT={}", root)
    if args.prepare_only:
        logger.info("prepared only; no model or tool request performed")
        return 0
    started = time.monotonic()
    progress_task = asyncio.create_task(monitor(root))
    try:
        artifact = await asyncio.wait_for(agent.run_loop(request, context), timeout=args.max_seconds)
        target = root / "idea" / "idea_proposal.v1.md"
        version = 1
        while target.exists():
            version += 1
            target = root / "idea" / f"idea_proposal.v{version}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.text, encoding="utf-8")
        summary.update(status="passed_method_proposal",
                       schema_valid=validate_document(artifact.text, expected_schema="proposal.v1").valid,
                       material_ready=True, proposal_path=str(target), proposal_sha256=digest(artifact.text))
    except (asyncio.CancelledError, KeyboardInterrupt):
        summary.update(status="interrupted", error_type="Cancelled", error="run interrupted; pending request usage may be unknown")
    except Exception as exc:
        summary.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
        logger.error("real Idea run failed: {}", type(exc).__name__)
    finally:
        progress_task.cancel()
        with suppress(asyncio.CancelledError):
            await progress_task
        summary["attempt_duration_seconds"] = time.monotonic() - started
        summary["duration_seconds"] = float(prior_summary.get("duration_seconds", 0)) + summary["attempt_duration_seconds"]
        summary["trace_root"] = context.metadata.get("loop_trace_root")
        trace_root = Path(str(summary["trace_root"])) if summary["trace_root"] else None
        if trace_root and (trace_root / "facts.json").exists():
            audit = audit_trace(trace_root)
            atomic_json(root / "audit.json", audit)
            summary["trace_consistent"] = audit["consistent"]
            summary["counts"] = audit["facts"]["counts"]
            summary["usage"] = audit["facts"]["usage"]
            summary["usage_complete"] = audit["facts"]["usage_complete"]
            summary["provider_error"] = audit.get("provider_error")
            checkpoint = trace_root / "checkpoint.json"
            if checkpoint.exists():
                state = json.loads(checkpoint.read_text())
                summary["reflection_accepted"] = state["reflection_accepted"]
                summary["loop_status"] = state["status"]
        atomic_json(root / "summary.json", summary)
        if journal:
            atomic_json(journal / "result.json", summary)
        logger.info("LIVE_RESULT {}", json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "passed_method_proposal" and summary.get("trace_consistent") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("configs/evaluation/idea_2d_lut_real.yaml"))
    parser.add_argument("--runs-root", type=Path, default=Path("runs/real_idea_evaluation"))
    parser.add_argument("--resume-run", type=Path,
                        help="resume an interrupted/model-error run with original inputs and cumulative budgets")
    parser.add_argument("--review-file", type=Path,
                        help="apply explicit candidate-bound reviewer issues without resetting invocation budgets")
    parser.add_argument("--mode", choices=["react", "reflection"])
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=1200.0,
                        help="overall live evaluation deadline; no unlimited retry")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (RuntimeError, ValueError) as exc:
        logger.error("preflight failed: {}", str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
