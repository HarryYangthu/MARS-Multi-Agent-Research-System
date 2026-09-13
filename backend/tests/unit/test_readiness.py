from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.runs import CreateRunPayload, create_run
from app.bridge.orchestrator import RunRequest
from app.harness.runtime.readiness import ReadinessScope, check_readiness


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    import app.settings as settings_mod

    settings_mod._settings = None
    yield
    settings_mod._settings = None


def test_development_readiness_inspects_real_cpu_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_EXECUTION_DEVICE", "cpu")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "pim_cpu")
    report = check_readiness(project="pimc")
    assert report.runtime_mode == "development"
    assert report.execution_device == "cpu"
    assert report.execution_device_source == "explicit"
    assert any(c.name == "execution_backend" and c.ready for c in report.checks)
    device = next(c for c in report.checks if c.name == "execution_device")
    assert device.ready is True
    assert device.details["effective_adapter_backend"] == "local_process"


def test_explicit_cpu_device_overrides_legacy_remote_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_EXECUTION_DEVICE", "cpu")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "remote_gpu")
    monkeypatch.setenv("MARS_REMOTE_ENABLED", "false")

    report = check_readiness(project="pimc")
    execution = next(c for c in report.checks if c.name == "execution_backend")

    assert report.execution_device == "cpu"
    assert execution.ready is True
    assert execution.details["effective_adapter_backend"] == "local_process"


def test_gpu_device_fails_closed_without_remote_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_EXECUTION_DEVICE", "gpu")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "paper_static")
    monkeypatch.setenv("MARS_REMOTE_ENABLED", "false")

    report = check_readiness(project="pimc")
    device = next(c for c in report.checks if c.name == "execution_device")

    assert report.execution_device == "gpu"
    assert report.execution_device_source == "explicit"
    assert report.ready is False
    assert device.ready is False
    assert device.severity == "blocker"
    assert device.details["effective_adapter_backend"] == "remote_gpu"


def test_production_readiness_blocks_missing_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "LOCAL_VLLM_BASE_URL",
    ):
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "production")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "paper_static")
    import app.settings as settings_mod

    settings_mod._settings = None
    report = check_readiness(project="pimc")
    blockers = {
        c.name for c in report.checks if c.severity == "blocker" and not c.ready
    }
    assert not report.ready
    assert "llm_providers" in blockers


def test_missing_llm_blocks_readiness_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "LOCAL_VLLM_BASE_URL",
    ):
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "paper_static")
    import app.settings as settings_mod

    settings_mod._settings = None
    report = check_readiness(project="pimc")
    blockers = {
        c.name for c in report.checks if c.severity == "blocker" and not c.ready
    }
    assert not report.ready
    assert "llm_providers" in blockers


def test_remote_gpu_backend_blocks_incomplete_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "remote_gpu")
    monkeypatch.setenv("MARS_REMOTE_ENABLED", "true")
    for name in (
        "MARS_REMOTE_SSH_HOST",
        "MARS_REMOTE_SSH_USER",
        "MARS_REMOTE_SSH_KEY_PATH",
        "MARS_REMOTE_SSH_KNOWN_HOSTS",
        "MARS_REMOTE_ROOT",
    ):
        monkeypatch.setenv(name, "")

    report = check_readiness(project="pimc")
    execution = next(item for item in report.checks if item.name == "execution_backend")

    assert report.execution_device == "gpu"
    assert report.execution_device_source == "legacy_backend"
    assert execution.ready is False
    assert execution.severity == "blocker"
    assert execution.details["configured"] is False
    assert "MARS_REMOTE_SSH_HOST" in execution.details["missing_fields"]


