"""Production readiness checks for V1 run admission.

The checker lives in harness so API and bridge callers can share one policy
without depending on each other. It inspects configuration and filesystem
state only; it does not import execution or agent implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.harness.llm.model_registry import available_providers, list_agent_configs
from app.settings import get_settings, repo_root


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
        _check_execution_backend(project_name),
    ]
    blockers = [c for c in checks if c.severity == "blocker" and not c.ready]
    return ReadinessReport(
        ready=not blockers,
        runtime_mode=settings.mars_runtime_mode,
        mock_mode=settings.mars_mock_mode,
        execution_backend=settings.mars_execution_backend,
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
        elif provider not in configured:
            missing.add(provider)
        for participant in cfg.debate_participants:
            p = str(participant.get("provider", ""))
            if not p:
                continue
            required.add(p)
            if p == "mock":
                mock_requested.append(f"{cfg.name}:debate")
            elif p not in configured:
                missing.add(p)

    if settings.is_production and mock_requested:
        return ReadinessCheck(
            name="llm_providers",
            ready=False,
            severity="blocker",
            message="production mode cannot use mock LLM providers",
            details={"mock_requested_by": mock_requested},
        )
    if settings.is_production and missing:
        return ReadinessCheck(
            name="llm_providers",
            ready=False,
            severity="blocker",
            message="production mode is missing required LLM provider configuration",
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
            ready=exists and project == "moe-pimc",
            severity="blocker" if settings.is_production else "info",
            message=(
                "PIM CPU execution backend is available"
                if exists and project == "moe-pimc"
                else "PIM CPU execution backend is only available for moe-pimc"
            ),
            details={"backend": backend},
        )
    ready = backend in {"mock", "local_command", "docker_command", "remote_gpu"}
    return ReadinessCheck(
        name="execution_backend",
        ready=ready,
        severity="info" if ready else "blocker",
        message=f"execution backend configured: {backend}",
        details={"backend": backend},
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _resolve_project_path(project_dir: Path, raw_path: str) -> Path:
    if not raw_path:
        return Path("")
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (project_dir / path).resolve()
