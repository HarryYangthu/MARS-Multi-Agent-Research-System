#!/usr/bin/env python3
"""Run a real Commander-driven MARS e2e lane.

This is intentionally stricter than the mock acceptance demo:
- forces real LLM provider use (MARS_MOCK_MODE=never)
- forces a configured real execution backend (default: paper_static)
- sends the research topic to the Commander first
- uses Commander tools, not manual API calls, to approve each HITL node
- verifies schema-valid artifacts and non-mock execution results
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_ARTIFACTS: dict[str, tuple[str, str, str]] = {
    "idea": ("idea_proposal", "approved", "proposal.v1"),
    "experiment": ("experiment_plan", "approved", "experiment_plan.v1"),
    "coding": ("code_spec", "approved", "code_spec.v1"),
    "execution": ("run_log", "approved", "run_log.v1"),
    "writing": ("research_report", "approved", "report.v1"),
}


def _prepare_env(execution_backend: str) -> None:
    os.environ["MARS_MOCK_MODE"] = "never"
    os.environ["MARS_EXECUTION_BACKEND"] = execution_backend
    os.environ.setdefault("MARS_LLM_TIMEOUT_SECONDS", "120")
    os.environ.setdefault("MARS_ENABLE_NETWORK_TOOLS", "false")
    sys.path.insert(0, str(REPO_ROOT / "backend"))


async def _run(args: argparse.Namespace) -> int:
    _prepare_env(args.execution_backend)

    from app.bridge.commander import Commander
    from app.bridge.commander_session import get_session_store
    from app.bridge.commander_tools import ToolContext, execute_tool
    from app.bridge.node_key import parse_node_key
    from app.harness.schema.frontmatter_parser import parse as parse_fm
    from app.harness.schema.validator import validate_document
    from app.main import register_default_agents
    from app.settings import get_settings
    from app.storage.run_state_store import RunStateStore
    from app.api.dependencies import get_orchestrator, get_run_store

    settings = get_settings()
    if settings.mars_mock_mode != "never":
        raise RuntimeError(f"expected MARS_MOCK_MODE=never, got {settings.mars_mock_mode}")
    if settings.mars_execution_backend != args.execution_backend:
        raise RuntimeError(
            "expected MARS_EXECUTION_BACKEND="
            f"{args.execution_backend}, got {settings.mars_execution_backend}"
        )
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    register_default_agents()
    orchestrator = get_orchestrator()
    run_store = get_run_store()
    commander = Commander(orchestrator=orchestrator, run_store=run_store)
    session = get_session_store().create(project=args.project)
    session.auto_mode = False
    session.metric_targets["RES"] = -26.0
    ctx = ToolContext(orchestrator=orchestrator, session=session, run_store=run_store)

    question = _compose_question(args)
    print("[real-e2e] sending topic to Commander")
    await commander.handle_user_message(session, question)
    run_id = session.linked_run_id
    if not run_id:
        print("[real-e2e] Commander did not start a run; using Commander tool fallback")
        created = await execute_tool(
            "create_and_start_run",
            {
                "entrypoint": "pipeline",
                "task": args.task,
                "user_request": question,
            },
            ctx,
        )
        if not created.get("ok"):
            raise RuntimeError(f"Commander run creation failed: {created}")
        run_id = str(created["run_id"])
    print(f"[real-e2e] run_id={run_id}")
    if args.run_id_file:
        Path(args.run_id_file).write_text(run_id + "\n", encoding="utf-8")

    approved_nodes: set[str] = set()
    feedback_started_for: set[str] = set()
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        rsession = orchestrator.session(run_id)
        states = {node: state.value for node, state in rsession.graph.all_states().items()}
        failed = [node for node, state in states.items() if state == "failed"]
        if failed:
            raise RuntimeError(f"run failed at {failed}; states={states}")

        waiting = [node for node, state in states.items() if state == "waiting_review"]
        for node in waiting:
            agent = parse_node_key(node).stage
            artifact = _artifact_path(rsession.run.root, agent)
            if artifact is not None:
                schema_id = MAIN_ARTIFACTS[agent][2]
                result = validate_document(artifact.read_text(encoding="utf-8"), expected_schema=schema_id)
                if not result.valid:
                    raise RuntimeError(
                        f"{agent} artifact is not schema-valid before approval: {result.first_error()}"
                    )
            approval = await execute_tool(
                "approve_node",
                {"run_id": run_id, "agent": agent},
                ctx,
            )
            if approval.get("ok"):
                approved_nodes.add(node)
                print(f"[real-e2e] Commander approved {node}")

        if rsession.waiting_for_feedback:
            version = _latest_diagnosis_version(rsession.run.root)
            if version and version not in feedback_started_for:
                feedback = await execute_tool(
                    "run.feedback_loop",
                    {"run_id": run_id, "diagnosis_version": version},
                    ctx,
                )
                if not feedback.get("ok"):
                    raise RuntimeError(f"Commander feedback loop failed: {feedback}")
                feedback_started_for.add(version)
                print(f"[real-e2e] Commander started feedback loop from diagnosis.{version}.md")

        if all(state in {"done", "skipped"} for state in states.values()):
            break
        await asyncio.sleep(0.5)
    else:
        raise TimeoutError(f"run {run_id} did not finish within {args.timeout}s")

    final_session = orchestrator.session(run_id)
    final_states = {
        node: state.value for node, state in final_session.graph.all_states().items()
    }
    print("[real-e2e] final states=" + json.dumps(final_states, ensure_ascii=False))
    run_root = final_session.run.root
    _verify_run_artifacts(
        run_root,
        validate_document,
        parse_fm,
        expected_min_results=args.expected_min_results,
        expected_backend=args.execution_backend,
    )
    status = RunStateStore(final_session.run).load()
    print(
        "[real-e2e] verified "
        "schema artifacts and non-mock execution results; "
        f"run_status={status.status if status else 'unknown'}"
    )
    return 0


def _compose_question(args: argparse.Namespace) -> str:
    parts = [args.question.strip()]
    if args.task_context_file:
        path = Path(args.task_context_file).expanduser()
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        parts.append(
            "本次 run 使用以下可配置任务上下文；它只适用于当前 PIMC 静态算法研究任务，"
            "不要把它当成 MARS 全局默认：\n\n"
            + path.read_text(encoding="utf-8")
        )
    return "\n\n".join(part for part in parts if part)


def _artifact_path(run_root: Path, agent: str) -> Path | None:
    if agent not in MAIN_ARTIFACTS:
        return None
    stem, version, _schema = MAIN_ARTIFACTS[agent]
    path = run_root / agent / f"{stem}.{version}.md"
    if path.exists():
        return path
    versions = sorted((run_root / agent).glob(f"{stem}.v*.md"))
    return versions[-1] if versions else None


def _latest_diagnosis_version(run_root: Path) -> str:
    versions = sorted((run_root / "diagnosis").glob("diagnosis.v*.md"))
    if not versions:
        return ""
    match = re.search(r"\.(v[0-9]+)\.md$", versions[-1].name)
    return match.group(1) if match else ""


def _verify_run_artifacts(
    run_root: Path,
    validate_document: Any,
    parse_fm: Any,
    *,
    expected_min_results: int,
    expected_backend: str,
) -> None:
    for agent, (stem, version, schema_id) in MAIN_ARTIFACTS.items():
        path = run_root / agent / f"{stem}.{version}.md"
        if not path.exists():
            raise AssertionError(f"missing approved artifact: {path}")
        result = validate_document(path.read_text(encoding="utf-8"), expected_schema=schema_id)
        if not result.valid:
            raise AssertionError(f"{path.name} failed schema validation: {result.first_error()}")

    idea_meta = parse_fm((run_root / "idea" / "idea_proposal.approved.md").read_text(encoding="utf-8")).metadata
    writing_meta = parse_fm((run_root / "writing" / "research_report.approved.md").read_text(encoding="utf-8")).metadata
    if idea_meta.get("debate_mode") != "real_multi_model":
        raise AssertionError(f"Idea debate did not use real_multi_model: {idea_meta.get('debate_mode')}")
    if writing_meta.get("debate_mode") != "real_multi_model":
        raise AssertionError(f"Writing debate did not use real_multi_model: {writing_meta.get('debate_mode')}")

    metrics_path = run_root / "execution" / "metrics.json"
    rows = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) < expected_min_results:
        got = len(rows) if isinstance(rows, list) else "invalid"
        raise AssertionError(
            f"expected at least {expected_min_results} execution metrics rows, got {got}"
        )

    run_logs = sorted((run_root / "execution").glob("run_log_*.v1.md"))
    if len(run_logs) < expected_min_results:
        raise AssertionError(
            f"expected at least {expected_min_results} per-experiment run logs, got {len(run_logs)}"
        )
    for path in run_logs:
        result = validate_document(path.read_text(encoding="utf-8"), expected_schema="run_log.v1")
        if not result.valid:
            raise AssertionError(f"{path.name} failed schema validation: {result.first_error()}")
        metadata = parse_fm(path.read_text(encoding="utf-8")).metadata
        if metadata.get("is_mock") is not False:
            raise AssertionError(f"{path.name} is not a real {expected_backend} run")

    curve_files = sorted((run_root / "execution" / "curves").glob("*_loss.json"))
    if curve_files and len(curve_files) < expected_min_results:
        raise AssertionError(
            f"expected at least {expected_min_results} loss curve JSON files, got {len(curve_files)}"
        )
    for path in curve_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("values", [])
        min_points = 1 if expected_backend == "paper_static" else 100
        if not isinstance(values, list) or len(values) < min_points:
            raise AssertionError(f"{path.name} has too few loss points")

    live_plots = sorted((run_root / "execution" / "live_plots").glob("*_loss.png"))
    if expected_backend != "paper_static":
        if len(live_plots) < expected_min_results:
            raise AssertionError(
                f"expected at least {expected_min_results} live loss PNG files, got {len(live_plots)}"
            )
        for path in live_plots:
            if path.stat().st_size < 20_000:
                raise AssertionError(f"{path.name} looks too small to be a detailed plot")

        aggregate_plot = run_root / "execution" / "loss_curves_16.png"
        if not aggregate_plot.exists() or aggregate_plot.stat().st_size < 50_000:
            raise AssertionError("missing or undersized aggregate loss-curve plot")

    manifests = sorted((run_root / "context").glob("context_manifest.v2.*.json"))
    if len(manifests) < 5:
        raise AssertionError(f"expected at least 5 context v2 manifests, got {len(manifests)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="pimc")
    parser.add_argument("--task", default="pimc_static_algorithm_real_e2e")
    parser.add_argument(
        "--execution-backend",
        default="paper_static",
        choices=["pim_cpu", "paper_static", "local_command", "docker_command", "remote_gpu"],
    )
    parser.add_argument(
        "--task-context-file",
        default="projects/pimc/context/task_pimc_static_algorithm.md",
    )
    parser.add_argument("--expected-min-results", type=int, default=1)
    parser.add_argument(
        "--question",
        default=(
            "Run the full MARS pipeline for the PIMC research workbench. This "
            "specific run is a PIMC static algorithm research task, not a "
            "router/MoE-only task. Use the configured static project context, "
            "produce schema-valid artifacts, run the configured real static "
            "execution backend, and write the final report with the full "
            "execution process."
        ),
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--run-id-file", default="")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
