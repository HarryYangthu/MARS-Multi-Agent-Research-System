#!/usr/bin/env python3
"""Socket-free MARS demo for acceptance environments.

This script drives the same REST routes as ``run_demo.py`` through an ASGI
transport, so it exercises FastAPI routing without binding a localhost port.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


STEM_BY_AGENT = {
    "idea": "idea_proposal",
    "experiment": "experiment_plan",
    "coding": "code_spec",
    "execution": "run_log",
    "writing": "research_report",
}


async def _api_json(
    client: AsyncClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    response = await client.request(method, path, json=payload)
    if response.status_code >= 400:
        sys.stderr.write(f"HTTP {response.status_code}: {response.text}\n")
        response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


async def _wait_until_state(
    client: AsyncClient,
    run_id: str,
    agent: str,
    state: str,
    *,
    timeout: float = 60.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = await _api_json(client, "GET", f"/api/runs/{run_id}")
        if detail["states"].get(agent) == state:
            return
        await asyncio.sleep(0.2)
    raise TimeoutError(f"agent {agent} did not reach {state}")


async def _approve(client: AsyncClient, run_id: str, agent: str, stem: str) -> None:
    await _api_json(client, "POST", f"/api/artifacts/{run_id}/{agent}/{stem}/v1/approve")


def _step(n: int, msg: str) -> None:
    print(f"\n[Step {n}] {msg}")


def _assert_run_dirs(run_id: str) -> None:
    run_dir = REPO_ROOT / "runs" / run_id
    if not run_dir.exists():
        raise AssertionError(f"runs dir missing: {run_dir}")
    expected_subdirs = (
        "input",
        "context",
        "idea",
        "experiment",
        "coding",
        "execution",
        "writing",
        "hitl",
        "events",
    )
    populated: list[str] = []
    empty: list[str] = []
    for sub in expected_subdirs:
        path = run_dir / sub
        if path.exists() and any(path.iterdir()):
            populated.append(sub)
        else:
            empty.append(sub)
    print(f"        populated subdirs: {populated}")
    if empty:
        raise AssertionError(f"empty subdirs: {empty}")


async def _assert_context_api(client: AsyncClient, run_id: str) -> None:
    payload = await _api_json(client, "GET", f"/api/context/runs/{run_id}")
    manifest_count = int(payload.get("budget_summary", {}).get("manifest_count", 0))
    if manifest_count < 5:
        raise AssertionError(f"context workbench API returned only {manifest_count} manifests")
    if not payload.get("manifests"):
        raise AssertionError("context workbench API returned no manifest summaries")
    print(f"        context workbench API manifests: {manifest_count}")


async def _run_demo(args: argparse.Namespace) -> int:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://mars.test") as client:
        _step(1, "User clicks Pipeline card on the front-end (simulated by API call)")
        _step(2, "Select project: pimc")
        _step(3, "Enter research question:")
        user_request = (
            "How can PIMC further reduce compute under 8L config while preserving "
            "RES performance?"
        )
        print(f"        {user_request}")

        _step(4, "Click Start Run")
        detail = await _api_json(
            client,
            "POST",
            "/api/runs",
            {
                "task": args.task,
                "project": "pimc",
                "entrypoint": "pipeline",
                "user_request": user_request,
            },
        )
        run_id = detail["run_id"]
        print(f"        run_id = {run_id}")
        if args.run_id_file:
            Path(args.run_id_file).write_text(f"{run_id}\n", encoding="utf-8")

        await _api_json(client, "POST", f"/api/runs/{run_id}/start")

        _step(5, "Idea Agent runs (multi-model debate auto-degrades to mock_debate)")
        await _wait_until_state(client, run_id, "idea", "waiting_review")

        _step(
            6,
            "HITL: review draft -> approve -> idea_proposal.approved.md (Gate 1 passes)",
        )
        await _approve(client, run_id, "idea", STEM_BY_AGENT["idea"])

        _step(7, "Experiment Agent runs -> baseline_match -> ablation matrix")
        await _wait_until_state(client, run_id, "experiment", "waiting_review")
        await _approve(client, run_id, "experiment", STEM_BY_AGENT["experiment"])

        _step(8, "Coding Agent runs -> patch_generator -> Gate 5 baseline check")
        await _wait_until_state(client, run_id, "coding", "waiting_review")
        await _approve(client, run_id, "coding", STEM_BY_AGENT["coding"])

        _step(9, "Execution Agent runs -> mock simulations (<=16 concurrent) + curves")
        await _wait_until_state(client, run_id, "execution", "waiting_review", timeout=120.0)
        await _approve(client, run_id, "execution", STEM_BY_AGENT["execution"])

        _step(10, "Writing Agent runs -> reviewer critique synthesis -> report")
        await _wait_until_state(client, run_id, "writing", "waiting_review", timeout=60.0)
        await _approve(client, run_id, "writing", STEM_BY_AGENT["writing"])

        _step(11, "Final state - runs/<id> has 9 populated subdirs")
        deadline = time.monotonic() + 30.0
        states: dict[str, str] = {}
        while time.monotonic() < deadline:
            final = await _api_json(client, "GET", f"/api/runs/{run_id}")
            states = final["states"]
            if all(state in ("done", "skipped") for state in states.values()):
                break
            await asyncio.sleep(0.2)
        print("        states =", json.dumps(states, indent=8))
        if not all(state in ("done", "skipped") for state in states.values()):
            print("        NOT all done - demo failed", file=sys.stderr)
            return 1

        _assert_run_dirs(run_id)
        await _assert_context_api(client, run_id)
        print("\n[demo] DONE - run_id:", run_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-mode", action="store_true")
    parser.add_argument("--task", default="pimc_demo")
    parser.add_argument("--run-id-file", default=None)
    args = parser.parse_args(argv)
    return asyncio.run(_run_demo(args))


if __name__ == "__main__":
    sys.exit(main())
