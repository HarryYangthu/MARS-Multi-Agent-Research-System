#!/usr/bin/env python3
"""Audit or run the real five-stage API path without replacing model/tool outcomes."""
from __future__ import annotations

import argparse
import ast
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.bridge.idea_input_context import IdeaRequirements
from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
from app.harness.llm.model_registry import AgentConfig, get_agent_config, provider_configured_for_agent
from app.harness.runtime.readiness import check_readiness
from app.harness.schema.validator import validate_document
from app.settings import get_settings

STAGES = {"idea": "proposal.v1", "experiment": "experiment_plan.v1", "coding": "code_spec.v1",
          "execution": "run_log.v1", "writing": "report.v1"}


class E2EConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_id: Literal["runtime.e2e.v1"] = "runtime.e2e.v1"
    project: str = "pimc"
    task: str = ""
    user_request: str = ""
    api_url: str = "http://127.0.0.1:8000"
    baseline_file: str | None = None
    data_path: str | None = None
    data_description: str = ""
    data_source_id: str | None = None
    selected_skills_by_agent: dict[str, list[str]] = Field(default_factory=dict)
    host_environment_file: str | None = None
    context_files: dict[str, str] = Field(default_factory=dict)
    idea_requirements: IdeaRequirements = Field(default_factory=IdeaRequirements)
    idea_scope: Literal["method_proposal", "project_proposal"] = "project_proposal"
    approval_mode: Literal["human", "auto"] = "human"
    evaluation_policy: dict[str, Any] | None = None
    require_network: bool = True
    network_allowed_domains: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=1800, ge=1)
    poll_seconds: int = Field(default=2, ge=1, le=30)

    @field_validator("api_url")
    @classmethod
    def valid_api(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("api_url must be an HTTP(S) service URL without embedded credentials")
        return value.rstrip("/")

    @field_validator("network_allowed_domains")
    @classmethod
    def valid_domains(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or "/" in value or ":" in value or "*" in value or value.strip() != value:
                raise ValueError("network domains must be explicit hostnames")
        return values


def file_record(path: Path) -> dict[str, Any]:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": checksum.hexdigest()}


def input_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def prepare_regression_fixture(root: Path) -> Path:
    """Create real CPU numerical work, never any Agent answer or claimed result."""
    from app.harness.project_workspace import open_folder
    root = root.resolve()
    if root.exists():
        raise ValueError("fixture directory already exists; choose a new directory to preserve previous work")
    project = open_folder(str(root), create=True)
    (root / "baseline").mkdir()
    (root / "data").mkdir()
    initial = "# Actual initial polynomial ridge model; the baseline remains immutable.\nDEGREE = 1\nREGULARIZATION = 0.01\n"
    (root / "baseline/baseline.py").write_text(initial)
    (root / "candidate.py").write_text(initial)
    source = REPO_ROOT / "projects/synthetic_regression/src/synthetic_regression_adapter/resources/dataset.json"
    (root / "data/dataset.json").write_bytes(source.read_bytes())
    rules = ("# CPU regression engineering task\n\n"
        "This project fits actual packaged synthetic numeric samples; it is not PIMC data or a physical experiment.\n"
        "Never edit baseline/, data/, .mars/, diagnostics.yaml, the evaluator, or the train/held-out split.\n"
        "Only candidate.py DEGREE and REGULARIZATION may change: integer degree 1..5 and finite regularization >=0.\n"
        "Both models use the same seed and disjoint training/held-out samples, with training-only normalization.\n"
        "The evaluator executes both real ridge fits and emits request-bound metric evidence.\n"
        "Goal: improve held-out MSE over the fixed baseline; report failure honestly if no improvement.\n")
    (root / "AGENTS.md").write_text(rules)
    (root / "context/method.md").write_text((REPO_ROOT / "projects/synthetic_regression/README.md").read_text())
    link_path = root / ".mars/repo_link.yaml"
    link = yaml.safe_load(link_path.read_text())
    link.update(allowed_paths=["candidate.py", "baseline/", "data/", "context/", "AGENTS.md", "README.md"],
                protected_paths=["baseline/", "data/", ".mars/", "diagnostics.yaml"])
    link_path.write_text(yaml.safe_dump(link, allow_unicode=True, sort_keys=False))
    description = ("Actual fixed synthetic numerical x/y pairs copied byte-for-byte from the public synthetic_regression pack. "
        "Use seed 0: shuffle indices, train on first eight samples and assess on last three, no overlap. "
        "Fit actual normalized polynomial ridge regressions; normalization uses training inputs only. "
        "This is an engineering fixture, not a claim about PIMC captures or scientific novelty.")
    host = root.parent / (root.name + "-host")
    host.mkdir(exist_ok=False)
    (project.metadata_root / "diagnostics.yaml").write_text(yaml.safe_dump({"project": project.name,
        "loop": {"max_iterations": 1, "default_budget": 1},
        "metrics": {"improvement": {"target": 1e-12, "direction": "gte", "aggregation": "min"}}}))
    checker = host / "check_candidate.py"
    checker.write_text(
        "import ast, hashlib, json, math\nfrom pathlib import Path\n"
        + "root = Path(" + repr(str(root)) + ")\n"
        + "manifest = json.loads((root / '.mars/regression_fixture.v1.json').read_text())\n"
        "for key, relative in [('baseline', 'baseline/baseline.py'), ('data', 'data/dataset.json')]:\n"
        "    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == manifest[key]['sha256'], relative + ' changed'\n"
        "values = {}\n"
        "for node in ast.parse((root / 'candidate.py').read_text()).body:\n"
        "    assert isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name), 'literal assignments only'\n"
        "    assert node.targets[0].id not in values, 'duplicate assignment'\n"
        "    values[node.targets[0].id] = ast.literal_eval(node.value)\n"
        "assert set(values) == {'DEGREE', 'REGULARIZATION'}\n"
        "assert type(values['DEGREE']) is int and 1 <= values['DEGREE'] <= 5\n"
        "assert type(values['REGULARIZATION']) in (int, float) and math.isfinite(values['REGULARIZATION']) and values['REGULARIZATION'] >= 0\n"
        "print('Syntax, candidate parameter constraints, and protected input checks passed; no experiment executed.')\n")
    check_argv = [sys.executable, str(checker)]
    argv = [sys.executable, str(Path(__file__).resolve()), "--regression-command", "--fixture", str(root)]
    execution = yaml.safe_load((REPO_ROOT / "configs/execution.yaml").read_text())
    execution["execution"].update(backend="local_command", max_concurrency=1,
        local_commands=[{"id": "actual_regression", "label": "Actual CPU ridge comparison", "argv": argv,
                         "required_metrics": ["baseline_mse", "candidate_mse", "improvement", "candidate_terms"]}])
    execution["code_checks"] = {kind: {"enabled": True, "commands": [{"id": "candidate_contract",
        "label": "Syntax, parameter constraints and protected input integrity", "argv": check_argv}]} for kind in ("test", "lint")}
    (host / "execution.yaml").write_text(yaml.safe_dump(execution, sort_keys=False))
    tools = yaml.safe_load((REPO_ROOT / "configs/tools.yaml").read_text())
    for tool in ("execution.simulation_runner", "execution.batch_runner"):
        tools["tools"][tool]["command_allowlist"] = [argv]
    for tool in ("code.test_runner", "code.lint"):
        tools["tools"][tool]["command_allowlist"] = [check_argv]
    (host / "tools.yaml").write_text(yaml.safe_dump(tools, sort_keys=False))
    host_env = {"MARS_EXECUTION_CONFIG_PATH": str(host / "execution.yaml"),
                "MARS_TOOLS_CONFIG_PATH": str(host / "tools.yaml"), "MARS_EXECUTION_BACKEND": "local_command",
                "MARS_ENABLE_NETWORK_TOOLS": "true", "MARS_WEB_SEARCH_ALLOWLIST": "arxiv.org"}
    (host / "environment.json").write_text(json.dumps(host_env, indent=2))
    config = E2EConfig(project=project.name, task="real-cpu-polynomial-regression",
        user_request=("Run the full Idea→Experiment→Coding→Execution→Writing research pipeline on this actual CPU regression fixture. "
            "Read baseline/baseline.py, candidate.py, the numeric data and domain context. Propose and implement changes only to "
            "candidate.py DEGREE and REGULARIZATION. Preserve baseline and data; degree must remain 1..5 and regularization finite >=0. "
            "Use one experiment with seed 0; both models independently fit the same eight training samples and use the same three held-out samples. "
            "Measure baseline_mse, candidate_mse, improvement and candidate_terms with the configured real local command. "
            "Report the actual comparison, including no improvement or failed validation. Do not claim PIMC or research novelty."),
        baseline_file="baseline/baseline.py", data_path="data/dataset.json", data_description=description,
        context_files={"background": "AGENTS.md", "literature_notes": "context/method.md"},
        approval_mode="human", require_network=True, network_allowed_domains=["arxiv.org"],
        host_environment_file=str(host / "environment.json"),
        evaluation_policy={"run": {"completion_gate": {"mode": "enforce"}}})
    config_path = root / "runtime_e2e.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(), allow_unicode=True, sort_keys=False))
    atomic_json(root / ".mars/regression_fixture.v1.json", {"schema": "regression.fixture.v1", "project": project.name,
        "baseline": file_record(root / "baseline/baseline.py"), "data": file_record(root / "data/dataset.json"),
        "command_argv": argv})
    return config_path


def configure_host_environment(config: E2EConfig, base: Path) -> None:
    if config.host_environment_file is None:
        return
    path = input_path(config.host_environment_file, base)
    environment = json.loads(path.read_text())
    allowed = {"MARS_EXECUTION_CONFIG_PATH", "MARS_TOOLS_CONFIG_PATH", "MARS_EXECUTION_BACKEND",
               "MARS_ENABLE_NETWORK_TOOLS", "MARS_WEB_SEARCH_ALLOWLIST"}
    if not isinstance(environment, dict) or set(environment) - allowed or any(not isinstance(value, str) for value in environment.values()):
        raise ValueError("host environment contains unsupported keys or values")
    from app.settings import set_runtime_env
    set_runtime_env(environment)


def regression_command(root: Path) -> None:
    """Execute actual fits and emit the host's local_command_result.v1 protocol."""
    sys.path.insert(0, str(REPO_ROOT / "projects/synthetic_regression/src"))
    from synthetic_regression_adapter.adapter import fit_ridge
    import math
    request = json.loads(Path(os.environ["MARS_JOB_REQUEST"]).read_text())
    output = Path(os.environ["MARS_RESULT_PATH"])
    fixture = json.loads((root / ".mars/regression_fixture.v1.json").read_text())
    if request["project"] != fixture["project"]:
        raise ValueError("local command project does not match its actual fixture")
    baseline_path = root / "baseline/baseline.py"
    if file_record(baseline_path)["sha256"] != fixture["baseline"]["sha256"]:
        raise ValueError("protected baseline changed")
    data_path = Path(request.get("config", {}).get("data_path") or root / "data/dataset.json")
    if file_record(data_path)["sha256"] != fixture["data"]["sha256"]:
        raise ValueError("actual execution dataset differs from the registered fixture")
    dataset = json.loads(data_path.read_text())
    indices = list(range(len(dataset["x"])))
    seed = request.get("seed")
    random.Random(seed if seed is not None else 0).shuffle(indices)
    train, held_out = indices[:8], indices[-3:]
    if set(train) & set(held_out):
        raise ValueError("training/held-out overlap")
    train_x, train_y = [dataset["x"][i] for i in train], [dataset["y"][i] for i in train]
    mean = sum(train_x) / len(train_x)
    scale = max(abs(value - mean) for value in train_x)
    results: dict[str, Any] = {}
    for name, path in (("baseline", baseline_path), ("candidate", root / "candidate.py")):
        assignments: dict[str, Any] = {}
        for statement in ast.parse(path.read_text()).body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                assignments[statement.targets[0].id] = ast.literal_eval(statement.value)
            else:
                raise ValueError("model source must contain only literal DEGREE and REGULARIZATION assignments")
        degree, ridge = assignments["DEGREE"], assignments["REGULARIZATION"]
        if type(degree) is not int or not 1 <= degree <= 5 or type(ridge) not in {int, float} or not math.isfinite(ridge) or ridge < 0:
            raise ValueError("candidate violates the preserved model constraints")
        design = [[((value - mean) / scale) ** power for power in range(degree + 1)] for value in train_x]
        coefficients = fit_ridge(design, train_y, float(ridge))
        predictions = [sum(c * ((dataset["x"][index] - mean) / scale) ** power for power, c in enumerate(coefficients)) for index in held_out]
        mse = sum((pred - dataset["y"][index]) ** 2 for pred, index in zip(predictions, held_out, strict=True)) / len(held_out)
        results[name] = {"mse": mse, "degree": degree, "regularization": ridge, "coefficients": coefficients,
                         "predictions": predictions, "source": file_record(path)}
    evidence = {"schema": "regression.actual_fit.v1", "run_id": request["run_id"], "invocation_id": request["invocation_id"],
        "training_indices": train, "held_out_indices": held_out, "data": file_record(data_path), "results": results,
        "evaluator": file_record(Path(__file__)), "scope": "synthetic_numeric_engineering_fixture"}
    atomic_json(output.parent / "measurement.json", evidence)
    metrics = {"baseline_mse": results["baseline"]["mse"], "candidate_mse": results["candidate"]["mse"],
        "improvement": results["baseline"]["mse"] - results["candidate"]["mse"], "candidate_terms": results["candidate"]["degree"] + 1}
    atomic_json(output, {"schema": "local_command_result.v1", "invocation_id": request["invocation_id"],
        "run_id": request["run_id"], "experiment_id": request["experiment_id"], "status": "completed",
        "metrics": metrics, "evidence_paths": ["measurement.json"]})


def effective_agents() -> list[AgentConfig]:
    selector = get_settings().mars_idea_runtime_profile
    if selector == "focused_v1":
        focused = yaml.safe_load((REPO_ROOT / "configs/idea_focused.yaml").read_text())
        result = [get_agent_config(focused["author_agent"]), get_agent_config(focused["review_agent"])]
    else:
        from app.agents.idea.runtime_profile import resolve_idea_profile
        profile = resolve_idea_profile(selector)
        result = [profile.lead, profile.child] if profile is not None else [get_agent_config("idea"), get_agent_config("idea_research")]
    result.extend(get_agent_config(stage) for stage in STAGES if stage != "idea")
    return result


def preflight(config: E2EConfig, *, base: Path) -> dict[str, Any]:
    settings = get_settings()
    checks: list[dict[str, Any]] = []
    inputs: dict[str, Any] = {}

    def add(name: str, ready: bool, message: str, **details: Any) -> None:
        checks.append({"name": name, "ready": ready, "message": message, "details": details})

    add("task_input", bool(config.task.strip() and config.user_request.strip()), "task and user_request must describe the actual task")
    for name, value in (("baseline_file", config.baseline_file), ("data_path", config.data_path)):
        path = input_path(value, base) if value else None
        available = path is not None and path.is_file() and path.stat().st_size > 0
        add(name, available, "required real input file is available" if available else "required real input file is missing",
            path=str(path) if path else None)
        if available and path is not None:
            inputs[name] = file_record(path)
    add("data_description", bool(config.data_description.strip()), "supply the actual data format and scientific meaning")
    for label, value in config.context_files.items():
        path = input_path(value, base)
        add("context_file:" + label, path.is_file(), "caller-provided context file", path=str(path))
        if path.is_file():
            inputs[label] = file_record(path)
    for agent in effective_agents():
        add("model:" + agent.name, agent.enabled and provider_configured_for_agent(agent),
            "actual model configuration is available" if provider_configured_for_agent(agent) else "model endpoint or required credential is missing",
            provider=agent.model_provider, model=agent.model_name, credential_env=agent.api_key_env)
        if agent.debate_enabled:
            for index, participant in enumerate(agent.debate_participants):
                provider = str(participant.get("provider", agent.model_provider))
                cfg = replace(agent, model_provider=provider, model_name=str(participant.get("model", agent.model_name)))
                add(f"model:{agent.name}:debate:{index}", provider_configured_for_agent(cfg),
                    "configured debate participant requires a real provider", provider=provider, model=cfg.model_name)
    readiness = check_readiness(project=config.project)
    for check in readiness.checks:
        # Development-mode warnings about absent real assets still block an E2E claim.
        if check.name != "llm_providers":
            add("runtime:" + check.name, check.ready, check.message, **check.details)
    backend = settings.mars_execution_backend
    add("pipeline_execution_adapter", backend in {"paper_static", "pim_cpu", "local_command"},
        "the five-stage batch path must select an implemented real execution adapter", backend=backend)
    if backend == "local_command":
        import shutil
        from app.harness.tools.config import load_execution_config
        execution = load_execution_config().get("execution", {})
        commands = execution.get("local_commands", [])
        configured = bool(commands) and all(isinstance(item, dict) and isinstance(item.get("argv"), list)
            and item["argv"] and shutil.which(str(item["argv"][0])) is not None for item in commands)
        add("local_execution_commands", configured, "real local command and executable must be configured; no fallback command is installed")
    if backend == "pim_cpu":
        add("dataset_consumption", False,
            "pim_cpu generates its own numerical signals; it does not consume this configured external dataset. Select the actual data-backed project adapter.")
    if config.require_network:
        actual_domains = {part.strip().lower() for part in settings.mars_web_search_allowlist.split(",") if part.strip()}
        add("network_tools", settings.mars_enable_network_tools, "network research must be explicitly enabled in the service")
        add("network_domains", bool(config.network_allowed_domains) and set(config.network_allowed_domains) <= actual_domains,
            "requested research hosts must be explicitly present in the service allowlist",
            requested=config.network_allowed_domains, configured=sorted(actual_domains))
    add("approval_policy", not (settings.is_production and config.approval_mode == "auto"),
        "production requires human approval; the driver never approves artifacts or gates")
    from app.bridge.evaluation_policy import policy_for_task
    policy_for_task(config.evaluation_policy)
    missing = [item for item in checks if not item["ready"]]
    return {"schema_id": "runtime.e2e.report.v1", "status": "required_dependency_missing" if missing else "ready",
        "failure_code": ("model_configuration_missing" if missing and all(item["name"].startswith("model:") for item in missing)
                         else "required_dependency_missing" if missing else None),
        "phase": "preflight", "created_at": datetime.now(timezone.utc).isoformat(),
        "project": config.project, "task_config_sha256": digest(config.model_dump()), "inputs": inputs,
        "checks": checks, "missing": missing, "runtime_profile": settings.mars_idea_runtime_profile,
        "model_calls_executed": 0, "tool_calls_executed": 0, "experiments_executed": 0,
        "scientific_validated": False, "approval_mode": config.approval_mode}


def request_payload(config: E2EConfig, *, base: Path, source_id: str) -> dict[str, Any]:
    assert config.baseline_file is not None
    context = {label: input_path(path, base).read_text(encoding="utf-8") for label, path in config.context_files.items()}
    baseline = input_path(config.baseline_file, base).read_text(encoding="utf-8")
    if "baseline_code" in context and context["baseline_code"] != baseline:
        raise ValueError("baseline_code context disagrees with baseline_file")
    context.update(baseline_code=baseline, data_description=config.data_description)
    payload: dict[str, Any] = {"task": config.task, "project": config.project, "entrypoint": "pipeline",
        "standalone": False, "user_request": config.user_request, "auto_approve": config.approval_mode == "auto",
        "idea_context": context, "idea_scope": config.idea_scope,
        "idea_requirements": config.idea_requirements.model_dump(exclude_none=True), "data_source": {"id": source_id}}
    if config.selected_skills_by_agent:
        payload["selected_skills_by_agent"] = config.selected_skills_by_agent
    if config.evaluation_policy is not None:
        payload["evaluation_policy"] = config.evaluation_policy
    return payload


def verify_local_command_evidence(run_root: Path, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    receipts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for path in sorted((run_root / "execution/local_commands").glob("*/*/execution_receipt.json")):
        receipt = json.loads(path.read_text())
        fingerprint = "sha256:" + file_record(path)["sha256"]
        if receipt.get("schema") != "local_command_receipt.v1" or receipt.get("status") != "completed" or receipt.get("returncode") != 0:
            continue  # Failed attempts are retained but cannot authenticate measured metrics.
        files = [(path.parent / "job.json", receipt.get("request_sha256")),
                 (path.parent / "result.json", receipt.get("result_sha256"))]
        files.extend((Path(item["path"]), item["sha256"]) for item in receipt.get("evidence", []))
        if not receipt.get("evidence") or any(not file.is_file() or not file.resolve().is_relative_to(path.parent.resolve())
                or "sha256:" + file_record(file)["sha256"] != expected for file, expected in files):
            errors.append("local command receipt has missing or changed evidence: " + str(path))
            continue
        result = json.loads((path.parent / "result.json").read_text())
        if any(result.get(key) != receipt.get(key) for key in ("invocation_id", "run_id", "experiment_id")):
            errors.append("local command result identity differs from its host receipt")
            continue
        receipts[fingerprint] = {"receipt": file_record(path), "metrics": result["metrics"],
            "invocation_id": receipt["invocation_id"], "evidence": receipt["evidence"]}
    for row in metrics:
        receipt = receipts.get(str(row.get("fingerprint_hash")))
        if receipt is None or receipt["metrics"] != row.get("metrics"):
            errors.append("summarized metrics do not match a verified actual command receipt")
    return {"receipts": list(receipts.values()), "verification_errors": errors}


def collect_evidence(run_root: Path) -> dict[str, Any]:
    errors: list[str] = []
    stages: dict[str, Any] = {}
    for stage, schema in STAGES.items():
        invocations: list[dict[str, Any]] = []
        for checkpoint in sorted((run_root / "agent_traces" / stage).glob("*/checkpoint.json")):
            audit = audit_trace(checkpoint.parent)
            invocations.append({"invocation_id": checkpoint.parent.name, "audit": audit})
        responses = sum(int(item["audit"].get("facts", {}).get("counts", {}).get("model_responses", 0)) for item in invocations)
        artifacts = []
        for path in sorted((run_root / stage).glob("*.approved.md")):
            valid = validate_document(path.read_text(), expected_schema=schema)
            if valid.valid:
                artifacts.append(file_record(path))
        if responses < 1 or not artifacts or any(not item["audit"].get("consistent") for item in invocations):
            errors.append(stage + ": missing real model response, valid approved artifact, or consistent trace")
        stages[stage] = {"model_responses": responses, "invocations": invocations, "approved_artifacts": artifacts}
    tool_observations = 0
    for events in (run_root / "agent_traces").glob("*/*/events.jsonl"):
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        tool_observations += sum(row.get("kind") == "observation" for row in rows)
    if not tool_observations:
        errors.append("no recorded actual tool observations")
    metrics_path, batch_path = run_root / "execution/metrics.json", run_root / "execution/batch_summary.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else []
    batch = json.loads(batch_path.read_text()) if batch_path.is_file() else {}
    if not isinstance(metrics, list) or not metrics or any(not row.get("metrics") or not row.get("fingerprint_hash") for row in metrics):
        errors.append("actual execution metrics and fingerprints are missing")
    if not batch or batch.get("failures"):
        errors.append("execution batch summary missing or reports failures")
    command_evidence: dict[str, Any] = {}
    if batch.get("runtime_backend") == "local_command":
        command_evidence = verify_local_command_evidence(run_root, metrics if isinstance(metrics, list) else [])
        errors.extend(command_evidence["verification_errors"])
    return {"stages": stages, "tool_observations": tool_observations, "execution_metrics": metrics,
        "execution_batch": batch, "local_command_evidence": command_evidence, "verification_errors": errors,
        "scientific_validated": False, "validation_scope": "actual_pipeline_execution_and_artifact_contracts"}


def validate_dataset_selection(profile: dict[str, Any], *, project: str, sha256: str) -> None:
    """Compare the data API's algorithm-qualified checksum with the local digest."""
    if profile.get("checksum") != "sha256:" + sha256 or profile.get("project") != project:
        raise ValueError("selected service dataset does not match the configured data file/project")


async def execute(config: E2EConfig, *, base: Path, report: dict[str, Any], output: Path) -> dict[str, Any]:
    if report["status"] != "ready":
        return report
    async with httpx.AsyncClient(base_url=config.api_url, timeout=30.0) as client:
        try:
            service = await client.get("/api/readiness", params={"project": config.project})
            service.raise_for_status()
            remote = service.json()
            if not remote.get("ready"):
                return {**report, "status": "required_dependency_missing", "phase": "service_preflight", "service_readiness": remote}
        except httpx.HTTPError as exc:
            return {**report, "status": "required_dependency_missing", "phase": "service_preflight",
                    "error": "MARS API unavailable: " + type(exc).__name__}
        source_id = config.data_source_id
        source_profile: dict[str, Any]
        if source_id is None:
            assert config.data_path is not None
            path = input_path(config.data_path, base)
            async def content() -> Any:
                with path.open("rb") as handle:
                    while block := handle.read(1024 * 1024):
                        yield block
            response = await client.post("/api/data-sources/upload", params={"filename": path.name,
                "project": config.project, "description": config.data_description}, content=content())
            response.raise_for_status()
            source_profile = response.json()
            source_id = str(source_profile["id"])
        else:
            response = await client.get(f"/api/data-sources/{source_id}")
            response.raise_for_status()
            source_profile = response.json()
        validate_dataset_selection(source_profile, project=config.project,
            sha256=report["inputs"]["data_path"]["sha256"])
        payload = request_payload(config, base=base, source_id=source_id)
        created = await client.post("/api/runs", json=payload)
        created.raise_for_status()
        run_id = str(created.json()["run_id"])
        report.update(run_id=run_id, phase="execution", status="running", request_sha256=digest(payload))
        report.update(model_calls_executed=None, tool_calls_executed=None, experiments_executed=None)
        atomic_json(output, report)
        started = await client.post(f"/api/runs/{run_id}/start")
        started.raise_for_status()
        deadline = time.monotonic() + config.timeout_seconds
        while time.monotonic() < deadline:
            response = await client.get(f"/api/runs/{run_id}")
            response.raise_for_status()
            state = response.json()
            report["run_state"] = state
            if state.get("status") in {"completed", "failed", "stopped"}:
                report["status"] = "completed" if state["status"] == "completed" else "failed"
                break
            waiting = [key for key, value in state.get("states", {}).items() if value == "waiting_review"]
            report["status"] = "blocked" if waiting or state.get("status") == "waiting_feedback" else "running"
            report["waiting_for"] = waiting or (["feedback_decision"] if state.get("status") == "waiting_feedback" else [])
            atomic_json(output, report)
            await asyncio.sleep(config.poll_seconds)
        else:
            report.update(status="blocked", reason="driver_timeout_service_run_preserved",
                          message="The service still owns the run; this driver did not cancel, approve, or replay it.")
        run_root = REPO_ROOT / "runs" / run_id
        if report["status"] == "completed":
            report["evidence"] = collect_evidence(run_root)
            report["model_calls_executed"] = sum(stage["model_responses"] for stage in report["evidence"]["stages"].values())
            report["tool_calls_executed"] = report["evidence"]["tool_observations"]
            report["experiments_executed"] = len(report["evidence"]["execution_metrics"])
            if report["evidence"]["verification_errors"]:
                report.update(status="failed", reason="execution_evidence_incomplete")
        report["run_root"] = str(run_root)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prepare-regression", type=Path, help="Create and register a real public CPU research fixture and task configuration")
    parser.add_argument("--regression-command", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--execute", action="store_true", help="Start actual configured model and tool work via the running MARS API")
    args = parser.parse_args()
    if args.regression_command:
        if args.fixture is None:
            parser.error("--regression-command requires --fixture")
        regression_command(args.fixture.resolve())
        return 0
    if args.output is None:
        parser.error("--output is required for reports")
    report: dict[str, Any] = {}
    try:
        if args.prepare_regression:
            args.config = prepare_regression_fixture(args.prepare_regression)
        raw = yaml.safe_load(args.config.read_text()) if args.config else {}
        config = E2EConfig.model_validate(raw)
        base = args.config.resolve().parent if args.config else Path.cwd()
        configure_host_environment(config, base)
        report = preflight(config, base=base)
        report["execution_requested"] = args.execute
        if args.prepare_regression:
            report["prepared_task_config"] = str(args.config)
            report["host_environment_file"] = config.host_environment_file
        if args.execute:
            report = asyncio.run(execute(config, base=base, report=report, output=args.output))
    except Exception as exc:
        report = {**report, "schema_id": "runtime.e2e.report.v1", "status": "failed", "phase": "driver",
                  "error_type": type(exc).__name__, "error": str(exc), "scientific_validated": False}
    atomic_json(args.output.resolve(), report)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["status"] in {"ready", "completed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
