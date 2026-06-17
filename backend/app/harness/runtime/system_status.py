"""Runtime resource and configuration summaries for operator UI.

This module stays under harness/runtime so API routes and bridge callers can
share read-only operational state without importing frontend or agent code.
"""
from __future__ import annotations

import csv
import importlib.util
import os
import shutil
import subprocess
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.harness.llm.model_registry import available_providers, list_agent_configs
from app.harness.runtime.readiness import check_readiness
from app.settings import get_settings, repo_root


def build_runtime_status(*, project: str | None = None) -> dict[str, Any]:
    """Build a sanitized, read-only status snapshot for P1 operator panels."""
    settings = get_settings()
    project_name = project or settings.mars_default_project
    readiness = check_readiness(project=project_name)
    return {
        "schema": "runtime_status.v1",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "project": project_name,
        "readiness": readiness.to_dict(),
        "resources": {
            "gpu": probe_gpu_resources(),
            "execution": _execution_summary(),
        },
        "observability": {
            "langsmith": _langsmith_summary(),
            "tracing": _tracing_summary(),
        },
        "config": _config_summary(),
    }


def probe_gpu_resources(*, timeout_seconds: float = 2.0) -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return {
            "available": False,
            "source": "nvidia-smi",
            "message": "nvidia-smi not found; CPU/mock fallback remains available",
            "devices": [],
            "summary": {"count": 0, "memory_total_mb": 0, "memory_used_mb": 0},
        }

    query = (
        "index,name,memory.total,memory.used,utilization.gpu,"
        "temperature.gpu,power.draw"
    )
    command = [
        executable,
        f"--query-gpu={query}",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "source": "nvidia-smi",
            "message": str(exc),
            "devices": [],
            "summary": {"count": 0, "memory_total_mb": 0, "memory_used_mb": 0},
        }
    if completed.returncode != 0:
        return {
            "available": False,
            "source": "nvidia-smi",
            "message": completed.stderr.strip() or "nvidia-smi returned non-zero status",
            "devices": [],
            "summary": {"count": 0, "memory_total_mb": 0, "memory_used_mb": 0},
        }
    devices = _parse_nvidia_smi_csv(completed.stdout)
    return {
        "available": bool(devices),
        "source": "nvidia-smi",
        "message": "GPU resources detected" if devices else "no GPU rows returned",
        "devices": devices,
        "summary": {
            "count": len(devices),
            "memory_total_mb": sum(_int_value(item.get("memory_total_mb")) for item in devices),
            "memory_used_mb": sum(_int_value(item.get("memory_used_mb")) for item in devices),
            "utilization_gpu_percent": _average(
                [_int_value(item.get("utilization_gpu_percent")) for item in devices]
            ),
        },
    }


def _parse_nvidia_smi_csv(text: str) -> list[dict[str, Any]]:
    rows = csv.reader(line for line in text.splitlines() if line.strip())
    devices: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < 7:
            continue
        devices.append(
            {
                "index": _to_int(row[0]),
                "name": row[1].strip(),
                "memory_total_mb": _to_int(row[2]),
                "memory_used_mb": _to_int(row[3]),
                "utilization_gpu_percent": _to_int(row[4]),
                "temperature_c": _to_int(row[5]),
                "power_draw_w": _to_float(row[6]),
            }
        )
    return devices


def _execution_summary() -> dict[str, Any]:
    settings = get_settings()
    raw = _read_yaml(repo_root() / "configs" / "execution.yaml")
    execution = _as_dict(raw.get("execution"))
    code_checks = _as_dict(raw.get("code_checks"))
    remote_gpu = _as_dict(execution.get("remote_gpu"))
    local_commands = execution.get("local_commands")
    return {
        "backend": settings.mars_execution_backend,
        "mock_mode": settings.mars_mock_mode,
        "max_concurrency": _to_int(execution.get("max_concurrency"), default=0),
        "batch_steps": _to_int(execution.get("batch_steps"), default=0),
        "command_timeout_seconds": _to_int(
            execution.get("command_timeout_seconds"),
            default=0,
        ),
        "allow_real_patch_apply": bool(execution.get("allow_real_patch_apply", False)),
        "local_command_count": len(local_commands) if isinstance(local_commands, list) else 0,
        "remote_gpu": {
            "enabled": bool(remote_gpu.get("enabled", False)),
            "configured": bool(remote_gpu.get("endpoint")),
        },
        "code_checks": {
            "lint_enabled": bool(_as_dict(code_checks.get("lint")).get("enabled", False)),
            "test_enabled": bool(_as_dict(code_checks.get("test")).get("enabled", False)),
        },
    }


def _langsmith_summary() -> dict[str, Any]:
    settings = get_settings()
    raw = _read_yaml(repo_root() / "configs" / "observability.yaml")
    sink_cfg = _as_dict(_as_dict(raw.get("sinks")).get("langsmith"))
    enabled = settings.mars_langsmith_enabled or bool(sink_cfg.get("enabled", False))
    endpoint = _env_or_yaml_str(
        "LANGSMITH_ENDPOINT",
        sink_cfg.get("endpoint"),
        settings.langsmith_endpoint,
    )
    project = _env_or_yaml_str(
        "LANGSMITH_PROJECT",
        sink_cfg.get("project"),
        settings.langsmith_project,
    )
    timeout_ms = _env_or_yaml_int(
        "MARS_LANGSMITH_TIMEOUT_MS",
        sink_cfg.get("timeout_ms"),
        settings.mars_langsmith_timeout_ms,
    )
    package_available = importlib.util.find_spec("langsmith") is not None
    configured = enabled and bool(settings.langsmith_api_key)
    ui_url = _langsmith_ui_url(endpoint)
    return {
        "enabled": enabled,
        "configured": configured,
        "package_available": package_available,
        "project": project,
        "endpoint": endpoint,
        "timeout_ms": timeout_ms,
        "ui_url": ui_url,
        "embed_url": ui_url if configured else "",
        "message": _langsmith_message(enabled, configured, package_available),
    }


