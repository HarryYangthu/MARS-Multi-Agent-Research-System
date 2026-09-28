"""Real GLM/file-tool/CPU/results diagnostic sharing one frozen SQLite budget.

This is a bounded developer integration check, not the complete research DAG.
It uses only authored synthetic data and never opens normal contract execution.
Supply the configured GLM credential in the child process environment.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

import frontmatter
import jsonschema
from loguru import logger

from app.bridge.orchestrator import Orchestrator
from app.bridge.research_contract_service import default_research_budget, freeze_research_task
from app.bridge.research_job_service import ResearchJobRequest, ResearchJobService, load_research_job_policy
from app.bridge.research_project_scope import prepare_project_scope
from app.bridge.research_run_service import create_research_run, research_execution_admission
from app.bridge.results_export import create_results_export, read_results_export
from app.bridge.results_service import collect_run_results
from app.harness.agent_loop.executor import LoopInput, NativeAgentLoop
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.discovery.snapshots import SnapshotPolicy
from app.harness.llm.accounting import guarded_complete
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Message
from app.harness.persistence import atomic_write_json
from app.harness.runtime.project_scope import bind_project_scope, verify_candidate_scope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.research_contract import ProjectContract, ResearchBudget
from app.harness.runtime.research_execution_scope import ResearchExecutionScope, bind_research_execution
from app.harness.runtime.state_journal import StateJournal
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import repo_root
from app.storage.run_store import RunStore
from scripts.diagnostics.contract_cpu_fixture import validate_features


def source_fingerprints() -> dict[str, str]:
    paths = (
        "scripts/verify_contract_cpu_loop.py", "scripts/diagnostics/contract_cpu_fixture.py",
        "backend/app/harness/tools/research_accounting.py", "backend/app/harness/tools/registry.py",
        "backend/app/bridge/research_job_service.py", "backend/app/execution/local/runner.py",
        "backend/app/execution/local/worker.py", "backend/app/harness/runtime/research_budget_ledger.py",
        "backend/app/harness/llm/research_accounting.py", "backend/app/harness/agent_loop/executor.py",
        "backend/app/bridge/results_service.py", "backend/app/bridge/results_export.py",
        "backend/app/bridge/research_results_usage.py", "backend/app/bridge/research_run_service.py",
        "backend/app/bridge/orchestrator.py", "configs/agents.yaml", "configs/research_jobs.yaml",
        "configs/research_defaults.yaml")
    return {name: hashlib.sha256((repo_root() / name).read_bytes()).hexdigest() for name in paths}


async def verify(output: Path) -> dict[str, Any]:
    sources = source_fingerprints()
    output.mkdir(parents=True, exist_ok=False)
    atomic_write_json(output / "started_source_fingerprints.json", sources)
    source = output / "source"
    source.mkdir()
    baseline = "def features(x):\n    return (1.0, x)\n"
    (source / "baseline.py").write_text(baseline)
    (source / "candidate.py").write_text(baseline)
    (source / "command.py").write_bytes((repo_root() / "scripts/diagnostics/contract_cpu_fixture.py").read_bytes())
    (source / "README.md").write_text(
        "This is an authored synthetic numerical integration workload, not an external scientific dataset.\n"
        "Target y = 0.4 + 0.6*x + 1.3*x*x + normal(0,0.04), x uniform[-1,1].\n"
        "For each seed, use 64 training and 64 separate held-out examples. Fit linear feature weights by "
        "normal equations with partial pivoting; evaluate held-out mean squared error.\n"
        "Compare seeds 41,42,43 with identical data for the two arms. Baseline features are (1.0,x).\n"
        "Improve only candidate.py. The allowed file is exactly one unannotated function features(x), "
        "whose single statement returns a tuple of 1-4 numerical features. Only finite constants, x, "
        "+,-,*, and constant integer powers 0-8 are permitted. No imports, calls, assignments, "
        "decorators, attributes, loops or external effects. Keep all other files unchanged.\n"
        "This is a small in-distribution test. Do not claim real-world superiority or significance.\n")
    project = ProjectContract.model_validate({"project_id": "contract_cpu_probe", "display_name": "合同账本真实 CPU 闭环诊断",
        "paths": {"code": str(source), "data": [], "knowledge": [], "output": str(output / "artifacts")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
            "arguments": ["command.py"], "entrypoint_files": ["command.py"]} for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "mse", "unit": "unitless", "direction": "minimize", "target": 0.02, "tolerance": 0.0}],
        "baseline_files": ["baseline.py", "command.py"], "allowed_paths": ["candidate.py"], "protected_paths": ["README.md"],
        "execution": {"kind": "local", "device": "cpu"}})
    budget = ResearchBudget.model_validate({**default_research_budget().model_dump(),
        "model_requests": 8, "tool_executions": 5, "operation_retries": 0,
        "training_job_seconds": 10, "training_process_seconds": 60, "research_activity_seconds": 600})
    frozen = freeze_research_task(project, goal="Measure a real generated feature change against a protected synthetic baseline", mode="manual", budget=budget)
    owner = Orchestrator(run_store=RunStore(output / "runs"))
    run = create_research_run(owner, name="Contract CPU loop diagnostic", contract=frozen).run
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    assert journal is not None
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=budget)
    ledger.initialize()
    project_scope = prepare_project_scope(run, candidate_id="one", snapshot_policy=SnapshotPolicy(allowed_paths=("*",)))
    jobs = ResearchJobService(run, ledger, load_research_job_policy())
    jobs.initialize()
    configured = get_agent_config("coding")
    if configured.model_provider != "zhipu" or not configured.model_name.lower().startswith("glm-5.3"):
        raise ValueError("The actual configured Coding provider must be GLM-5.3")
    provider, config = select_provider(replace(configured, max_tokens=4096, max_retries=0))
    schema: dict[str, Any] = {"type": "object", "additionalProperties": False,
        "required": ["schema_id", "change_summary", "hypothesis", "limitations"], "properties": {
            "schema_id": {"const": "contract_cpu_diagnostic.v1"},
            "change_summary": {"type": "string", "minLength": 1},
            "hypothesis": {"type": "string", "minLength": 1},
            "limitations": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}

    async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
        document = frontmatter.loads(text)
        errors = [error.message for error in jsonschema.Draft202012Validator(schema).iter_errors(document.metadata)]
        for name in ("code.repo_reader", "code.write_file"):
            if not any(row.get("tool") == name and row.get("ok") for row in observations):
                errors.append("An actual successful " + name + " receipt is required")
        try:
            content = (project_scope.candidate_root / "candidate.py").read_text()
            validate_features(content)
            if content == baseline:
                errors.append("The candidate was not changed")
            changes = verify_candidate_scope(project_scope)
            if {change.path for change in changes} != {"candidate.py"}:
                errors.append("Exactly candidate.py must change")
        except (OSError, ValueError, SyntaxError) as exc:
            errors.append("Candidate verification rejected: " + type(exc).__name__)
        return errors

    try:
        with bind_project_scope(project_scope), bind_research_execution(ResearchExecutionScope(ledger, "coding", "cpu-diagnostic-coding")):
            loop = await NativeAgentLoop().run(LoopInput(
                messages=[Message("user", "Read README.md and candidate.py using code.repo_reader, then implement one minimal "
                    "feature change through code.write_file. Follow the bounded arithmetic function contract. "
                    "Use each file read once and reuse its observation. Do not run any command. Submit the requested "
                    "diagnostic metadata with your change, falsifiable hypothesis and limitations. "
                    "No experimental result exists yet; do not invent one.")],
                provider=provider, config=config, registry=get_registry(), tools=("code.repo_reader", "code.write_file"),
                tool_context=ToolContext(run.run_id, run.project, "coding", extra={"run_root": str(run.root)}),
                policy=AgentLoopPolicy(protocol="native_tools", mode="react", native_observation_history=True,
                    max_model_calls=7, max_tool_steps=5, max_validation_repairs=1, max_protocol_repairs=1),
                final_schema=schema, trace_root=run.root / "coding/contract_diagnostic", validate=validate))
        (output / "candidate_diagnostic.md").write_text(loop.text)
        if loop.status != "passed":
            raise ValueError("Actual model/tool loop did not pass: " + loop.status)
        measured: list[dict[str, Any]] = []
        for arm in ("baseline", "candidate"):
            for seed in (41, 42, 43):
                job_id = f"{arm}-{seed}"
                request = ResearchJobRequest(job_id=job_id, attempt_id="attempt1", experiment_id=arm,
                    command_name="evaluate", config={"arm": arm}, seed=seed, steps=1)
                with bind_project_scope(project_scope), bind_research_execution(ResearchExecutionScope(ledger, "execution", "cpu-diagnostic-execution")):
                    jobs.submit(request, project_scope)
                deadline = time.monotonic() + 15
                while True:
                    view = jobs.status(job_id)
                    if view.receipt is not None:
                        break
                    if time.monotonic() >= deadline:
                        jobs.stop(job_id)
                        raise TimeoutError("Owned diagnostic CPU job did not finish")
                    await asyncio.sleep(0.05)
                if view.status != "completed" or view.budget_state != "settled":
                    raise ValueError("Actual CPU command did not complete and settle")
                measured.append({"job_id": job_id, "arm": arm, "seed": seed,
                    "mse": view.receipt["metrics"]["mse"], "duration_seconds": view.receipt["duration_seconds"]})
        with bind_research_execution(ResearchExecutionScope(ledger, "writing", "cpu-diagnostic-observations")):
            explanation = await guarded_complete(provider, [Message("user",
                "These are actual held-out MSE receipts for the authored synthetic workload described below. "
                "Briefly summarize the observed baseline/candidate comparison and limitations in Chinese. "
                "Do not claim statistical significance, general scientific validity or completed product acceptance. "
                + (source / "README.md").read_text() + "\nCandidate:\n"
                + (project_scope.candidate_root / "candidate.py").read_text()
                + "\nActual receipts:\n" + json.dumps(measured))], replace(config, tools=(), max_tokens=2048), run_root=run.root)
        (output / "measured_observation_summary.md").write_text(explanation.text)
    finally:
        await provider.close()
    results = collect_run_results(run)
    if len(results["experiments"]) != 6 or any(item["verification"] != "verified_local_receipt" for item in results["experiments"]):
        raise ValueError("Results did not verify all six actual jobs")
    resources = results["resources"]
    if (resources.get("authority") != "sqlite" or resources.get("status") != "recorded"
            or resources.get("usage_complete") is not True or resources.get("cost") is not None):
        raise ValueError("Results did not verify the actual contract model usage")
    exported = create_results_export(run)
    encoded = read_results_export(run, exported["export_id"])
    (output / "results.zip").write_bytes(encoded)
    snapshot = ledger.snapshot()
    if snapshot.unknown_reservations or snapshot.active_jobs or snapshot.used["implemented_candidates"] != 1:
        raise ValueError("Shared budget did not settle all admitted operations")
    if resources["observed_sdk_attempts"] != snapshot.used["model_requests"]:
        raise ValueError("Results and shared SDK accounting disagree")
    if (source / "candidate.py").read_text() != baseline or (source / "baseline.py").read_text() != baseline:
        raise ValueError("Original source changed")
    admission = research_execution_admission(run)
    assert admission is not None
    if source_fingerprints() != sources:
        raise ValueError("Diagnostic implementation changed while the real run was active")
    receipt = {"schema": "verification.contract_cpu_loop.v1", "status": "passed", "run_id": run.run_id,
        "task_sha256": frozen.task_sha256, "loop_counts": loop.counts,
        "actual_model": explanation.model, "response_model_status": explanation.raw.get("response_model_status"),
        "measurements": measured, "budget": snapshot.model_dump(mode="json"),
        "export_sha256": hashlib.sha256(encoded).hexdigest(), "export_bytes": len(encoded),
        "candidate_sha256": hashlib.sha256((project_scope.candidate_root / "candidate.py").read_bytes()).hexdigest(),
        "results_resources": resources,
        "legacy_model_ledger_created": (run.root / "resources/model_budget.v1.json").exists(),
        "original_source_unchanged": True, "research_execution_ready": admission.ready,
        "full_research_accepted": False, "os_execution_sandbox_certified": False,
        "source_fingerprints": sources, "selected_source_files_unchanged_during_run": True}
    atomic_write_json(output / "receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    try:
        report = asyncio.run(verify(options.output.resolve()))
    except Exception as exc:
        if options.output.is_dir() and not (options.output / "failure.json").exists():
            atomic_write_json(options.output / "failure.json", {"schema": "verification.contract_cpu_loop_failure.v1",
                "status": "failed", "error_type": type(exc).__name__, "full_research_accepted": False})
        logger.error("Contract CPU loop diagnostic failed: {}", type(exc).__name__)
        return 1
    logger.info("{}", json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
