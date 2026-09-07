from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.harness.tools.registry import ToolContext, reset_for_tests


@pytest.mark.asyncio
async def test_unconfigured_batch_cannot_fabricate_execution_artifacts(tmp_path: Path) -> None:
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

    assert result.ok is False
    assert json.loads((run_root / "execution" / "metrics.json").read_text()) == []
    assert not (run_root / "execution" / "curves").exists()
    assert (run_root / "events" / "tool_events.jsonl").is_file()


@pytest.mark.asyncio
async def test_removed_metric_echo_command_is_rejected(tmp_path: Path) -> None:
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

    assert result.ok is False
    assert result.metrics == {}
    assert not (run_root / "execution" / "metrics.json").exists()


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
