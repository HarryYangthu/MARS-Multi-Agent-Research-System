from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.harness.runtime.readiness import check_readiness


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    import app.settings as settings_mod

    settings_mod._settings = None
    yield
    settings_mod._settings = None


def test_development_readiness_allows_mock_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_EXECUTION_DEVICE", "cpu")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "mock")
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
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "mock")
    monkeypatch.setenv("MARS_REMOTE_ENABLED", "false")

    report = check_readiness(project="pimc")
    device = next(c for c in report.checks if c.name == "execution_device")

    assert report.execution_device == "gpu"
    assert report.execution_device_source == "explicit"
    assert report.ready is False
    assert device.ready is False
    assert device.severity == "blocker"
    assert device.details["effective_adapter_backend"] == "remote_gpu"


def test_production_readiness_blocks_missing_llm_and_mock_execution(
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
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "mock")
    import app.settings as settings_mod

    settings_mod._settings = None
    report = check_readiness(project="pimc")
    blockers = {
        c.name for c in report.checks if c.severity == "blocker" and not c.ready
    }
    assert not report.ready
    assert {"llm_providers", "execution_backend"}.issubset(blockers)


def test_mock_never_blocks_missing_llm_in_development(
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
    monkeypatch.setattr(
        "app.harness.runtime.readiness.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

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
