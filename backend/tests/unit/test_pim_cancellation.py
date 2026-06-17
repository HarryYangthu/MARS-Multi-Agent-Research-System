from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.execution.pim_cancellation import plot_loss_curve, run_pim_cancellation


def test_real_pim_loss_curve_is_gradual_and_has_local_variation() -> None:
    _data, result = run_pim_cancellation(
        n_points=8192,
        steps=80,
        ablation_config={"expert_count": 8},
        seed=7,
    )

    assert len(result.loss_curve) == 80
    assert result.loss_curve[0] > result.loss_curve[-1] * 5
    assert result.loss_curve[5] > 0.2
    deltas = [
        next_value - current
        for current, next_value in zip(result.loss_curve, result.loss_curve[1:], strict=False)
    ]
    assert any(delta > 0 for delta in deltas)


def test_real_pim_reports_each_training_step() -> None:
    seen: list[tuple[int, float, int]] = []

    def on_step(step: int, value: float, curve: list[float]) -> None:
        seen.append((step, value, len(curve)))

    _data, result = run_pim_cancellation(
        n_points=2048,
        steps=8,
        ablation_config={"expert_count": 4},
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
