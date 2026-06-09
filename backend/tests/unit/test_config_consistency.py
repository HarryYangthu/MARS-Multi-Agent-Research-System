"""Cross-config invariants that have silently drifted before.

These guard the relationships *between* YAML config files and the code that
consumes them — the kind of mismatch unit tests on a single module miss.
"""
from __future__ import annotations

from typing import Any

import yaml

from app.harness.gates.baseline_compatibility import GATE_ID, monitored_tools
from app.settings import repo_root


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((repo_root() / "configs" / name).read_text("utf-8")) or {}


def test_gate5_monitored_tools_are_registered() -> None:
    """Every tool Gate 5 screens must exist in tools.yaml, else the guard
    points at a tool that can never be dispatched."""
    registered = set((_load("tools.yaml").get("tools", {}) or {}).keys())
    for tool in monitored_tools():
        assert tool in registered, f"Gate 5 monitors unregistered tool '{tool}'"


def test_gate5_code_reads_monitored_tools_from_yaml() -> None:
    """The enforced list (monitored_tools()) must match gates.yaml, not a
    stale hardcoded copy."""
    bc = (_load("gates.yaml").get("gates", {}) or {}).get(GATE_ID, {}) or {}
    assert set(monitored_tools()) == set(bc.get("monitored_tools", []))


def test_execution_yaml_present_and_parsed() -> None:
    """Concurrency must come from configs/execution.yaml, not a hardcoded
    literal in code."""
    from app.execution.config import get_execution_config

    cfg = get_execution_config()
    raw = _load("execution.yaml")
    assert raw, "configs/execution.yaml is missing"
    assert cfg.max_concurrency == int(raw["concurrency"]["max_concurrent"])
