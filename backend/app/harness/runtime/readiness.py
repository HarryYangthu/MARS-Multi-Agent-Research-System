"""Production readiness checks for V2 run admission.

The checker lives in harness so API and bridge callers can share one policy
without depending on each other. It inspects configuration and filesystem
state only; it does not import execution or agent implementations.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.harness.llm.model_registry import (
    available_providers,
    list_agent_configs,
    provider_configured_for_agent,
)
from app.settings import env_or_local, get_settings, repo_root

_REMOTE_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_REMOTE_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,63}$")
_REMOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ready: bool
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    runtime_mode: str
    mock_mode: str
    execution_backend: str
    execution_device: str
    execution_device_source: str
    project: str
    checks: tuple[ReadinessCheck, ...]

    def blocking_messages(self) -> list[str]:
        return [
            c.message for c in self.checks if c.severity == "blocker" and not c.ready
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "runtime_mode": self.runtime_mode,
            "mock_mode": self.mock_mode,
            "execution_backend": self.execution_backend,
            "execution_device": self.execution_device,
            "execution_device_source": self.execution_device_source,
            "project": self.project,
            "checks": [
                {
                    "name": c.name,
                    "ready": c.ready,
                    "severity": c.severity,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.checks
            ],
        }


class ProductionReadinessError(RuntimeError):
    def __init__(self, report: ReadinessReport) -> None:
        super().__init__("; ".join(report.blocking_messages()) or "not production ready")
        self.report = report


def check_readiness(*, project: str | None = None) -> ReadinessReport:
    settings = get_settings()
    project_name = project or settings.mars_default_project
    checks = [
        _check_llm_providers(),
        _check_project_repo(project_name),
        _check_schema_templates(),
        _check_gates(),
        _check_execution_device(),
        _check_execution_backend(project_name),
    ]
    blockers = [c for c in checks if c.severity == "blocker" and not c.ready]
    return ReadinessReport(
        ready=not blockers,
        runtime_mode=settings.mars_runtime_mode,
        mock_mode=settings.mars_mock_mode,
        execution_backend=settings.mars_execution_backend,
        execution_device=settings.effective_execution_device,
        execution_device_source=settings.execution_device_source,
        project=project_name,
        checks=tuple(checks),
    )


def assert_ready_for_run(*, project: str | None = None) -> None:
    settings = get_settings()
    if not settings.is_production:
        return
    report = check_readiness(project=project)
    if not report.ready:
        raise ProductionReadinessError(report)


def _check_llm_providers() -> ReadinessCheck:
    settings = get_settings()
    configured = available_providers(include_mock=False)
    required: set[str] = set()
    missing: set[str] = set()
    mock_requested: list[str] = []

    for cfg in list_agent_configs():
        if not cfg.enabled:
            continue
        provider = cfg.model_provider
        required.add(provider)
        if provider == "mock":
            mock_requested.append(cfg.name)
        elif not provider_configured_for_agent(cfg):
            missing.add(provider)
        for participant in cfg.debate_participants:
            p = str(participant.get("provider", ""))
            if not p:
                continue
            required.add(p)
            if p == "mock":
                mock_requested.append(f"{cfg.name}:debate")
            elif p == provider and provider_configured_for_agent(cfg):
                continue
            elif p not in configured:
                missing.add(p)

    strict_real_llm = settings.is_production or settings.mars_mock_mode == "never"
    if strict_real_llm and mock_requested:
        return ReadinessCheck(
            name="llm_providers",
            ready=False,
            severity="blocker",
            message="strict real-LLM mode cannot use mock LLM providers",
            details={"mock_requested_by": mock_requested},
        )
    if strict_real_llm and missing:
        return ReadinessCheck(
            name="llm_providers",
            ready=False,
            severity="blocker",
            message="strict real-LLM mode is missing required LLM provider configuration",
            details={"missing": sorted(missing), "required": sorted(required)},
        )
    return ReadinessCheck(
        name="llm_providers",
        ready=True,
        severity="info",
        message="LLM provider configuration is acceptable for this runtime mode",
        details={"configured": sorted(configured), "required": sorted(required)},
    )


def _check_project_repo(project: str) -> ReadinessCheck:
    project_dir = repo_root() / "projects" / project
    repo_link = project_dir / "repo_link.yaml"
    if not project_dir.exists() or not repo_link.exists():
        return ReadinessCheck(
            name="project_repo",
            ready=False,
            severity="blocker",
            message=f"project '{project}' is missing project metadata or repo_link.yaml",
        )
    raw = _read_yaml(repo_link)
    repo_path_raw = str(raw.get("repo_path", ""))
    repo_path = _resolve_project_path(project_dir, repo_path_raw)
    exists = bool(repo_path_raw) and repo_path.exists()
    severity = "blocker" if get_settings().is_production else "warning"
    return ReadinessCheck(
        name="project_repo",
        ready=exists,
        severity=severity,
        message=(
            "project repository is connected"
            if exists
            else f"project repository path does not exist: {repo_path}"
        ),
        details={"repo_path": str(repo_path), "repo_mode": raw.get("repo_mode", "")},
    )


def _check_schema_templates() -> ReadinessCheck:
    schemas_dir = repo_root() / "backend" / "app" / "harness" / "schema" / "schemas"
    templates_dir = repo_root() / "templates" / "artifacts"
    schema_ids = {
        cfg.output_schema for cfg in list_agent_configs() if cfg.enabled and cfg.output_schema
    }
    schema_ids.add("diagnosis.v1")
    missing_schema = [
        sid for sid in sorted(schema_ids) if not (schemas_dir / f"{sid}.json").exists()
    ]
    missing_template = [
        sid for sid in sorted(schema_ids) if not (templates_dir / f"{sid}.md").exists()
    ]
    ready = not missing_schema and not missing_template
    return ReadinessCheck(
        name="schema_templates",
        ready=ready,
        severity="blocker" if not ready else "info",
        message=(
            "schema and artifact templates are present"
            if ready
            else "schema or artifact templates are missing"
        ),
        details={
            "missing_schema": missing_schema,
            "missing_template": missing_template,
        },
    )


def _check_gates() -> ReadinessCheck:
    gates_path = repo_root() / "configs" / "gates.yaml"
    raw = _read_yaml(gates_path)
    gates = raw.get("gates", {}) if isinstance(raw.get("gates", {}), dict) else {}
    baseline = gates.get("baseline_compatibility", {})
    enabled = bool(isinstance(baseline, dict) and baseline.get("enabled", False))
    return ReadinessCheck(
        name="gates",
        ready=enabled,
        severity="blocker" if not enabled else "info",
        message=(
            "baseline compatibility gate is enabled"
            if enabled
            else "baseline compatibility gate must be enabled"
        ),
    )


def _check_execution_backend(project: str) -> ReadinessCheck:
    settings = get_settings()
    backend = settings.mars_execution_backend
    if settings.is_production and backend == "mock":
        return ReadinessCheck(
            name="execution_backend",
            ready=False,
            severity="blocker",
            message="production mode cannot use mock execution backend",
            details={"backend": backend},
        )
    if backend == "pim_cpu":
        exists = (repo_root() / "backend" / "app" / "execution" / "pim_cancellation.py").exists()
        return ReadinessCheck(
            name="execution_backend",
            ready=exists and project == "pimc",
            severity="blocker" if settings.is_production else "info",
            message=(
                "PIM CPU execution backend is available"
                if exists and project == "pimc"
                else "PIM CPU execution backend is only available for pimc"
            ),
            details={"backend": backend},
        )
    if backend == "paper_static":
        details = _paper_static_details()
        ready = all(
            bool(details[key])
            for key in ("python_exists", "repo_exists", "config_exists", "data_exists")
        )
        return ReadinessCheck(
            name="execution_backend",
            ready=ready,
            severity="blocker" if settings.is_production else "warning",
            message=(
                "Paper static execution backend is connected"
                if ready
                else "Paper static execution backend is missing code, data, config, or Python"
            ),
            details={"backend": backend, **details},
        )
    if backend == "remote_gpu" and settings.effective_execution_device == "cpu":
        return ReadinessCheck(
            name="execution_backend",
            ready=True,
            severity="info",
            message=(
                "legacy remote_gpu backend is overridden by the explicit CPU device; "
                "Project Adapters run locally"
            ),
            details={
                "backend": backend,
                "effective_adapter_backend": "local_process",
                "execution_device": "cpu",
                "device_source": settings.execution_device_source,
            },
        )
    if backend == "remote_gpu":
        details = remote_gpu_configuration_status()
        ready = bool(details["configured"])
        if not bool(details["enabled"]):
            message = "remote GPU execution backend is selected but disabled"
        elif ready:
            message = (
                "remote GPU execution backend is configured; "
                "the Project Adapter will perform a live runner probe"
            )
        else:
            message = "remote GPU execution backend configuration is incomplete"
        return ReadinessCheck(
            name="execution_backend",
            ready=ready,
            severity="info" if ready else "blocker",
            message=message,
            details={"backend": backend, **details},
        )
    ready = backend in {"mock", "local_command", "docker_command"}
    return ReadinessCheck(
        name="execution_backend",
        ready=ready,
        severity="info" if ready else "blocker",
        message=f"execution backend configured: {backend}",
        details={"backend": backend},
    )


def _check_execution_device() -> ReadinessCheck:
    settings = get_settings()
    device = settings.effective_execution_device
    source = settings.execution_device_source
    if device == "cpu":
        return ReadinessCheck(
            name="execution_device",
            ready=True,
            severity="info",
            message="local CPU execution is selected; Project Adapters use ProcessAdapter",
            details={
                "device": device,
                "source": source,
                "effective_adapter_backend": "local_process",
            },
        )

    details = remote_gpu_configuration_status()
    ready = bool(details["configured"])
    if ready:
        message = (
            "GPU execution is selected and remote SSH prerequisites are configured; "
            "the Project Adapter will perform a live runner probe"
        )
    elif not bool(details["enabled"]):
        message = "GPU execution is selected but the remote GPU executor is disabled"
    else:
        message = "GPU execution is selected but remote SSH configuration is incomplete"
    return ReadinessCheck(
        name="execution_device",
        ready=ready,
        severity="info" if ready else "blocker",
        message=message,
        details={
            "device": device,
            "source": source,
            "effective_adapter_backend": "remote_gpu",
            **details,
        },
    )


def remote_gpu_configuration_status() -> dict[str, Any]:
    """Return sanitized local prerequisites for ``remote_job.v1``.

    This deliberately performs no network I/O and does not import execution
    implementations into harness. A live runner/module check occurs through
    the Project Adapter's adapter.v1 READINESS call before research work.
    """

    raw = _read_yaml(repo_root() / "configs" / "execution.yaml")
    execution = raw.get("execution", {})
    execution_mapping = execution if isinstance(execution, dict) else {}
    remote = execution_mapping.get("remote_gpu", {})
    remote_mapping = remote if isinstance(remote, dict) else {}
    env = remote_mapping.get("env", {})
    env_mapping = env if isinstance(env, dict) else {}

    def env_value(key: str) -> str:
        env_name = str(env_mapping.get(key, "")).strip()
        return env_or_local(env_name).strip() if env_name else ""

    enabled = _parse_bool(
        env_value("enabled"),
        default=bool(remote_mapping.get("enabled", False)),
    )
    host = env_value("host")
    user = env_value("user")
    key_path_raw = env_value("key_path")
    known_hosts_raw = env_value("known_hosts")
    remote_root = env_value("remote_root")
    remote_python = env_value("python") or "python3"
    gpu_ids = tuple(
        value.strip()
        for value in env_value("gpu_ids").split(",")
        if value.strip()
    )
    port_raw = env_value("port")

    required = (
        ("MARS_REMOTE_SSH_HOST", host),
        ("MARS_REMOTE_SSH_USER", user),
        ("MARS_REMOTE_SSH_KEY_PATH", key_path_raw),
        ("MARS_REMOTE_SSH_KNOWN_HOSTS", known_hosts_raw),
        ("MARS_REMOTE_ROOT", remote_root),
    )
    missing_fields = tuple(label for label, value in required if not value)
    findings: list[str] = []
    if shutil.which("ssh") is None:
        findings.append("ssh_binary_missing")
    if shutil.which("scp") is None:
        findings.append("scp_binary_missing")

    key_path = Path(key_path_raw).expanduser() if key_path_raw else None
    known_hosts_path = (
        Path(known_hosts_raw).expanduser() if known_hosts_raw else None
    )
    if key_path is not None and not key_path.is_file():
        findings.append("ssh_key_path_not_file")
    if known_hosts_path is not None and not known_hosts_path.is_file():
        findings.append("known_hosts_path_not_file")
    if host and _REMOTE_HOST_RE.fullmatch(host) is None:
        findings.append("ssh_host_invalid")
    if user and _REMOTE_USER_RE.fullmatch(user) is None:
        findings.append("ssh_user_invalid")
    if remote_python and _REMOTE_TOKEN_RE.fullmatch(remote_python) is None:
        findings.append("remote_python_invalid")
    if remote_root:
        root = PurePosixPath(remote_root)
        if (
            not root.is_absolute()
            or ".." in root.parts
            or root.as_posix() != remote_root
            or _REMOTE_TOKEN_RE.fullmatch(remote_root) is None
        ):
            findings.append("remote_root_invalid")
    if gpu_ids and (
        any(not value.isdigit() for value in gpu_ids)
        or len(set(gpu_ids)) != len(gpu_ids)
    ):
        findings.append("remote_gpu_ids_invalid")
    try:
        port = int(port_raw) if port_raw else 22
    except ValueError:
        port = 0
    if not 1 <= port <= 65_535:
        findings.append("ssh_port_invalid")

    configured = enabled and not missing_fields and not findings
    return {
        "enabled": enabled,
        "configured": configured,
        "protocol": str(remote_mapping.get("protocol", "remote_job.v1")),
        "transport": str(remote_mapping.get("transport", "system_ssh")),
        "host_configured": bool(host),
        "port": port,
        "user_configured": bool(user),
        "key_path_configured": key_path is not None,
        "key_path_exists": bool(key_path is not None and key_path.is_file()),
        "known_hosts_configured": known_hosts_path is not None,
        "known_hosts_exists": bool(
            known_hosts_path is not None and known_hosts_path.is_file()
        ),
        "remote_root_configured": bool(remote_root),
        "remote_python_configured": bool(remote_python),
        "gpu_count": len(gpu_ids),
        "missing_fields": missing_fields,
        "local_findings": tuple(findings),
        "live_probe": "pending" if configured else "blocked",
    }


def _parse_bool(value: str, *, default: bool) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _paper_static_details() -> dict[str, Any]:
    raw = _read_yaml(repo_root() / "configs" / "execution.yaml")
    execution = raw.get("execution", {}) if isinstance(raw.get("execution"), dict) else {}
    paper = execution.get("paper_static", {}) if isinstance(execution.get("paper_static"), dict) else {}
    repo_path = _resolve_plain_path(str(paper.get("repo_path", "")), repo_root())
    config_path = _resolve_plain_path(str(paper.get("config_path", "configs/static.yaml")), repo_path)
    data_path = _resolve_plain_path(str(paper.get("data_path", "")), repo_path)
    python = str(os.environ.get("MARS_PAPER_STATIC_PYTHON") or paper.get("python") or "python")
    return {
        "python": python,
        "python_exists": _python_exists(python),
        "repo_path": str(repo_path),
        "repo_exists": repo_path.is_dir(),
        "config_path": str(config_path),
        "config_exists": config_path.is_file(),
        "data_path": str(data_path),
        "data_exists": data_path.is_file(),
        "default_max_iters": paper.get("default_max_iters", 1),
        "default_dry_run": paper.get("default_dry_run", False),
    }


def _resolve_plain_path(raw_path: str, base: Path) -> Path:
    if not raw_path:
        return Path("")
    path = Path(raw_path).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _python_exists(python: str) -> bool:
    candidate = Path(python).expanduser()
    if candidate.is_absolute():
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(python) is not None


def _resolve_project_path(project_dir: Path, raw_path: str) -> Path:
    if not raw_path:
        return Path("")
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (project_dir / path).resolve()
