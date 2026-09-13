"""Run only the configured Idea agent through Bridge, without a web server."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Any

from loguru import logger

from app.agents.idea.runtime_profile import resolve_idea_profile
from app.agents.idea.service_agent import ServiceIdeaAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.idea_input_context import IdeaRequirements, validate_idea_context
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.agent_loop.trace import atomic_json
from app.harness.llm.model_registry import get_agent_config, provider_configured_for_agent
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.settings import get_settings, repo_root
from app.storage.artifact_store import ArtifactStore
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore
from scripts.watch_agent_trace import monitor, progress_snapshot


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project", required=True)
    result.add_argument("--request-file", type=Path, required=True, help="UTF-8 research request")
    result.add_argument("--task", default="idea_standalone")
    result.add_argument("--scope", choices=("project_proposal", "method_proposal"), default="project_proposal")
    result.add_argument("--context-json", type=Path, help="Labeled background/baseline_code/data_description/etc.")
    result.add_argument("--requirements-json", type=Path, help="Explicit IdeaRequirements overrides")
    result.add_argument("--runs-root", type=Path, default=repo_root() / "runs")
    result.add_argument("--max-seconds", type=float, default=1500)
    result.add_argument("--prepare-only", action="store_true", help="Archive inputs only; no model or tool execution")
    return result


def build_request(args: argparse.Namespace) -> RunRequest:
    if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        raise ValueError("max-seconds must be positive and finite")
    project = (repo_root() / "projects" / args.project).resolve()
    if not project.is_relative_to((repo_root() / "projects").resolve()) or not project.is_dir():
        raise ValueError("project must name an existing project directory")
    text = args.request_file.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("request-file must contain a nonempty research request")
    extra: dict[str, Any] = {"scope": args.scope}
    if args.context_json:
        extra["idea_context"] = validate_idea_context(json.loads(args.context_json.read_text(encoding="utf-8")))
    if args.requirements_json:
        requirements = IdeaRequirements.model_validate_json(args.requirements_json.read_text(encoding="utf-8"))
        extra["idea_requirements"] = requirements.model_dump(exclude_none=True)
    return RunRequest(task=args.task, project=args.project, entrypoint="idea", standalone=True,
                      auto_approve=False, user_request=text, extra=extra)


def source_identity() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo_root()), *args], text=True).strip()
    return {"source_commit": git("rev-parse", "HEAD"),
            "tracked_changes": git("diff", "HEAD", "--name-only").splitlines(),
            "untracked_files": git("ls-files", "--others", "--exclude-standard").splitlines(),
            "source_dirty": bool(git("status", "--porcelain"))}


def failure_diagnostic(root: Path) -> dict[str, Any] | None:
    path = root / "events" / "agent_events.jsonl"
    if not path.is_file():
        return None
    diagnostic = None
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") == "agent.node_failed" and isinstance(event.get("diagnostic"), dict):
            diagnostic = event["diagnostic"]
    return diagnostic


async def run(args: argparse.Namespace) -> int:
    request = build_request(args)
    profile = resolve_idea_profile(get_settings().mars_idea_runtime_profile)
    agent = ServiceIdeaAgent(profile=profile)
    registry = AgentRegistry()
    registry.register("idea", agent)
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(args.runs_root.resolve()), registry=registry, bus=bus)
    session = orch.create_session(request)
    root = session.run.root
    summary: dict[str, Any] = {
        "run_id": session.run.run_id, "run_root": str(root), "status": "prepared",
        "entrypoint": "idea", "standalone": True, "auto_approved": False,
        "human_approved": False, "simulation_executed": False, "proposal_ready": False,
        "runtime_profile": get_settings().mars_idea_runtime_profile, **source_identity(),
    }
    target = root / "idea" / "standalone_summary.json"
    atomic_json(target, summary)
    logger.info("IDEA_RUN_ROOT={}", root)
    if args.prepare_only:
        logger.info("Prepared inputs only; no model or tool request performed. Summary: {}", target)
        return 0

    configs = (agent.config, profile.child if profile else get_agent_config("idea_research"))
    missing = [cfg.name for cfg in configs if not cfg.enabled or not provider_configured_for_agent(cfg)]
    if missing:
        summary.update(status="configuration_error", missing_agent_configuration=missing)
        atomic_json(target, summary)
        session.graph.transition("idea", NodeState.FAILED)
        state_store = RunStateStore(session.run)
        snapshot = state_store.load()
        if snapshot is not None:
            state_store.write(graph=session.graph, request=snapshot.request, status="failed")
        session.run.write_event("agent_events", {"event": "idea.configuration_error", "agent": "idea",
                                                 "missing_agent_configuration": missing})
        logger.error("Real model configuration missing for {}; no model request made", ", ".join(missing))
        await bus.close()
        return 2

    started = time.monotonic()
    result = orch.start_owned_run(session.run.run_id)
    if not result.get("ok"):
        raise RuntimeError("fresh standalone run could not start")
    progress = asyncio.create_task(monitor(root))
    stop_reason = "standalone_host_shutdown"
    try:
        while True:
            state = session.graph.state("idea")
            if state == NodeState.WAITING_REVIEW:
                # The existing stop operation preserves review state and material;
                # it ends only this process's HITL wait, without approving anything.
                stop_reason = "standalone_review_ready"
                summary["status"] = "waiting_review"
                break
            task = orch.owned_tasks.active(session.run.run_id)
            if task is None or task.done():
                if task is not None:
                    await task
                summary["status"] = "failed"
                break
            if time.monotonic() - started >= args.max_seconds:
                stop_reason = "standalone_time_limit"
                summary["status"] = "timed_out"
                break
            await asyncio.sleep(0.2)
    except (asyncio.CancelledError, KeyboardInterrupt):
        stop_reason = "standalone_interrupted"
        summary["status"] = "interrupted"
        raise
    finally:
        stopped = await orch.stop_owned_run(session.run.run_id, reason=stop_reason)
        progress.cancel()
        with suppress(asyncio.CancelledError):
            await progress
        await bus.close()
        ref = ArtifactStore(session.run).latest(agent_dir="idea", stem="idea_proposal")
        summary.update(elapsed_seconds=round(time.monotonic() - started, 2),
                       states={key: value.value for key, value in session.graph.all_states().items()},
                       progress=progress_snapshot(root), diagnostic=failure_diagnostic(root))
        if stopped.get("status") == "stop_incomplete":
            summary["status"] = "stop_incomplete"
        if ref is not None:
            summary["proposal_path"] = str(ref.path)
            summary["proposal_ready"] = summary["status"] == "waiting_review"
        if summary["status"] == "waiting_review" and ref is None:
            summary["status"] = "failed"
        atomic_json(target, summary)
    logger.info("IDEA_RESULT {}", json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "waiting_review" else 1


def main() -> None:
    args = parser().parse_args()
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        # Provider internals may contain response bodies; diagnostics live in
        # the run archive. Never render arbitrary exception text here.
        logger.error("Standalone Idea stopped: {}. Check inputs/configuration and the run summary.", type(exc).__name__)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
