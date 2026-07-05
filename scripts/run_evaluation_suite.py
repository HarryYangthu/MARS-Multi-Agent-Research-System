#!/usr/bin/env python
"""Evaluate multiple existing MARS runs as trials in one suite."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.harness.evaluation.run_report import evaluate_run_replay
from app.harness.evaluation.suite_report import SuiteTrialResult, write_suite_report
from app.harness.evaluation.suites import load_suite
from app.main import create_app
from app.settings import repo_root
from app.storage.run_store import RunStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default=None, help="Evaluation suite YAML path.")
    parser.add_argument("--run-id", action="append", default=[], help="Run id to evaluate. Repeat for multiple trials.")
    parser.add_argument("--run-ids-file", default=None, help="Text file with one run id per line.")
    parser.add_argument("--runs-root", default=None, help="Optional runs root.")
    parser.add_argument("--output-dir", default=None, help="Suite report output directory.")
    parser.add_argument("--live", action="store_true", help="Create fresh runs from suite tasks before evaluating.")
    parser.add_argument("--trials", type=int, default=None, help="Override live trial count per task.")
    parser.add_argument("--timeout-seconds", type=float, default=240.0, help="Live trial timeout.")
    args = parser.parse_args()

    run_ids = list(args.run_id)
    if args.run_ids_file:
        run_ids.extend(_read_run_ids(Path(args.run_ids_file)))
    suite = load_suite(Path(args.suite)) if args.suite else load_suite()
    runs_root = Path(args.runs_root) if args.runs_root else repo_root() / "runs"
    if args.live:
        live_ids = asyncio.run(
            _run_live_trials(
                suite=suite,
                trial_override=args.trials,
                timeout_seconds=args.timeout_seconds,
            )
        )
        run_ids.extend(live_ids)
    if not run_ids:
        parser.error("provide --live, at least one --run-id, or --run-ids-file")
    store = RunStore(runs_root=runs_root)
    trials: list[SuiteTrialResult] = []
    for run_id in run_ids:
        run = store.get(run_id)
        if run is None:
            parser.error(f"run not found: {run_id}")
        evaluation = evaluate_run_replay(run=run, suite=suite)
        trials.append(SuiteTrialResult(run=run, evaluation=evaluation))
    result = write_suite_report(
        suite=suite,
        trials=trials,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    print(result.report_markdown_path)
    print(result.report_json_path)
    print(result.scorecard_path)
    print(result.self_evolution_export_path)
    return 0


def _read_run_ids(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


async def _run_live_trials(
    *,
    suite: Any,
    trial_override: int | None,
    timeout_seconds: float,
) -> list[str]:
    tasks = suite.tasks
    if not tasks:
        raise ValueError("live mode requires at least one task in the evaluation suite")
    app = create_app()
    transport = ASGITransport(app=app)
    run_ids: list[str] = []
    async with AsyncClient(transport=transport, base_url="http://mars.eval") as client:
        for task in tasks:
            trial_count = trial_override if trial_override is not None else task.trials
            for trial_index in range(1, trial_count + 1):
                payload = {
                    "task": f"{task.task}_eval_trial_{trial_index}",
                    "project": task.project,
                    "entrypoint": task.entrypoint,
                    "standalone": task.standalone,
                    "user_request": task.user_request,
                    "auto_approve": True,
                }
                detail = await _api_json(client, "POST", "/api/runs", payload)
                run_id = str(detail["run_id"])
                run_ids.append(run_id)
                _write_environment_record(run_id=run_id, task_id=task.id, trial_index=trial_index)
                await _api_json(client, "POST", f"/api/runs/{run_id}/start")
                await _wait_complete(client, run_id, timeout_seconds=timeout_seconds)
    return run_ids


async def _api_json(
    client: AsyncClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    response = await client.request(method, path, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    if not response.content:
        return {}
    return response.json()


async def _wait_complete(
    client: AsyncClient,
    run_id: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_states: dict[str, str] = {}
    while time.monotonic() < deadline:
        detail = await _api_json(client, "GET", f"/api/runs/{run_id}")
        raw_states = detail.get("states", {})
        if isinstance(raw_states, dict):
            last_states = {str(k): str(v) for k, v in raw_states.items()}
            if last_states and all(state in {"done", "skipped"} for state in last_states.values()):
                return
        await asyncio.sleep(0.25)
    raise TimeoutError(
        f"run {run_id} did not complete within {timeout_seconds}s; states={json.dumps(last_states, ensure_ascii=False)}"
    )


def _write_environment_record(*, run_id: str, task_id: str, trial_index: int) -> None:
    run_root = repo_root() / "runs" / run_id
    events = run_root / "events"
    events.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "evaluation_environment_isolation.v1",
        "run_id": run_id,
        "task_id": task_id,
        "trial_index": trial_index,
        "fresh_run_id": True,
        "reused_prior_run": False,
        "driver": "scripts/run_evaluation_suite.py --live",
        "notes": [
            "Each live trial is created through the normal MARS run API.",
            "Tool, gate, context, artifact, and event traces are regenerated per run.",
        ],
    }
    (events / "evaluation_environment.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
