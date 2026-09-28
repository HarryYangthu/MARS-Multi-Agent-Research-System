"""Exercise the real registered Coding Agent through its SQL-bound runner.

Developer diagnostic with a small authored project. Never opens product admission.
Credentials come only from the normal process/local credential configuration.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from loguru import logger

from app.agents.coding.agent import CodingAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.agent_runner import run_agent_node
from app.bridge.orchestrator import Orchestrator
from app.bridge.research_contract_service import default_research_budget, freeze_research_task
from app.bridge.research_project_scope import prepare_project_scope
from app.bridge.research_run_service import create_research_run, research_execution_admission
from app.bridge.research_scope_recovery import seal_project_scope
from app.bridge.research_stage_service import bind_research_stage
from app.harness.discovery.snapshots import SnapshotPolicy
from app.harness.persistence import atomic_write_json
from app.harness.runtime.project_scope import verify_candidate_scope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.research_contract import ProjectContract, ResearchBudget
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_journal import StateJournal
from app.harness.runtime.state_machine import NodeState
from app.harness.runtime.task_contract import ResultEnvelope
from app.harness.schema.validator import validate_document
from app.settings import repo_root
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


SOURCES = (
    "scripts/verify_bound_coding_stage.py", "backend/app/bridge/agent_runner.py",
    "backend/app/bridge/research_stage_runtime.py", "backend/app/bridge/research_stage_service.py",
    "backend/app/bridge/research_scope_recovery.py", "backend/app/agents/coding/agent.py",
    "backend/app/agents/base.py", "backend/app/harness/agent_loop/executor.py",
    "backend/app/harness/llm/research_accounting.py", "backend/app/harness/tools/research_accounting.py",
    "configs/agents.yaml", "configs/tools.yaml", "configs/models.yaml",
)


def fingerprints() -> dict[str, str]:
    return {name: hashlib.sha256((repo_root() / name).read_bytes()).hexdigest() for name in SOURCES}


async def verify(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    original_sources = fingerprints()
    atomic_write_json(output / "started_source_fingerprints.json", original_sources)
    source = output / "source"
    source.mkdir()
    (source / "baseline.py").write_text("def square(x):\n    return x * x\n")
    (source / "candidate.py").write_text("def square(x):\n    return x + x\n")
    project = ProjectContract.model_validate({"project_id": "bound_coding_probe", "display_name": "Real bound Coding diagnostic",
        "paths": {"code": str(source), "data": [], "knowledge": [], "output": str(output / "artifacts")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
            "arguments": ["baseline.py"], "entrypoint_files": ["baseline.py"]} for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "mse", "unit": "unitless", "direction": "minimize", "target": 0, "tolerance": 0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}})
    budget = ResearchBudget.model_validate({**default_research_budget().model_dump(), "model_requests": 6,
        "tool_executions": 8, "operation_retries": 0, "research_activity_seconds": 300,
        "billed_output_tokens": 32768})
    goal = ("This is a small authored Coding integration task, not a research result. Read candidate.py once "
        "using code.repo_reader, then use code.write_file to implement square(x) as x*x. Change only candidate.py. "
        "Do not search knowledge, generate a patch, execute a command or call a test tool. Tool execution permits "
        "only repository reads and authorized file writes here. Deliver code_spec.v1 for bound_coding_probe, "
        "files_changed contains candidate.py, baseline_compat.preserved=true. Include the actual change and "
        "mark all tests skipped because no test command has run. No experiments or success metrics exist. "
        "Return raw Markdown starting with YAML frontmatter; follow the native tool protocol.")
    frozen = freeze_research_task(project, goal=goal, mode="manual", budget=budget)
    run = create_research_run(Orchestrator(run_store=RunStore(output / "runs")), name="Bound Coding diagnostic", contract=frozen).run
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    assert journal is not None
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=budget)
    ledger.initialize()
    scope = prepare_project_scope(run, candidate_id="one", snapshot_policy=SnapshotPolicy(allowed_paths=("*",)))
    seal_project_scope(run, scope, ledger)
    # Explicit diagnostic graph, not Orchestrator product start or a full DAG.
    graph = RunGraph()
    graph.add_node("coding", metadata={"stage": "coding", "attempt": 1})
    prior = journal.read()
    RunStateStore(run).write(graph=graph, request=prior["request"], status="created", expected_revision=prior["revision"])
    task = bind_research_stage(run, node_key="coding", candidate_id="one", ledger=ledger, goal=goal,
        output_schema="code_spec.v1", host_context=("frozen_contract", "project_rules"))
    graph.restore_state("coding", NodeState.RUNNING)
    prior = journal.read()
    RunStateStore(run).write(graph=graph, request=prior["request"], status="running", expected_revision=prior["revision"])
    # These mutable legacy options must not select an unbound skill or review.
    options_path = run.root / "input/run_request_options.v1.json"
    options = json.loads(options_path.read_text())
    options["extra"].update({"selected_skills_by_agent": {"coding": ["unbound-diagnostic-skill"]},
                             "external_review": {"invalid_unsealed_option": True}})
    atomic_write_json(options_path, options)
    registry = AgentRegistry()
    agent = CodingAgent()
    if agent.config.model_provider != "zhipu" or agent.config.model_name != "glm-5.3" or agent.loop_policy.protocol != "native_tools":
        raise ValueError("Diagnostic requires actual configured GLM-5.3 and native Coding loop")
    registry.register("coding", agent)
    failure = None
    try:
        await asyncio.wait_for(run_agent_node(run, "coding", registry=registry), timeout=300)
    except Exception as exc:
        failure = type(exc).__name__
    with journal.connection() as connection:
        row = connection.execute("SELECT result FROM research_stage_dispatches WHERE invocation_id=?", (task.invocation_id,)).fetchone()
    result = ResultEnvelope.model_validate_json(row[0]) if row is not None and row[0] else None
    changes = verify_candidate_scope(scope)
    snapshot = ledger.snapshot()
    admission = research_execution_admission(run)
    assert admission is not None
    evidence: dict[str, Any] = {"run_id": run.run_id, "invocation_id": task.invocation_id, "failure_type": failure,
        "result": result.model_dump() if result else None, "changed_paths": [change.path for change in changes],
        "used": snapshot.used, "unknown_reservations": list(snapshot.unknown_reservations),
        "source_unchanged": (source / "candidate.py").read_text() == "def square(x):\n    return x + x\n",
        "sources_unchanged": fingerprints() == original_sources,
        "product_admission_ready": admission.ready,
        "full_research_acceptance": False, "experiment_executed": False}
    atomic_write_json(output / "receipt.json", evidence)
    if failure or result is None or result.status != "awaiting_review" or not evidence["sources_unchanged"] or not evidence["source_unchanged"]:
        raise ValueError("Actual bound Coding diagnostic did not pass; inspect durable receipt")
    assert result.artifact_ref is not None
    assert validate_document((run.root / result.artifact_ref).read_text(), expected_schema="code_spec.v1").valid
    assert evidence["changed_paths"] == ["candidate.py"] and int(snapshot.used["tool_executions"] or 0) >= 2
    assert int(snapshot.used["model_requests"] or 0) >= 2 and not snapshot.unknown_reservations
    assert not (run.root / "input/task_results").exists()
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = asyncio.run(verify(args.output.resolve()))
    logger.info("Bound Coding diagnostic passed: run={}, model_requests={}, tool_executions={}",
                evidence["run_id"], evidence["used"]["model_requests"], evidence["used"]["tool_executions"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
