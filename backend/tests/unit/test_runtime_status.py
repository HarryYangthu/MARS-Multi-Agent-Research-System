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
    monkeypatch.setattr("app.harness.runtime.system_status.shutil.which", lambda _name: None)
    import app.settings as settings_mod

    settings_mod._settings = None
    client = TestClient(app)

    response = client.get("/api/runtime/status")

    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["schema"] == "runtime_status.v1"
    assert payload["resources"]["gpu"]["available"] is False
    assert payload["config"]["llm"]["secrets_configured"]["openai"] is True
    assert "secret-value" not in response.text
