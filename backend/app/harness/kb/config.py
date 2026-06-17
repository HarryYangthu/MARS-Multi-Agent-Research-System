"""Memory v2 configuration helpers.

The memory profile is deliberately environment-driven so dev E2E can remain
mock-friendly while research/hardware runs fail closed on eval gates.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Literal

import yaml

from app.settings import repo_root

MemoryProfile = Literal["dev_e2e", "research", "hardware"]


@lru_cache(maxsize=1)
def load_memory_config() -> dict[str, Any]:
    path = repo_root() / "configs" / "memory.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def current_profile() -> MemoryProfile:
    raw = os.environ.get("MARS_MEMORY_PROFILE", "dev_e2e").strip()
    if raw in {"dev_e2e", "research", "hardware"}:
        return raw  # type: ignore[return-value]
    return "dev_e2e"


def write_gate(profile: MemoryProfile | None = None) -> dict[str, Any]:
    cfg = load_memory_config()
    gates = cfg.get("write_gates", {})
    selected = profile or current_profile()
    raw = gates.get(selected, {}) if isinstance(gates, dict) else {}
    return dict(raw) if isinstance(raw, dict) else {}


def mock_policy() -> dict[str, Any]:
    raw = load_memory_config().get("mock_policy", {})
    return dict(raw) if isinstance(raw, dict) else {}


def lifecycle_config() -> dict[str, Any]:
    raw = load_memory_config().get("lifecycle", {})
    return dict(raw) if isinstance(raw, dict) else {}


def selector_config() -> dict[str, Any]:
    raw = load_memory_config().get("selector", {})
    return dict(raw) if isinstance(raw, dict) else {}


def backend_store() -> str:
    raw = load_memory_config().get("backend", {})
    if isinstance(raw, dict):
        return str(raw.get("store", "file") or "file")
    return "file"


def reset_config_cache_for_tests() -> None:
    load_memory_config.cache_clear()
