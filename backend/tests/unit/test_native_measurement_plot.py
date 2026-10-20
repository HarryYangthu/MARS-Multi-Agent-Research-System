"""Measured dB values retain their scale; legacy linear ratios stay compatible."""
from pathlib import Path

import numpy as np
import pytest

from app.execution.pim_cancellation import curve_plot_values, plot_loss_curves


def test_native_db_values_and_negative_measurements_are_not_logged_twice(tmp_path: Path) -> None:
    values = [7.72, 7.56, 0.0, -18.5]
    actual = curve_plot_values(values, value_scale="native")
    assert actual.dtype == np.dtype(np.float64)
    assert actual.tolist() == values
    output = tmp_path / "native.png"
    plot_loss_curves({"measured": values}, output, value_scale="native", title="Native measurements")
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_original_linear_ratio_plotting_keeps_its_conversion() -> None:
    actual = curve_plot_values([0.01, 1, 10])
    assert actual.dtype == np.dtype(np.float64)
    assert np.allclose(actual, [-20, 0, 10])


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_nonfinite_values_are_rejected_without_drawing(value: float, tmp_path: Path) -> None:
    output = tmp_path / "invalid.png"
    with pytest.raises(ValueError, match="finite"):
        plot_loss_curves({"invalid": [value]}, output, value_scale="native")
    assert not output.exists()


def test_unknown_scale_is_not_silently_treated_as_linear(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="scale"):
        plot_loss_curves({"unknown": [7.72]}, tmp_path / "unknown.png", value_scale="guessed")
