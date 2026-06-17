#!/usr/bin/env python3
"""Verify Tools V1 invariants against a completed mock demo run."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib import parse, request

REPO_ROOT = Path(__file__).resolve().parents[1]
_NO_PROXY_OPENER = request.build_opener(request.ProxyHandler({}))

REQUIRED_TOOLS = {
    "search.arxiv_search",
    "search.web_search",
    "search.local_docs",
    "knowledge.baseline_match",
    "code.apply_patch",
    "code.write_file",
    "code.delete_file",
    "code.rollback_patch",
    "execution.batch_runner",
    "execution.simulation_runner",
    "run.create",
    "run.status",
    "artifact.review",
    "metrics.evaluate",
    "diagnosis.failure_analysis",
    "user.approval",
}


def _http_json(url: str) -> Any:
    req = request.Request(url, method="GET")
    with _NO_PROXY_OPENER.open(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def _http_getter(base: str) -> Callable[[str], Any]:
    clean_base = base.rstrip("/")

    def _get(path: str) -> Any:
        return _http_json(f"{clean_base}{path}")

    return _get


def _in_process_getter() -> Callable[[str], Any]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())

    def _get(path: str) -> Any:
        response = client.get(path)
        if response.status_code >= 400:
            raise AssertionError(f"GET {path} failed: {response.status_code} {response.text}")
        return response.json() if response.content else {}

    return _get


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise AssertionError(f"missing JSONL file: {path}")
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_local_registry() -> None:
    from app.harness.llm.model_registry import list_agent_configs
    from app.harness.tools.config import load_tool_configs
    from app.harness.tools.registry import reset_for_tests

    registry = reset_for_tests()
    configs = load_tool_configs()
    registered = set(registry.names())
    catalogue = {spec.name: spec for spec in registry.specs(include_bridge_only=True)}

    _assert(REQUIRED_TOOLS.issubset(catalogue), "required Tools V1 specs are missing")
    _assert(
        not [name for name in catalogue if name not in configs],
        "registered tools must be represented in configs/tools.yaml",
    )
    _assert(
        not [name for name, cfg in configs.items() if name not in registered and not cfg.bridge_only],
        "configs/tools.yaml contains unregistered non-bridge tools",
    )
    for agent in list_agent_configs():
        for tool_name in agent.tools:
            cfg = configs.get(tool_name)
            _assert(
                registry.has(tool_name) or (cfg is not None and cfg.bridge_only),
                f"configs/agents.yaml references unknown tool {agent.name}:{tool_name}",
            )

    _assert(configs["search.arxiv_search"].enabled, "arXiv search should be config-enabled")
    _assert(not configs["search.web_search"].enabled, "web_search should stay disabled by default")
    _assert(catalogue["search.arxiv_search"].policy.network, "arXiv must be marked network=true")
    _assert(catalogue["search.web_search"].policy.network, "web_search must be marked network=true")
    print("  ✓ local registry/config catalogue")


def _check_api_catalogue(get_json: Callable[[str], Any]) -> None:
    tools = get_json("/api/tools")
    _assert(isinstance(tools, list), "/api/tools should return a list")
    by_name = {str(item.get("name")): item for item in tools if isinstance(item, dict)}
    missing = sorted(REQUIRED_TOOLS - set(by_name))
    _assert(not missing, f"/api/tools missing required tools: {missing}")
    for name in REQUIRED_TOOLS:
        spec = by_name[name]
        policy = spec.get("policy", {})
        _assert(isinstance(policy, dict), f"{name} policy should be an object")
        _assert(policy.get("mutation_level") in {"read", "write"}, f"{name} mutation_level invalid")
        _assert(float(policy.get("timeout_seconds", 0)) > 0, f"{name} timeout missing")
    _assert(by_name["run.status"].get("bridge_only") is True, "run.status should be bridge-only")
    _assert(by_name["search.arxiv_search"]["policy"].get("network") is True, "arXiv network flag missing")
    print("  ✓ /api/tools catalogue")


def _check_run_tool_audit(get_json: Callable[[str], Any], run_id: str) -> None:
    run_root = REPO_ROOT / "runs" / run_id
    _assert(run_root.is_dir(), f"run root missing: {run_root}")

    tool_events = _read_jsonl(run_root / "events" / "tool_events.jsonl")
    tool_calls = _read_jsonl(run_root / "events" / "tool_calls.jsonl")
    execution_events = [
        item for item in tool_events if item.get("tool") == "execution.batch_runner"
    ]
    _assert(
        any(item.get("event") == "tool.started" for item in execution_events),
        "execution.batch_runner missing tool.started event",
    )
    _assert(
        any(item.get("event") == "tool.completed" and item.get("status") == "success" for item in execution_events),
        "execution.batch_runner missing successful tool.completed event",
    )
    _assert(
        any(item.get("tool") == "execution.batch_runner" and item.get("status") == "success" for item in tool_calls),
        "execution.batch_runner missing successful tool_calls audit row",
    )

    params = parse.urlencode({"tool": "execution.batch_runner", "status": "success"})
    filtered = get_json(f"/api/runs/{run_id}/tools?{params}")
    _assert(isinstance(filtered, list) and filtered, "tool audit API filter returned no rows")
    _assert(
        all(item.get("tool") == "execution.batch_runner" and item.get("status") == "success" for item in filtered),
        "tool audit API filter returned unrelated rows",
    )
    limited = get_json(f"/api/runs/{run_id}/tools?limit=1")
    _assert(isinstance(limited, list) and len(limited) == 1, "tool audit limit=1 failed")
    print("  ✓ run tool audit events/API filters")


def _check_trace_and_artifacts(get_json: Callable[[str], Any], run_id: str) -> None:
    run_root = REPO_ROOT / "runs" / run_id
    trace = get_json(f"/api/traces/{run_id}")
    spans = trace.get("spans", []) if isinstance(trace, dict) else []
    _assert(
        any(
            isinstance(span, dict)
            and span.get("name") == "tool:execution.batch_runner"
            and span.get("kind") == "tool"
            and span.get("status") == "ok"
            for span in spans
        ),
        "trace manifest missing successful execution.batch_runner tool span",
    )

    execution_dir = run_root / "execution"
    _assert((execution_dir / "metrics.json").is_file(), "execution/metrics.json missing")
    _assert((execution_dir / "batch_summary.json").is_file(), "execution/batch_summary.json missing")
    _assert(any((execution_dir / "curves").glob("*_loss.json")), "execution curves missing")
    _assert(any(execution_dir.glob("run_log_*.v1.md")), "per-experiment run logs missing")
    print("  ✓ trace span and execution artifacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--in-process", action="store_true")
    args = parser.parse_args(argv)
    get_json = _in_process_getter() if args.in_process else _http_getter(args.base)

    try:
        _check_local_registry()
        _check_api_catalogue(get_json)
        _check_run_tool_audit(get_json, args.run_id)
        _check_trace_and_artifacts(get_json, args.run_id)
    except Exception as exc:
        print(f"  ✗ Tools V1 acceptance failed: {exc}", file=sys.stderr)
        return 1
    print("  ✓ Tools V1 acceptance checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
