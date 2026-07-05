#!/usr/bin/env python3
"""End-to-end MARS V0 demo (ACCEPTANCE.md §2 — 11 step main script).

Runs against a live backend on http://localhost:8000 (override with --base).
With ``--mock-mode`` the demo doesn't need any real LLM key or GPU — Mars's
mock_provider + mock_simulation cover everything.

Usage:
    python scripts/run_demo.py                # against localhost:8000
    python scripts/run_demo.py --port 8765    # against localhost:8765
    python scripts/run_demo.py --mock-mode    # explicit, no-op (everything is mock-ready)
    python scripts/run_demo.py --base http://other:9000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parents[1]
_NO_PROXY_OPENER = request.build_opener(request.ProxyHandler({}))


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = request.Request(url, data=data, method=method, headers=headers)
    try:
        with _NO_PROXY_OPENER.open(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except error.HTTPError as exc:
        sys.stderr.write(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')}\n")
        raise
    except error.URLError as exc:
        sys.stderr.write(f"URL error: {exc}\n")
        raise


def _wait_until_state(
    base: str, run_id: str, agent: str, state: str, *, timeout: float = 60.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = _http_json("GET", f"{base}/api/runs/{run_id}")
        if d["states"].get(agent) == state:
            return
        time.sleep(0.5)
    raise TimeoutError(f"agent {agent} did not reach {state}")


def _approve(base: str, run_id: str, agent: str, stem: str) -> None:
    _http_json(
        "POST",
        f"{base}/api/artifacts/{run_id}/{agent}/{stem}/v1/approve",
    )


def _step(n: int, msg: str) -> None:
    print(f"\n[Step {n}] {msg}")


STEM_BY_AGENT = {
    "idea": "idea_proposal",
    "experiment": "experiment_plan",
    "coding": "code_spec",
    "execution": "run_log",
    "writing": "research_report",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("MARS_DEMO_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--port", type=int, default=None, help="override port on 127.0.0.1")
    parser.add_argument(
        "--mock-mode",
        action="store_true",
        help=(
            "Acknowledge that we're running with no real LLM/GPU. The runtime "
            "auto-falls-back to mock; this flag is for documentation."
        ),
    )
    parser.add_argument(
        "--task",
        default="pimc_demo",
        help="task slug for the demo run (becomes the runs/<...> directory name)",
    )
    parser.add_argument(
        "--run-id-file",
        default=None,
        help="optional file path where the created run_id is written",
    )
    args = parser.parse_args(argv)
    base = f"http://127.0.0.1:{args.port}" if args.port else args.base.rstrip("/")

    _step(1, "User clicks Pipeline card on the front-end (simulated by API call)")
    _step(2, "Select project: pimc")
    _step(3, "Enter research question:")
    user_request = (
        "How can PIMC further reduce compute under 8L config while preserving "
        "RES performance?"
    )
    print(f"        {user_request}")

    _step(4, "Click Start Run")
    detail = _http_json(
        "POST",
        f"{base}/api/runs",
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
    _http_json("POST", f"{base}/api/runs/{run_id}/start")

    _step(5, "Idea Agent runs (multi-model debate auto-degrades to mock_debate)")
    _wait_until_state(base, run_id, "idea", "waiting_review")

    _step(
        6,
        "HITL: review draft → approve → idea_proposal.approved.md (Gate 1 plan_finalized passes)",
    )
    _approve(base, run_id, "idea", "idea_proposal")

    _step(
        7,
        "Experiment Agent runs → baseline_match → ablation matrix in experiment_plan",
    )
    _wait_until_state(base, run_id, "experiment", "waiting_review")
    _approve(base, run_id, "experiment", "experiment_plan")

    _step(8, "Coding Agent runs → patch_generator → Gate 5 baseline_compatibility check")
    _wait_until_state(base, run_id, "coding", "waiting_review")
    _approve(base, run_id, "coding", "code_spec")

    _step(9, "Execution Agent runs → mock simulations (≤16 concurrent) + curves")
    _wait_until_state(base, run_id, "execution", "waiting_review", timeout=120.0)
    _approve(base, run_id, "execution", "run_log")

    _step(10, "Writing Agent runs (debate → reviewer critique synthesis) → report")
    _wait_until_state(base, run_id, "writing", "waiting_review", timeout=60.0)
    _approve(base, run_id, "writing", "research_report")

    _step(11, "Final state — runs/<id>/ has 9 populated subdirs")
    # Wait for all stages to reach "done" (orchestrator may still be
    # transitioning right after the last approve).
    final_deadline = time.monotonic() + 30.0
    states: dict[str, str] = {}
    while time.monotonic() < final_deadline:
        final = _http_json("GET", f"{base}/api/runs/{run_id}")
        states = final["states"]
        if all(s in ("done", "skipped") for s in states.values()):
            break
        time.sleep(0.5)
    print("        states =", json.dumps(states, indent=8))
    if not all(s in ("done", "skipped") for s in states.values()):
        print("        NOT all done — demo failed", file=sys.stderr)
        return 1

    run_dir = REPO_ROOT / "runs" / run_id
    if not run_dir.exists():
        print(f"        runs dir missing: {run_dir}", file=sys.stderr)
        return 2
    expected_subdirs = ("input", "context", "idea", "experiment", "coding", "execution", "writing", "hitl", "events")
    populated = []
    empty = []
    for sub in expected_subdirs:
        p = run_dir / sub
        if not p.exists():
            empty.append(sub)
            continue
        if any(p.iterdir()):
            populated.append(sub)
        else:
            empty.append(sub)
    print(f"        populated subdirs: {populated}")
    if empty:
        print(f"        empty subdirs: {empty}", file=sys.stderr)

    print("\n[demo] DONE — run_id:", run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