def test_remote_gpu_backend_accepts_complete_local_prerequisites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_path = tmp_path / "private-key-marker"
    known_hosts_path = tmp_path / "known-hosts-marker"
    key_path.write_text("test-only", encoding="utf-8")
    known_hosts_path.write_text("test-only", encoding="utf-8")
    values = {
        "MARS_EXECUTION_BACKEND": "remote_gpu",
        "MARS_REMOTE_ENABLED": "true",
        "MARS_REMOTE_SSH_HOST": "gpu.example.test",
        "MARS_REMOTE_SSH_PORT": "2222",
        "MARS_REMOTE_SSH_USER": "mars",
        "MARS_REMOTE_SSH_KEY_PATH": str(key_path),
        "MARS_REMOTE_SSH_KNOWN_HOSTS": str(known_hosts_path),
        "MARS_REMOTE_ROOT": "/srv/mars",
        "MARS_REMOTE_PYTHON": "/opt/mars/bin/python",
        "MARS_REMOTE_GPU_IDS": "0,1",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    report = check_readiness(project="pimc")
    execution = next(item for item in report.checks if item.name == "execution_backend")
    device = next(item for item in report.checks if item.name == "execution_device")

    assert report.execution_device == "gpu"
    assert report.execution_device_source == "legacy_backend"
    assert execution.ready is True
    assert device.ready is True
    assert execution.details["configured"] is True
    assert execution.details["gpu_count"] == 2
    assert execution.details["live_probe"] == "pending"
    assert str(key_path) not in repr(execution.details)
    assert "gpu.example.test" not in repr(execution.details)


@pytest.mark.parametrize(
    ("entrypoint", "standalone", "requires_execution"),
    [
        ("idea", True, False),
        ("idea", False, True),
        ("pipeline", True, True),
        ("pipeline", False, True),
        ("experiment", True, True),
        ("execution", True, True),
    ],
)
def test_only_standalone_idea_narrows_admission_dependencies(
    entrypoint: str, standalone: bool, requires_execution: bool
) -> None:
    request = RunRequest(
        task="dependency scope", project="pimc",
        entrypoint=entrypoint, standalone=standalone,  # type: ignore[arg-type]
    )
    assert request.readiness_scope.requires_execution is requires_execution
    assert request.readiness_scope.required_agents == (
        None if requires_execution else frozenset({"idea", "idea_research"})
    )


def test_standalone_idea_does_not_require_gpu_but_keeps_research_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "production")
    monkeypatch.setenv("MARS_EXECUTION_DEVICE", "gpu")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "remote_gpu")
    monkeypatch.setenv("MARS_REMOTE_ENABLED", "false")
    request = RunRequest(
        task="idea only", project="pimc", entrypoint="idea", standalone=True,
    )
    report = check_readiness(project=request.project, scope=request.readiness_scope)
    checks = {item.name: item for item in report.checks}

    assert "execution_device" not in checks
    assert "execution_backend" not in checks
    assert {"project_repo", "gates", "schema_templates"} <= checks.keys()
    assert checks["llm_providers"].details["agents"] == ["idea", "idea_research"]
    assert checks["schema_templates"].details["required"] == [
        "proposal.v1", "research_report.v1",
    ]
    full_report = check_readiness(project=request.project)
    assert any(item.name == "execution_device" and not item.ready for item in full_report.checks)
    assert not full_report.ready


def test_scoped_readiness_still_blocks_missing_real_model_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    request = RunRequest(
        task="missing credentials", project="pimc", entrypoint="idea", standalone=True,
    )
    report = check_readiness(project=request.project, scope=request.readiness_scope)
    check = next(item for item in report.checks if item.name == "llm_providers")

    assert not check.ready
    assert not report.ready
    assert check.severity == "blocker"
    assert "deepseek" in check.details["missing"]
    assert check.details["agents"] == ["idea", "idea_research"]


def test_scoped_readiness_rejects_missing_agent_configuration() -> None:
    report = check_readiness(
        project="pimc",
        scope=ReadinessScope(
            required_agents=frozenset({"idea", "unconfigured_researcher"}),
            requires_execution=False,
        ),
    )
    check = next(item for item in report.checks if item.name == "llm_providers")

    assert not report.ready
    assert not check.ready
    assert check.details["missing_agents"] == ["unconfigured_researcher"]


async def test_api_admission_uses_standalone_scope_without_faking_model_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("MARS_EXECUTION_DEVICE", "gpu")
    monkeypatch.setenv("MARS_REMOTE_ENABLED", "false")

    with pytest.raises(HTTPException) as caught:
        await create_run(CreateRunPayload(
            task="idea admission", project="pimc", entrypoint="idea", standalone=True,
        ))

    assert caught.value.status_code == 503
    detail: object = caught.value.detail
    assert isinstance(detail, dict)
    check_items = detail["checks"]
    assert isinstance(check_items, list)
    assert all(isinstance(item, dict) for item in check_items)
    checks = {item["name"]: item for item in check_items}
    assert not checks["llm_providers"]["ready"]
    assert checks["llm_providers"]["details"]["agents"] == ["idea", "idea_research"]
    assert "execution_device" not in checks
    assert "execution_backend" not in checks
