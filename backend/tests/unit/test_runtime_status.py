from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import subprocess
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.harness.runtime import system_status
from app.main import app


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    import app.settings as settings_mod

    settings_mod._settings = None
    yield
    settings_mod._settings = None


def test_gpu_probe_falls_back_when_nvidia_smi_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.harness.runtime.system_status.shutil.which", lambda _name: None)

    status = system_status.probe_gpu_resources()

    assert status["available"] is False
    assert status["devices"] == []
    assert status["summary"]["count"] == 0


def test_gpu_probe_parses_nvidia_smi_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "nvidia-smi"
    executable.write_text("", encoding="utf-8")

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert command[0] == str(executable)
        assert capture_output is True
        assert check is False
        assert text is True
        assert timeout == 2.0
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="0, NVIDIA L40S, 46068, 1024, 31, 52, 111.5\n",
            stderr="",
        )

    monkeypatch.setattr(
        "app.harness.runtime.system_status.shutil.which",
        lambda _name: str(executable),
    )
    monkeypatch.setattr("app.harness.runtime.system_status.subprocess.run", fake_run)

    status = system_status.probe_gpu_resources()

    assert status["available"] is True
    assert status["summary"]["memory_total_mb"] == 46068
    assert status["devices"][0]["name"] == "NVIDIA L40S"
    assert status["devices"][0]["power_draw_w"] == 111.5


def test_runtime_status_api_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("LANGSMITH_API_KEY", "secret-value")
    monkeypatch.setenv("MARS_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("MARS_EXECUTION_DEVICE", "cpu")
    monkeypatch.setattr("app.harness.runtime.system_status.shutil.which", lambda _name: None)
    import app.settings as settings_mod

    settings_mod._settings = None
    client = TestClient(app)

    response = client.get("/api/runtime/status")

    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["schema"] == "runtime_status.v1"
    assert payload["resources"]["gpu"]["available"] is False
    execution = payload["resources"]["execution"]
    assert execution["device"] == "cpu"
    assert execution["device_source"] == "explicit"
    assert execution["effective_adapter_backend"] == "local_process"
    assert execution["configured_default_device"] == "cpu"
    assert payload["readiness"]["execution_device"] == "cpu"
    assert payload["config"]["llm"]["secrets_configured"]["openai"] is True
    assert "secret-value" not in response.text


def test_remote_gpu_status_exposes_state_without_connection_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_path = tmp_path / "private-key-secret-marker"
    known_hosts_path = tmp_path / "known-hosts-secret-marker"
    key_path.write_text("test-only", encoding="utf-8")
    known_hosts_path.write_text("test-only", encoding="utf-8")
    connection_values = {
        "MARS_EXECUTION_DEVICE": "gpu",
        "MARS_EXECUTION_BACKEND": "remote_gpu",
        "MARS_REMOTE_ENABLED": "true",
        "MARS_REMOTE_SSH_HOST": "secret-host.example.test",
        "MARS_REMOTE_SSH_USER": "secret-user",
        "MARS_REMOTE_SSH_KEY_PATH": str(key_path),
        "MARS_REMOTE_SSH_KNOWN_HOSTS": str(known_hosts_path),
        "MARS_REMOTE_ROOT": "/secret/remote/root",
        "MARS_REMOTE_PYTHON": "/secret/remote/python",
        "MARS_REMOTE_GPU_IDS": "0,1",
    }
    for name, value in connection_values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "app.harness.runtime.readiness.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        "app.harness.runtime.system_status.probe_gpu_resources",
        lambda: {"available": False, "devices": [], "summary": {"count": 0}},
    )
    import app.settings as settings_mod

    settings_mod._settings = None

    status = system_status.build_runtime_status(project="pimc")
    remote = status["resources"]["execution"]["remote_gpu"]
    execution = status["resources"]["execution"]
    serialized = repr(status)

    assert execution["device"] == "gpu"
    assert execution["device_source"] == "explicit"
    assert execution["effective_adapter_backend"] == "remote_gpu"
    assert remote["configured"] is True
    assert remote["gpu_count"] == 2
    for value in connection_values.values():
        if value not in {"true", "gpu", "remote_gpu", "0,1"}:
            assert value not in serialized
