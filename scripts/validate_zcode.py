"""Real ZCode + configured GLM coding acceptance; no model/tool substitutes.

Creates an isolated synthetic Git project, preserves its baseline, and uses
the same CodingAgent, ToolRegistry, schema and branch paths as product runs.
Run with PYTHONPATH=backend .venv/bin/python scripts/validate_zcode.py.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from loguru import logger
import yaml

from app.agents.base import ContextPack, RunRequest
from app.agents.coding.agent import CodingAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest as BridgeRunRequest
from app.bridge.research_branch import research_branch_scope
from app.harness.agent_loop.trace import atomic_json, audit_trace
from app.harness.project_workspace import open_folder
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.validator import validate_document
from app.harness.tools.git_branch import git, receipt_path
from app.hitl.review_session import get_registry as get_review_registry
from app.settings import repo_root, reset_settings_cache
from app.storage.run_store import RunHandle, RunStore


async def validate(*, interrupt: bool, bridge: bool = False) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S%f")
    root = repo_root() / "runs" / (stamp + "_zcode_acceptance")
    root.mkdir(parents=True)
    os.environ["MARS_CODING_BACKEND"] = "zcode"
    os.environ["MARS_FOLDER_PROJECTS_REGISTRY"] = str(root / "registry.json")
    reset_settings_cache()
    project = open_folder(str(root / "project"), create=True)
    source = root / "source"
    source.mkdir()
    (source / "tests").mkdir()
    (source / "baseline").mkdir()
    (source / "baseline/reference.py").write_text("REFERENCE = 'preserve'\n")
    (source / "main.py").write_text("def add(a, b):\n    return a - b\n")
    (source / "tests/test_main.py").write_text(
        "from main import add\n\ndef test_add():\n"
        "    assert add(2, 3) == 5\n    assert add(-2, 3) == 1\n    assert add(0, 0) == 0\n")
    git(source, "init", "-b", "baseline")
    git(source, "add", ".")
    git(source, "-c", "user.name=MARS validation", "-c", "user.email=mars@localhost",
        "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic coding acceptance baseline")
    baseline = git(source, "rev-parse", "HEAD")
    (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
        "repo_path": str(source), "read_only": True, "allowed_paths": ["main.py", "tests/"],
        "protected_paths": ["baseline/", "tests/"], "ignore_patterns": [".git/", ".env*"]}))
    # A real host-selected check configuration; model arguments cannot supply
    # a command. This process's environment does not change running services.
    execution = yaml.safe_load((repo_root() / "configs/execution.yaml").read_text())
    execution["code_checks"] = {"test": {"enabled": True, "commands": [{
        "id": "repo-tests", "label": "Actual project tests", "argv": ["python", "-m", "pytest", "-q", "tests"]}]}}
    execution_file = root / "execution.yaml"
    execution_file.write_text(yaml.safe_dump(execution))
    os.environ["MARS_EXECUTION_CONFIG_PATH"] = str(execution_file)
    run = RunHandle(root.name, root, project.name, "zcode_acceptance", "coding", stamp)
    request = RunRequest(project.name,
        "Fix add in main.py to return the correct sum. Only main.py may change. "
        "Read main.py and tests/test_main.py using MARS. Run code.test_runner with command_id repo-tests. "
        "Do not change tests or baseline. Deliver a code_spec.v1 with accurate files_changed and actual test evidence.",
        extra={"run_root": str(root), "run_id": run.run_id, "invocation_id": "zcodeacceptance"})
    context = ContextPack(system=CodingAgent.agent_brief,
        project="Approved scope: main.py only; preserve tests/ and baseline/; existing tests are host configured.",
        task=request.user_request)
    observed = asyncio.Event()

    async def progress(event: dict[str, object]) -> None:
        logger.info("ZCode acceptance progress: {}", event.get("kind"))
        if event.get("kind") == "observation":
            observed.set()

    request.progress_sink = progress
    interrupted = False
    bridge_review_status = "not exercised"
    if bridge:
        registry = AgentRegistry()
        registry.register("coding", CodingAgent())
        orchestrator = Orchestrator(run_store=RunStore(root / "product_runs"), registry=registry)
        session = orchestrator.create_session(BridgeRunRequest(task="zcode_bridge_acceptance", project=project.name,
            entrypoint="coding", standalone=True, user_request=request.user_request, auto_approve=False))
        root, run = session.run.root, session.run
        driver = asyncio.create_task(orchestrator.run(run.run_id))
        try:
            async with asyncio.timeout(180):
                while (session.graph.state("coding") != NodeState.WAITING_REVIEW
                       or get_review_registry().get(run.run_id, "coding") is None):
                    if driver.done():
                        await driver
                        raise ValueError("Real bridge did not reach coding review")
                    await asyncio.sleep(0.1)
            bridge_review_status = session.graph.state("coding").value
            review = get_review_registry().get(run.run_id, "coding")
            assert review is not None and review.decision is None and not review.approval_event.is_set()
            artifact_text = (root / "coding/code_spec.v1.md").read_text()
            assert not (root / "coding/code_spec.approved.md").exists()
            checkpoint = next((root / "agent_traces/coding").glob("*/checkpoint.json"))
        finally:
            # Never approve or advance into execution as part of this check.
            driver.cancel()
            with suppress(asyncio.CancelledError):
                await driver
    else:
        with research_branch_scope(run, "coding"):
            task = asyncio.create_task(CodingAgent().run_loop(request, context))
            if interrupt:
                await asyncio.wait_for(observed.wait(), 180)
                # Cancellation at an observation boundary, after the real receipt.
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                interrupted = True
                request.extra["resume_invocation"] = "zcodeacceptance"
                task = asyncio.create_task(CodingAgent().run_loop(request, context))
            artifact_text = (await task).text
        checkpoint = root / "agent_traces/coding/zcodeacceptance/checkpoint.json"
    check = validate_document(artifact_text, expected_schema="code_spec.v1")
    state = json.loads(checkpoint.read_text())
    history = state["history"]
    writes = [r for r in history if r["tool"] in {"code.write_file", "code.apply_patch"} and r["ok"]]
    tests = [r for r in history if r["tool"] == "code.test_runner" and r["ok"]]
    changed = git(source, "diff", "--name-only", "HEAD").splitlines()
    assert check.valid and state["status"] == "passed"
    assert changed == ["main.py"] and writes and tests
    assert git(source, "rev-parse", "baseline") == baseline
    assert git(source, "show", "baseline:main.py") == "def add(a, b):\n    return a - b"
    assert (source / "baseline/reference.py").read_text() == "REFERENCE = 'preserve'\n"
    assert audit_trace(checkpoint.parent)["consistent"]
    assert 0 < state["active_elapsed_seconds"] < 900
    assert not (checkpoint.parent / "zcode_runtime/provider.json").exists()
    (root / "coding").mkdir(exist_ok=True)
    if not bridge:
        (root / "coding/code_spec.v1.md").write_text(artifact_text)
    evidence = {"status": "passed", "real_runtime": "ZCode", "interrupted_and_resumed": interrupted,
        "schema_valid": check.valid, "counts": state["counts"], "usage": state["usage"],
        "usage_complete": state["usage_complete"], "session_id": state["session_id"],
        "changed_files": changed, "baseline_preserved": True,
        "branch_receipt": str(receipt_path(root)), "actual_test_receipts": tests,
        "model_budget_ledger": str(root / "resources/model_budget.v1.json"),
        "trace": str(checkpoint.parent), "human_review": "not approved", "execution": "not started",
        "bridge_review_status": bridge_review_status, "active_elapsed_seconds": state["active_elapsed_seconds"]}
    atomic_json(root / "zcode_acceptance.json", evidence)
    logger.info("Real ZCode acceptance passed: {}", root / "zcode_acceptance.json")
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interrupt-resume", action="store_true")
    parser.add_argument("--bridge", action="store_true", help="Use the real product orchestrator and stop at human review")
    args = parser.parse_args()
    if args.bridge and args.interrupt_resume:
        parser.error("Run the bridge and interruption checks separately")
    asyncio.run(validate(interrupt=args.interrupt_resume, bridge=args.bridge))