def _tracing_summary() -> dict[str, Any]:
    raw = _read_yaml(repo_root() / "configs" / "observability.yaml")
    tracing = _as_dict(raw.get("tracing"))
    sinks = _as_dict(raw.get("sinks"))
    return {
        "enabled": bool(tracing.get("enabled", True)),
        "exporter": str(tracing.get("exporter", "file")),
        "manifest_path": str(tracing.get("manifest_path", "context/trace_manifest.v1.json")),
        "file_sink": bool(_as_dict(sinks.get("file")).get("enabled", True)),
        "websocket_sink": bool(_as_dict(sinks.get("websocket")).get("enabled", True)),
    }


def _config_summary() -> dict[str, Any]:
    settings = get_settings()
    tools_raw = _read_yaml(repo_root() / "configs" / "tools.yaml")
    tools = _as_dict(tools_raw.get("tools"))
    enabled_tools = [
        name for name, spec in tools.items() if isinstance(spec, dict) and spec.get("enabled", False)
    ]
    network_tools = [
        name for name, spec in tools.items() if isinstance(spec, dict) and spec.get("network", False)
    ]
    agent_configs = list_agent_configs()
    return {
        "runtime": {
            "mode": settings.mars_runtime_mode,
            "mock_mode": settings.mars_mock_mode,
            "default_project": settings.mars_default_project,
            "llm_timeout_seconds": settings.mars_llm_timeout_seconds,
        },
        "llm": {
            "available_providers": sorted(available_providers(include_mock=False)),
            "agents_configured": len(agent_configs),
            "secrets_configured": {
                "anthropic": bool(settings.anthropic_api_key),
                "openai": bool(settings.openai_api_key),
                "qwen": bool(settings.qwen_api_key),
                "gemini": bool(settings.gemini_api_key),
                "deepseek": bool(settings.deepseek_api_key),
                "custom_endpoint": bool(settings.custom_endpoint_url and settings.custom_endpoint_api_key),
            },
        },
        "tools": {
            "total": len(tools),
            "enabled": len(enabled_tools),
            "disabled": max(0, len(tools) - len(enabled_tools)),
            "network_defined": len(network_tools),
            "network_runtime_enabled": settings.mars_enable_network_tools,
            "web_search_provider": settings.mars_web_search_provider,
        },
        "context": {
            "max_tokens": settings.mars_context_max_tokens,
            "target_tokens": settings.mars_context_target_tokens,
            "auto_compress": settings.mars_context_auto_compress,
            "tool_raw_externalize": settings.mars_context_tool_raw_externalize,
            "workbench_enabled": settings.mars_context_workbench_enabled,
        },
        "mcp": {
            "chroma_enabled": _env_bool("MARS_MCP_CHROMA_ENABLED"),
            "chroma_command_configured": bool(os.environ.get("MARS_MCP_CHROMA_COMMAND")),
            "filesystem_command_configured": bool(os.environ.get("MARS_MCP_FILESYSTEM_COMMAND")),
            "filesystem_roots_configured": bool(os.environ.get("MARS_MCP_FILESYSTEM_ROOTS")),
            "git_command_configured": bool(os.environ.get("MARS_MCP_GIT_COMMAND")),
            "github_enabled": _env_bool("MARS_MCP_GITHUB_ENABLED"),
            "github_command_configured": bool(os.environ.get("MARS_MCP_GITHUB_COMMAND")),
        },
    }


def _langsmith_message(
    enabled: bool,
    configured: bool,
    package_available: bool,
) -> str:
    if not enabled:
        return "LangSmith mirror is disabled; file-backed traces are active"
    if not configured:
        return "LangSmith mirror is enabled but LANGSMITH_API_KEY is not configured"
    if not package_available:
        return "LangSmith mirror is configured but the langsmith package is unavailable"
    return "LangSmith mirror is configured"


def _langsmith_ui_url(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized == "https://api.smith.langchain.com":
        return "https://smith.langchain.com"
    if normalized.endswith("/api"):
        return normalized[:-4]
    if "api.smith.langchain.com" in normalized:
        return "https://smith.langchain.com"
    return normalized


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _to_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in {"", "[N/A]", "N/A"}:
            return default
        try:
            return int(float(cleaned))
        except ValueError:
            return default
    return default


def _env_or_yaml_str(env_name: str, value: object, default: str) -> str:
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    if isinstance(value, str) and value:
        return value
    return default


def _env_or_yaml_int(env_name: str, value: object, default: int) -> int:
    env_value = os.environ.get(env_name)
    if env_value:
        return _to_int(env_value, default=default)
    return _to_int(value, default=default)


def _to_float(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in {"", "[N/A]", "N/A"}:
            return default
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def _int_value(value: object) -> int:
    return _to_int(value)


def _average(values: Sequence[int | float]) -> float:
    items = [float(value) for value in values]
    if not items:
        return 0.0
    return round(sum(items) / len(items), 2)


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["build_runtime_status", "probe_gpu_resources"]
