from __future__ import annotations

from pathlib import Path

import pytest

from app.execution.paper_static_adapter import _subprocess_env, run_paper_static_simulation
from app.execution.simulation_runner import JobSpec


def test_epoch_and_summary_conversion_are_pure_contracts() -> None:
    from app.execution.paper_static_adapter import _parse_epoch_line, _metrics_from_summary
    # Authored parsing examples, never presented as results of a training process.
    parsed = _parse_epoch_line("epoch: 0_0 PIM: 25.18 RES: 16.53 APE: 8.74")
    metrics = _metrics_from_summary({"PIM": 25.18, "RES": 16.53, "APE": 8.74})
    assert parsed == metrics
    assert metrics["loss"] == pytest.approx(10 ** (-8.74 / 10))
    assert _parse_epoch_line("no metrics") is None
    assert _metrics_from_summary({}) == {}


@pytest.mark.asyncio
async def test_missing_actual_capture_cannot_produce_success(tmp_path: Path) -> None:
    result = await run_paper_static_simulation(JobSpec(run_id="missing-capture", experiment_id="absent",
        project="pimc", run_root=tmp_path, config={"data_path": str(tmp_path / "absent.pth")}), steps=1)
    assert result.status == "failed" and not result.is_mock
    assert "RES" not in result.metrics and "loss" not in result.metrics
    assert (tmp_path / "execution/logs/absent_paper_static.log").is_file()


def test_paper_static_subprocess_does_not_inherit_control_plane_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-research-code")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-research-code")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/tmp/agent.sock")
    monkeypatch.setenv("PYTHONPATH", "/private/tmp/untrusted-pythonpath")
    monkeypatch.setenv("MARS_LOG_LEVEL", "INFO")
    spec = JobSpec(
        run_id="secret-boundary",
        experiment_id="static-secret-boundary",
        project="pimc",
        config={},
        run_root=tmp_path,
    )

    environment = _subprocess_env(run_root=tmp_path, spec=spec)

    assert "DEEPSEEK_API_KEY" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert "SSH_AUTH_SOCK" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["MARS_LOG_LEVEL"] == "INFO"
    assert environment["MARS_RUN_ID"] == "secret-boundary"
