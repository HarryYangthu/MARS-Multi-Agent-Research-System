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


def test_step_observation_and_summary_preserve_actual_metric_and_axis() -> None:
    from app.execution.paper_static_adapter import _parse_step_line, _metrics_from_summary
    row = _parse_step_line('mars.progress {"optimizer_step": 7, "training_loss": 0.1, "PIM": 25, "RES": 16, "APE": 9}')
    assert row is not None and row["optimizer_step"] == 7 and row["loss"] == .1
    assert row["paper_RES_db"] == 16
    metrics = _metrics_from_summary({"optimizer_steps": 50, "parameter_counts": 19264, "seed": 2026,
                                    "RES": 16, "APE": 9, "loss": .1})
    assert metrics["RES"] == 16 and metrics["paper_RES_db"] == 16
    assert metrics["loss"] == .1 and metrics["optimizer_steps"] == 50
    for line in ('mars.progress {"optimizer_step": true, "training_loss": 0.1}',
                 'mars.progress {"optimizer_step": 1, "training_loss": NaN}', 'mars.progress {}'):
        assert _parse_step_line(line) is None


def test_steps_require_a_real_entrypoint_capability(tmp_path: Path) -> None:
    from app.execution.paper_static_adapter import approved_config_path
    (tmp_path / 'train_static.py').write_text('print("legacy entry")\n')
    config = {'config_path': 'configs/one.yaml', 'entrypoint': 'train_static.py', 'budget_steps': 50}
    with pytest.raises(ValueError, match='--max-steps'):
        approved_config_path(config, {}, tmp_path)
    # Pure source inspection; the authored entry is never executed as a tool double.
    (tmp_path / 'train_static.py').write_text('parser.add_argument("--max-steps", type=int)\n')
    assert approved_config_path(config, {}, tmp_path) == tmp_path / 'configs/one.yaml'
