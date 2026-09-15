"""Terminal entry point: doctor, research, resume and status; no web server needed."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

from loguru import logger

from app.bridge.cli_research_service import CliResearchService, configuration, initialize, prepare_protocol
from app.cli_composition import CliAgents
from app.execution.research_process import run_worker
from app.harness.agent_loop.trace import atomic_json
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Message
from app.harness.research_trial import ResearchBudget, read_record
from app.settings import env_or_local, set_runtime_env


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="mars", description="Run a bounded, evidence-backed research loop on a local static PIMC checkout")
    commands = result.add_subparsers(dest="command", required=True)
    cfg = configuration()
    for name in ("doctor", "research"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--data", type=Path, required=True, help="Real static .pth capture with x/y/nf")
        command.add_argument("--model", choices=("deepseek-v4-flash", "deepseek-v4-pro"), default="deepseek-v4-flash")
        if name == "doctor":
            command.add_argument("--check-model", action="store_true", help="Make one real small API request")
        else:
            command.add_argument("--task", default="在静态 PIMC 上减少至少20%的实参数量，RES性能不下降。完成调研、假设、代码修改、实验、分析与迭代。")
            command.add_argument("--output", type=Path)
            command.add_argument("--reduction", type=float, default=cfg["reduction"])
            command.add_argument("--max-degradation-db", type=float, default=cfg["max_degradation_db"])
            command.add_argument("--rounds", type=int, default=cfg["rounds"])
            command.add_argument("--max-steps", type=int, default=cfg["max_steps"])
            command.add_argument("--timeout-seconds", type=int, default=cfg["timeout_seconds"])
            command.add_argument("--prepare-only", action="store_true", help="Research, code and model preflight; performs no training or test metrics")
    for name in ("resume", "status"):
        command = commands.add_parser(name)
        command.add_argument("run", type=Path)
    return result


async def doctor(repo: Path, data: Path, model: str, check_model: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"repo": str(repo), "data": str(data), "dataset_present": data.is_file(),
        "key_configured": bool(env_or_local("DEEPSEEK_API_KEY")), "python": sys.version.split()[0],
        "missing_dependencies": [name for name in ("torch", "scipy", "matplotlib", "tensorboard") if importlib.util.find_spec(name) is None]}
    if not result["missing_dependencies"]:
        try:
            with tempfile.TemporaryDirectory(prefix="mars-doctor-") as temporary:
                root = Path(temporary)
                protocol = prepare_protocol(repo, data, ResearchBudget())
                atomic_json(root / "protocol.json", protocol)
                result["baseline_preflight"] = await run_worker(
                    {"repo": str(repo), "protocol": str(root / "protocol.json"), "operation": "preflight"}, root / "preflight", 120)
                result["baseline_preflight"].pop("output", None)
        except Exception as exc:
            result["baseline_preflight"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if check_model:
        try:
            provider, cfg = select_provider(replace(get_agent_config("coding"), model_name=model,
                max_tokens=32, thinking_enabled=False, max_retries=0, request_timeout_seconds=45))
            try:
                completion = await provider.complete([Message(role="user", content="Reply with READY only.")], cfg)
                result["model_check"] = {"provider": completion.provider, "model": completion.model, "responded": bool(completion.text.strip())}
            finally:
                await provider.close()
        except Exception as exc:
            # Providers own redaction; never echo credentials or HTTP request headers.
            result["model_check"] = {"responded": False, "error_type": type(exc).__name__}
    result["ready"] = (result["dataset_present"] and result["key_configured"] and not result["missing_dependencies"]
                       and result.get("baseline_preflight", {}).get("status") == "completed"
                       and (not check_model or result.get("model_check", {}).get("responded", False)))
    return result


async def dispatch(options: argparse.Namespace) -> dict[str, Any]:
    if options.command == "status":
        root = options.run.expanduser().resolve()
        state = read_record(root / "state.json")
        return {"status": state["status"], "error": state.get("error"), "report": str(root / "report.md"),
                "trials": {k: v["status"] for k, v in state["trials"].items()}, "final_comparison": state.get("final_comparison")}
    if options.command == "doctor":
        return await doctor(options.repo.expanduser().resolve(), options.data.expanduser().resolve(), options.model, options.check_model)
    if options.command == "research":
        repo, data = options.repo.expanduser().resolve(), options.data.expanduser().resolve()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        root = (options.output or repo / ".mars/runs" / stamp).expanduser().resolve()
        budget = ResearchBudget(reduction=options.reduction, max_degradation_db=options.max_degradation_db,
            rounds=options.rounds, max_steps=options.max_steps, timeout_seconds=options.timeout_seconds)
        initialize(repo, data, root, options.task, options.model, budget)
    else:
        root = options.run.expanduser().resolve()
    manifest = read_record(root / "input/manifest.json")
    # This research command authorizes public literature retrieval. Respect a configured domain scope.
    if not env_or_local("MARS_WEB_SEARCH_ALLOWLIST"):
        set_runtime_env({"MARS_WEB_SEARCH_ALLOWLIST": ",".join(manifest["configuration"]["source_domains"])})
    set_runtime_env({"MARS_ENABLE_NETWORK_TOOLS": "true"})
    service = CliResearchService(CliAgents(manifest["model"], manifest["configuration"]["coding_loop"]))
    state = await service.run(root, prepare_only=bool(getattr(options, "prepare_only", False)))
    return {"status": state["status"], "error": state.get("error"), "run": str(root), "report": str(root / "report.md"),
            "final_comparison": state.get("final_comparison")}


def main() -> int:
    options = parser().parse_args()
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")
    try:
        result = asyncio.run(dispatch(options))
    except KeyboardInterrupt:
        logger.warning("Interrupted; inspect mars status and use mars resume to continue")
        return 130
    except Exception as exc:
        logger.error("{}: {}", type(exc).__name__, exc)
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if options.command == "doctor":
        return 0 if result["ready"] else 2
    return 2 if result.get("status") in {"blocked_data", "failed", "interrupted"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
