"""Shared backend controls, project contracts, and legacy PIMC CLI entrypoints."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Literal, NoReturn

from loguru import logger
from pydantic import Field
import yaml

from app.bridge.cli_research_service import CliResearchService, configuration, initialize, prepare_protocol
from app.bridge.research_contract_service import (
    FrozenResearchTask, ProjectPreflight, contract_sha256, default_research_budget,
    freeze_research_task, load_project_contract, preflight_project, validate_frozen_research_task,
)
from app.cli_runtime_client import RuntimeClient, RuntimeClientError, RuntimeResponse
from app.harness.runtime.research_contract import ContractModel
from app.harness.runtime.research_contract import ResearchBudget as ProjectResearchBudget
from app.cli_composition import CliAgents
from app.execution.research_process import run_worker
from app.harness.agent_loop.trace import atomic_json
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Message
from app.harness.research_trial import ResearchBudget, read_record
from app.settings import env_or_local, repo_root, set_runtime_env


class RuntimeClientLimits(ContractModel):
    schema_id: Literal["cli_runtime.v1"]
    timeout_seconds: float = Field(strict=True, gt=0, allow_inf_nan=False)
    max_response_bytes: int = Field(strict=True, gt=0)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default diagnostic can echo credentials pasted into an
        # unsupported option or choice. Usage contains only declared options.
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid command arguments; use --help for supported options.\n")


def _runtime_client(server: str) -> RuntimeClient:
    limits = RuntimeClientLimits.model_validate(yaml.safe_load(
        (repo_root() / "configs/cli_runtime.yaml").read_text(encoding="utf-8")))
    return RuntimeClient(server, timeout_seconds=limits.timeout_seconds,
                         max_response_bytes=limits.max_response_bytes)


def _runtime_response(response: RuntimeResponse) -> dict[str, Any]:
    # Do not collapse HTTP acceptance, run status and scientific outcome.
    return {"http_status": response.status_code, "http_ok": response.ok, "response": response.payload}



def _creation_protocol_error() -> NoReturn:
    raise RuntimeClientError("creation_response_mismatch",
        "Backend creation evidence does not match this request; inspect the saved request before continuing")


def _creation_response(response: RuntimeResponse, *, request_id: str | None,
                       expected_task_sha256: str | None, lookup: bool,
                       expected_name: str | None = None) -> dict[str, Any]:
    """Keep transport facts; only explicit, correlated evidence confirms a save."""
    result = {**_runtime_response(response), "creation_confirmed": False, "contract_match": None}

    def check_hash(value: Any, *, unknown: bool = False) -> str | None:
        if value is None and unknown:
            return None
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            _creation_protocol_error()
        if expected_task_sha256 is not None and value != expected_task_sha256:
            _creation_protocol_error()
        return value

    def check_run(value: Any) -> str:
        if (not isinstance(value, dict) or value.get("status") != "created"
                or value.get("research_started") is not False or value.get("request_id") != request_id
                or value.get("idempotent") is not (request_id is not None)
                or value.get("entrypoint") != "pipeline"
                or not isinstance(value.get("run_id"), str)
                or re.fullmatch(r"[A-Za-z0-9_.-]+", value["run_id"]) is None or value["run_id"] in {".", ".."}
                or not isinstance(value.get("task"), str) or not value["task"].strip()
                or expected_name is not None and value["task"] != expected_name
                or not isinstance(value.get("project"), str) or not value["project"]
                or not isinstance(value.get("created_at"), str) or not value["created_at"]
                or not isinstance(value.get("execution_admission"), dict)
                or type(value["execution_admission"].get("ready")) is not bool):
            _creation_protocol_error()
        fingerprint = check_hash(value.get("task_sha256"))
        assert fingerprint is not None
        return fingerprint

    if not lookup and response.status_code == 201:
        check_run(response.payload)
        result.update(creation_confirmed=True, contract_match=expected_task_sha256 is not None)
        return result
    value = response.payload
    if not response.ok:
        detail = value.get("detail") if isinstance(value, dict) else None
        if not isinstance(detail, dict) or detail.get("status") not in {"unknown", "rejected"}:
            return result
        value = detail
    elif response.status_code != (200 if lookup else 202):
        _creation_protocol_error()
    if (not isinstance(value, dict) or request_id is None or value.get("request_id") != request_id
            or value.get("research_started") is not False
            or value.get("status") not in {"pending", "created", "unknown", "rejected"}):
        _creation_protocol_error()
    status = value["status"]
    expected_admission = True if status in {"pending", "created"} else False if status == "rejected" else None
    if value.get("admitted") is not expected_admission:
        _creation_protocol_error()
    fingerprint = check_hash(value.get("task_sha256"), unknown=status == "unknown")
    if expected_task_sha256 is not None and fingerprint is not None:
        result["contract_match"] = True
    if status == "created":
        if not lookup or check_run(value.get("run")) != fingerprint or value.get("run_id") != value["run"]["run_id"]:
            _creation_protocol_error()
        result["creation_confirmed"] = True
    elif (value.get("run") is not None or status == "rejected" and value.get("run_id") is not None
          or not lookup and response.status_code == 202 and status != "pending"):
        _creation_protocol_error()
    return result

def _export_frozen(frozen: FrozenResearchTask, output_path: Path) -> dict[str, Any]:
    if contract_sha256(frozen.task) != frozen.task_sha256:
        raise ValueError("Frozen task fingerprint does not match its content")
    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(frozen.model_dump(mode="json"), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"status": "prepared", "task_sha256": frozen.task_sha256, "output": str(output),
            "research_started": False}


async def _remote_project(options: argparse.Namespace) -> dict[str, Any]:
    async with _runtime_client(options.server) as client:
        if options.project_command == "defaults":
            response = await client.defaults()
            return (ProjectResearchBudget.model_validate(response.payload).model_dump(mode="json")
                    if response.ok else _runtime_response(response))
        if options.project_command == "create":
            frozen = validate_frozen_research_task(json.loads(options.contract.read_text(encoding="utf-8")))
            response = await client.create_research_run(name=options.name, contract=frozen.model_dump(mode="json"),
                                                        request_id=options.request_id)
            return _creation_response(response, request_id=options.request_id, expected_task_sha256=frozen.task_sha256,
                                      lookup=False, expected_name=options.name.strip())
        if options.project_command == "request-status":
            expected = options.task_sha256
            if expected is not None and re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise RuntimeClientError("invalid_request", "Expected task fingerprint must be a SHA-256 digest")
            response = await client.creation_status(options.request_id)
            return _creation_response(response, request_id=options.request_id, expected_task_sha256=expected, lookup=True)
        project = load_project_contract(options.config.expanduser().resolve())
        if options.project_command == "preflight":
            response = await client.preflight(project.model_dump(mode="json"))
            return (ProjectPreflight.model_validate(response.payload).model_dump(mode="json")
                    if response.ok else _runtime_response(response))
        if options.budget:
            budget = ProjectResearchBudget.model_validate(yaml.safe_load(options.budget.read_text(encoding="utf-8")))
        else:
            response = await client.defaults()
            if not response.ok:
                return _runtime_response(response)
            budget = ProjectResearchBudget.model_validate(response.payload)
        response = await client.prepare(project=project.model_dump(mode="json"), goal=options.goal,
                                        mode=options.mode, budget=budget.model_dump(mode="json"))
        if not response.ok:
            return _runtime_response(response)
        return _export_frozen(FrozenResearchTask.model_validate(response.payload), options.output)


async def _remote_run(options: argparse.Namespace) -> dict[str, Any]:
    async with _runtime_client(options.server) as client:
        if options.run_command == "list":
            response = await client.list_runs(project=options.project)
        elif options.run_command == "show":
            response = await client.detail(options.run_id)
        elif options.run_command == "start":
            response = await client.start(options.run_id)
        elif options.run_command == "stop":
            response = await client.stop(options.run_id)
        elif options.run_command == "resume":
            response = await client.resume(options.run_id)
        else:
            raise ValueError("Unknown runtime action")
        return _runtime_response(response)


def parser() -> argparse.ArgumentParser:
    result = SafeArgumentParser(prog="mars", description="MARS project preflight and research tools")
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Control the same backend-owned runs used by the web UI")
    run.add_argument("--server", required=True, help="Explicit loopback backend origin; no service is started")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    listing = run_commands.add_parser("list")
    listing.add_argument("--project", default="")
    for action in ("show", "start", "stop", "resume"):
        control = run_commands.add_parser(action)
        control.add_argument("run_id")
    project = commands.add_parser("project", help="Inspect and freeze a generic project contract without starting research")
    project.add_argument("--server", help="Use the selected backend for the same preflight and freeze service")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_commands.add_parser("defaults", help="Show the finite standard research budgets")
    create = project_commands.add_parser("create", help="Save a frozen task under the selected backend owner; does not start research")
    create.add_argument("--contract", type=Path, required=True, help="Previously frozen task JSON")
    create.add_argument("--name", required=True, help="Task name displayed by the shared backend")
    create.add_argument("--request-id", help="Stable non-secret ID retained before sending; omission is non-idempotent")
    request_status = project_commands.add_parser("request-status", help="Read a saved creation request without starting or retrying it")
    request_status.add_argument("request_id", help="Previously retained creation request ID")
    request_status.add_argument("--task-sha256", help="Optional expected frozen task hash; no source files are read")
    for action in ("preflight", "freeze"):
        contract_command = project_commands.add_parser(action)
        contract_command.add_argument("--config", type=Path, required=True, help="research_project.v1 YAML")
        if action == "freeze":
            contract_command.add_argument("--goal", required=True)
            contract_command.add_argument("--mode", choices=("bounded_auto", "manual"), default="bounded_auto")
            contract_command.add_argument("--budget", type=Path, help="Complete budget YAML; defaults are used when omitted")
            contract_command.add_argument("--output", type=Path, required=True, help="New frozen task JSON file")
    cfg = configuration()
    for name in ("doctor", "research"):
        command = commands.add_parser(name, help="Legacy StaticPIMC workflow; not the shared UI runtime")
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--data", type=Path, required=True, help="Real static .pth capture with x/y/nf")
        command.add_argument("--model", default=get_agent_config("coding").model_name)
        if name == "doctor":
            command.add_argument("--check-model", action="store_true", help="Make one real small API request")
        else:
            command.add_argument("--task", default="在静态 PIMC 上减少至少20%的实参数量，RES性能不下降。完成调研、假设、代码修改、实验、分析与迭代。")
            command.add_argument("--output", type=Path)
            command.add_argument("--reuse-research-from", type=Path,
                                 help="Reuse a passed, audited research stage from an identical pre-test run; no new research API call")
            command.add_argument("--reduction", type=float, default=cfg["reduction"])
            command.add_argument("--max-degradation-db", type=float, default=cfg["max_degradation_db"])
            command.add_argument("--rounds", type=int, default=cfg["rounds"])
            command.add_argument("--max-steps", type=int, default=cfg["max_steps"])
            command.add_argument("--timeout-seconds", type=int, default=cfg["timeout_seconds"])
            command.add_argument("--prepare-only", action="store_true", help="Research, code and model preflight; performs no training or test metrics")
    for name in ("resume", "status"):
        command = commands.add_parser(name, help="Legacy StaticPIMC run directory operation")
        command.add_argument("run", type=Path)
    return result


async def doctor(repo: Path, data: Path, model: str, check_model: bool) -> dict[str, Any]:
    coding_config = get_agent_config("coding")
    result: dict[str, Any] = {"repo": str(repo), "data": str(data), "dataset_present": data.is_file(),
        "key_configured": bool(env_or_local(coding_config.api_key_env)), "python": sys.version.split()[0],
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
            provider, cfg = select_provider(replace(coding_config, model_name=model,
                max_tokens=1024, max_retries=0, request_timeout_seconds=45))
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
    if options.command == "run":
        return await _remote_run(options)
    if options.command == "project":
        if options.server:
            return await _remote_project(options)
        if options.project_command in {"create", "request-status"}:
            raise RuntimeClientError("server_required", "Project creation and lookup require an explicit --server backend owner")
        if options.project_command == "defaults":
            return default_research_budget().model_dump(mode="json")
        project = load_project_contract(options.config.expanduser().resolve())
        if options.project_command == "preflight":
            return preflight_project(project).model_dump(mode="json")
        project_budget = (ProjectResearchBudget.model_validate(yaml.safe_load(options.budget.read_text(encoding="utf-8")))
                  if options.budget else default_research_budget())
        frozen = freeze_research_task(project, goal=options.goal, mode=options.mode, budget=project_budget)
        return _export_frozen(frozen, options.output)
    logger.warning("Legacy StaticPIMC CLI selected; shared web tasks use mars run --server <origin>")
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
        initialize(repo, data, root, options.task, options.model, budget,
                   reuse_research_from=options.reuse_research_from.expanduser().resolve() if options.reuse_research_from else None)
    else:
        root = options.run.expanduser().resolve()
    manifest = read_record(root / "input/manifest.json")
    # This research command authorizes public literature retrieval. Respect a configured domain scope.
    if not env_or_local("MARS_WEB_SEARCH_ALLOWLIST"):
        set_runtime_env({"MARS_WEB_SEARCH_ALLOWLIST": ",".join(manifest["configuration"]["source_domains"])})
    set_runtime_env({"MARS_ENABLE_NETWORK_TOOLS": "true"})
    service = CliResearchService(CliAgents(manifest["model"], manifest["configuration"]["coding_loop"],
        research_author=manifest["configuration"].get("research_author"),
        generation=manifest["configuration"].get("generation")))
    state = await service.run(root, prepare_only=bool(getattr(options, "prepare_only", False)))
    return {"status": state["status"], "error": state.get("error"), "run": str(root), "report": str(root / "report.md"),
            "final_comparison": state.get("final_comparison")}


def main() -> int:
    options = argparse.Namespace(command=None)
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")
    try:
        options = parser().parse_args()
        result = asyncio.run(dispatch(options))
    except KeyboardInterrupt:
        if options.command == "run":
            logger.warning("Client interrupted; inspect the same backend with mars run --server <origin> show <run_id> before retrying")
        elif options.command == "project" and getattr(options, "project_command", None) == "create":
            logger.warning("Client interrupted; use project --server <origin> request-status <request_id> to inspect an identified save before retrying")
        else:
            logger.warning("Interrupted; inspect the selected workflow's saved status before continuing")
        return 130
    except RuntimeClientError as exc:
        if options.command == "project" and getattr(options, "project_command", None) == "create" and exc.code in {
                "timeout", "connection_failed", "invalid_response", "response_too_large", "creation_response_mismatch"}:
            logger.warning("Save outcome may be unknown; use project --server <origin> request-status <request_id>; do not replace or resend the request automatically")
        sys.stdout.write(json.dumps({"http_ok": False, "error_code": exc.code,
            "http_status": exc.status_code, "message": str(exc)}, ensure_ascii=False) + "\n")
        return 2
    except Exception as exc:
        # Pydantic/YAML/OS diagnostics may embed input values or partial values
        # which cannot be reliably redacted by replacing the full credential.
        explanations = {
            "ValidationError": "Input does not match the project or configuration schema.",
            "FileExistsError": "The output already exists; choose a new output file.",
            "FileNotFoundError": "A required input or configuration file is unavailable.",
            "ValueError": "Invalid input or state; verify the selected command's requirements.",
        }
        logger.error("{}: {}", type(exc).__name__, explanations.get(type(exc).__name__,
            "Command failed; inspect the configured inputs and saved task state."))
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if result.get("http_ok") is False:
        return 2
    if options.command == "project" and options.project_command in {"create", "request-status"}:
        return 0 if result.get("creation_confirmed") is True else 2
    if options.command == "doctor":
        return 0 if result["ready"] else 2
    if options.command == "project" and result.get("ready") is False:
        return 2
    return 2 if result.get("status") in {"blocked_data", "failed", "interrupted"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
