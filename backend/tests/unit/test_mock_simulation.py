"""Real CPU computation and explicit rejection replace fabricated loss curves."""
from __future__ import annotations

import importlib.util
import math

import pytest

from app.execution.pim_cancellation import run_pim_cancellation
from app.execution.simulation_runner import JobSpec, run_one


def test_removed_simulator_is_not_importable() -> None:
    assert importlib.util.find_spec("app.execution.mock_simulation") is None


def test_real_cpu_result_and_callbacks_use_computed_residuals() -> None:
    observed: list[tuple[int, float]] = []

    def record_step(step: int, value: float, curve: list[float]) -> None:
        assert curve[-1] == value
        observed.append((step, value))

    # A real numerical smoke test on generated signals, not production PIMC evidence.
    data, result = run_pim_cancellation(
        n_points=1024, steps=8, seed=11,
        ablation_config={"memory": 2, "order": 3, "loss_batch_size": 1024},
        on_step=record_step,
    )
    assert data.x.shape == data.y.shape == (1024,)
    assert result.is_mock is False
    assert [step for step, _ in observed] == list(range(8))
    assert [value for _, value in observed] == result.loss_curve
    assert all(math.isfinite(value) and value >= 0 for value in result.loss_curve)
    assert result.loss_curve[0] > result.final_loss
    assert math.isclose(result.res_db, 10 * math.log10(result.final_loss), abs_tol=0.002)


@pytest.mark.asyncio
async def test_unconfigured_execution_does_not_return_a_completed_sample() -> None:
    with pytest.raises(RuntimeError, match="no configured execution adapter"):
        await run_one(JobSpec(run_id="missing", experiment_id="missing", project="unconfigured"))
