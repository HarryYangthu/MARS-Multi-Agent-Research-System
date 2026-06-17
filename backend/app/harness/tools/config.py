"""Shared config readers for tool implementations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.settings import get_settings, repo_root


@dataclass(frozen=True)
class ToolConfig:
    enabled: bool = True
    adapter: str = "local"
    mutation_level: str = "read"
    allowed_agents: tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    requires_approval: bool = False
    network: bool = False
    bridge_only: bool = False
    command_allowlist: tuple[tuple[str, ...], ...] = ()
    redaction: tuple[str, ...] = ()
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    description: str = ""


@dataclass(frozen=True)
class CommandSpec:
    id: str
    label: str
    argv: tuple[str, ...]


def load_tool_configs() -> dict[str, ToolConfig]:
    path = repo_root() / "configs" / "tools.yaml"
    raw = _load_yaml(path)
    tools_raw = raw.get("tools", {})
    out: dict[str, ToolConfig] = {}
    if not isinstance(tools_raw, dict):
        return out
    for name, cfg_raw in tools_raw.items():
        cfg = cfg_raw if isinstance(cfg_raw, dict) else {}
        out[str(name)] = ToolConfig(
            enabled=bool(cfg.get("enabled", True)),
            adapter=str(cfg.get("adapter", "local") or "local"),
            mutation_level=str(cfg.get("mutation_level", "read") or "read"),
            allowed_agents=_str_tuple(cfg.get("allowed_agents")),
            timeout_seconds=_float(cfg.get("timeout_seconds"), 30.0),
            requires_approval=bool(cfg.get("requires_approval", False)),
            network=bool(cfg.get("network", False)),
            bridge_only=bool(cfg.get("bridge_only", False)),
            command_allowlist=_command_allowlist(cfg.get("command_allowlist")),
            redaction=_str_tuple(cfg.get("redaction")),
            input_schema=cfg.get("input_schema") if isinstance(cfg.get("input_schema"), dict) else None,
            output_schema=cfg.get("output_schema") if isinstance(cfg.get("output_schema"), dict) else None,
            description=str(cfg.get("description", "") or ""),
        )
    return out


def tool_config(name: str) -> ToolConfig:
    return load_tool_configs().get(name, ToolConfig())


def load_execution_config() -> dict[str, Any]:
    path = repo_root() / "configs" / "execution.yaml"
    raw = _load_yaml(path)
    settings = get_settings()
    execution = raw.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    execution.setdefault("backend", settings.mars_execution_backend)
    execution.setdefault("max_concurrency", 16)
    execution.setdefault("batch_steps", 120)
    execution.setdefault("command_timeout_seconds", 60)
    execution.setdefault("allow_real_patch_apply", True)
    code_checks = raw.get("code_checks", {})
    if not isinstance(code_checks, dict):
        code_checks = {}
    return {"execution": execution, "code_checks": code_checks}


def check_commands(kind: str) -> tuple[CommandSpec, ...]:
    cfg = load_execution_config()
    checks_raw = cfg.get("code_checks", {})
    section = checks_raw.get(kind, {}) if isinstance(checks_raw, dict) else {}
    if not isinstance(section, dict) or not bool(section.get("enabled", True)):
        return ()
    commands_raw = section.get("commands", [])
    if not isinstance(commands_raw, list):
        return ()
    out: list[CommandSpec] = []
    for i, item in enumerate(commands_raw):
        if not isinstance(item, dict):
            continue
        argv_raw = item.get("argv", [])
        if not isinstance(argv_raw, list) or not all(isinstance(x, str) for x in argv_raw):
            continue
        if not argv_raw:
            continue
        out.append(
            CommandSpec(
                id=str(item.get("id") or f"{kind}_{i + 1}"),
                label=str(item.get("label") or item.get("id") or f"{kind} {i + 1}"),
                argv=tuple(argv_raw),
            )
        )
    return tuple(out)


def command_timeout_seconds() -> float:
    cfg = load_execution_config()["execution"]
    try:
        return float(cfg.get("command_timeout_seconds", 60) or 60)
    except (TypeError, ValueError):
        return 60.0


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _command_allowlist(value: Any) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        return ()
    out: list[tuple[str, ...]] = []
    for item in value:
        if isinstance(item, list) and all(isinstance(part, str) for part in item):
            out.append(tuple(item))
        elif isinstance(item, str):
            out.append(tuple(item.split()))
    return tuple(out)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}
