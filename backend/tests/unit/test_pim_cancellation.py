from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.execution.pim_cancellation import plot_loss_curve, run_pim_cancellation


def test_real_pim_loss_curve_reports_monotone_measured_objective() -> None:
    _data, result = run_pim_cancellation(
        n_points=8192,
        steps=80,
        ablation_config={"order": 7, "memory": 8},
        seed=7,
    )

    assert len(result.loss_curve) == 80
    assert result.loss_curve[0] > result.loss_curve[-1] * 5
    assert result.loss_curve[5] > 0.2
    deltas = [
        next_value - current
        for current, next_value in zip(result.loss_curve, result.loss_curve[1:], strict=False)
    ]
    assert all(delta <= 1e-12 for delta in deltas)
    assert result.final_loss <= result.loss_curve[-1] + 1e-6
    assert result.n_basis == 32


def test_real_pim_reports_each_training_step() -> None:
    seen: list[tuple[int, float, int]] = []

    def on_step(step: int, value: float, curve: list[float]) -> None:
        seen.append((step, value, len(curve)))

    _data, result = run_pim_cancellation(
        n_points=2048,
        steps=8,
        ablation_config={"order": 3, "memory": 4},
        seed=11,
        on_step=on_step,
    )

    assert [step for step, _value, _count in seen] == list(range(8))
    assert [count for _step, _value, count in seen] == list(range(1, 9))
    assert [value for _step, value, _count in seen] == result.loss_curve


def test_plot_loss_curve_can_run_from_worker_thread(tmp_path: Path) -> None:
    out = tmp_path / "loss.png"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            plot_loss_curve,
            [1.0, 0.72, 0.61, 0.48, 0.42],
            out,
            total_steps=10,
            experiment_id="worker_thread",
        )
        future.result(timeout=10)

    assert out.exists()
    assert out.stat().st_size > 0
