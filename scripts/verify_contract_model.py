"""One real GLM request through a frozen-contract SQLite model budget.

This explicit developer diagnostic does not start or certify the research DAG.
Credentials must already be supplied in the process environment.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import secrets
import sys
from typing import Any

import yaml

from app.bridge.orchestrator import Orchestrator
from app.bridge.research_contract_service import freeze_research_task
from app.bridge.research_run_service import create_research_run, research_execution_admission
from app.harness.llm.accounting import ResourceBudgetError, guarded_complete
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Message
from app.harness.persistence import atomic_write_json
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.research_contract import ProjectContract, ResearchBudget
from app.harness.runtime.research_execution_scope import ResearchExecutionScope, bind_research_execution
from app.harness.runtime.state_journal import StateJournal
from app.settings import repo_root
from app.storage.run_store import RunStore


async def verify(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    code = output / "source"
    code.mkdir()
    (code / "baseline.py").write_text("# Immutable authored input for a model-budget diagnostic.\n")
    (code / "command.py").write_text("raise RuntimeError('This diagnostic does not run experiments')\n")
    project = ProjectContract.model_validate({"project_id": "contract_model_probe", "display_name": "Model budget diagnostic",
        "paths": {"code": str(code), "data": [], "knowledge": [], "output": str(output / "artifacts")},
        "commands": [{"name": kind, "purpose": kind, "executable": sys.executable,
                      "arguments": ["command.py"], "entrypoint_files": ["command.py"]}
                     for kind in ("check", "train", "evaluate")],
        "metrics": [{"name": "mse", "unit": "unitless", "direction": "minimize", "target": 0, "tolerance": 0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}})
    defaults = yaml.safe_load((repo_root() / "configs/research_defaults.yaml").read_text())["budget"]
    budget = ResearchBudget.model_validate({**defaults, "model_requests": 2, "operation_retries": 0})
    frozen = freeze_research_task(project, goal="Verify one real contract-accounted model request", mode="manual", budget=budget)
    owner = Orchestrator(run_store=RunStore(output / "runs"))
    session = create_research_run(owner, name="Contract model accounting probe", contract=frozen)
    journal = StateJournal.from_authority(session.run.root, run_id=session.run.run_id)
    assert journal is not None
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget)
    ledger.initialize()
    configured = get_agent_config("idea")
    if configured.model_provider != "zhipu" or not configured.model_name.lower().startswith("glm-5.3"):
        raise ValueError("the actual configured provider must be GLM-5.3")
    provider, config = select_provider(replace(configured, max_tokens=1024, max_retries=0))
    nonce = secrets.token_hex(12)
    messages = [Message("user", "Return only this public diagnostic nonce: " + nonce)]
    scope = ResearchExecutionScope(ledger, "idea", "contract-model-diagnostic")
    try:
        with bind_research_execution(scope):
            completion = await guarded_complete(provider, messages, config, run_root=session.run.root)
            if completion.text.strip() != nonce:
                raise ValueError("actual model response did not return the diagnostic nonce")
            duplicate_blocked = False
            try:
                await guarded_complete(provider, messages, config, run_root=session.run.root)
            except ResourceBudgetError:
                duplicate_blocked = True
            if not duplicate_blocked:
                raise ValueError("an identical model request was unexpectedly replayed")
    finally:
        await provider.close()
    snapshot = ledger.snapshot()
    if snapshot.used["model_requests"] != 1 or snapshot.unknown_reservations:
        raise ValueError("actual SDK request count was not settled exactly once")
    admission = research_execution_admission(session.run)
    assert admission is not None
    receipt = {"schema": "verification.contract_model.v1", "status": "passed",
        "provider": completion.provider, "actual_model": completion.model,
        "response_model_status": completion.raw.get("response_model_status"),
        "run_id": session.run.run_id, "task_sha256": frozen.task_sha256,
        "nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest(),
        "duplicate_request_blocked": duplicate_blocked, "snapshot": snapshot.model_dump(mode="json"),
        "legacy_model_ledger_created": (session.run.root / "resources/model_budget.v1.json").exists(),
        "research_execution_ready": admission.ready,
        "source_fingerprints": {name: hashlib.sha256((repo_root() / name).read_bytes()).hexdigest() for name in (
            "backend/app/harness/runtime/research_budget_ledger.py",
            "backend/app/harness/runtime/research_execution_scope.py",
            "backend/app/harness/llm/accounting.py", "backend/app/harness/llm/research_accounting.py",
            "backend/app/harness/llm/openai_provider.py", "backend/app/harness/llm/provider_base.py",
            "scripts/verify_contract_model.py")}, "full_research_accepted": False}
    atomic_write_json(output / "receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    from loguru import logger
    logger.info("{}", json.dumps(asyncio.run(verify(arguments.output.resolve())), ensure_ascii=False))
