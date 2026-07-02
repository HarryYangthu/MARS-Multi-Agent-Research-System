from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.tools.registry import ToolContext, reset_for_tests


@pytest.mark.asyncio
async def test_batch_runner_writes_standard_execution_artifacts(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    run_root.mkdir(parents=True)

    result = await reg.dispatch(
        "execution.batch_runner",
        {
            "run_id": "r1",
            "steps": 1,
            "experiments": [
                {"experiment_id": "exp_a", "config": {"alpha": 1}},
                {"experiment_id": "exp_b", "config": {"alpha": 2}},
            ],
        },
        ToolContext(
            run_id="r1",
            project="pimc",
            agent="execution",
            extra={"run_root": str(run_root)},
        ),
    )

    assert result.ok is True
    assert (run_root / "execution" / "metrics.json").is_file()
    assert (run_root / "execution" / "curves" / "exp_a_loss.json").is_file()
    assert (run_root / "execution" / "logs" / "exp_a.log").is_file()
    assert (run_root / "execution" / "run_log_exp_a.v1.md").is_file()
    assert (run_root / "events" / "tool_events.jsonl").is_file()


@pytest.mark.asyncio
async def test_simulation_runner_local_command_backend_writes_artifacts(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    run_root.mkdir(parents=True)

    result = await reg.dispatch(
        "execution.simulation_runner",
        {
            "run_id": "r1",
            "backend": "local_command",
            "command_id": "python_metric_echo",
            "experiment_id": "cmd_a",
        },
        ToolContext(
            run_id="r1",
            project="pimc",
            agent="execution",
            extra={"run_root": str(run_root)},
        ),
    )

    assert result.ok is True
    assert result.output["backend"] == "local_command"
    assert result.metrics["RES"] == -27.2
    assert (run_root / "execution" / "metrics.json").is_file()
    assert (run_root / "execution" / "logs" / "cmd_a_python_metric_echo.log").is_file()


@pytest.mark.asyncio
async def test_non_mock_backend_requires_bridge_callback(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    run_root.mkdir(parents=True)

    result = await reg.dispatch(
        "execution.simulation_runner",
        {"run_id": "r1", "backend": "pim_cpu"},
        ToolContext(
            run_id="r1",
            project="pimc",
            agent="execution",
            extra={"run_root": str(run_root)},
        ),
    )

    assert result.ok is False
    assert "requires a bridge-provided execution callback" in str(result.error)


@pytest.mark.asyncio
async def test_remote_gpu_backend_is_interface_only(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    run_root.mkdir(parents=True)

    result = await reg.dispatch(
        "execution.batch_runner",
        {"run_id": "r1", "backend": "remote_gpu"},
        ToolContext(
            run_id="r1",
            project="pimc",
            agent="execution",
            extra={"run_root": str(run_root)},
        ),
    )

    assert result.ok is False
    assert result.status == "blocked"
    assert "interface-only" in str(result.error)
