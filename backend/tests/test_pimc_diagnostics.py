"""Real tensors and files verify diagnostic arithmetic and held-out isolation."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.execution.pimc_diagnostics import data_diagnostics
from app.execution.pimc_static_worker import load_splits
from app.harness.research_trial import file_sha256


def capture(tmp_path: Path) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    torch = pytest.importorskip("torch")
    np = importlib.import_module("numpy")
    # Explicit analytical RF tone fixture: [time] -> stored [channels,time].
    phase = torch.arange(32768, dtype=torch.float64) * (2 * torch.pi * 128 / 4096)
    tone = torch.polar(torch.ones_like(phase), phase)
    x = torch.arange(1, 17, dtype=torch.float64)[:, None] * tone[None, :]
    arrays = {"x": x, "y": 2 * x, "nf": x / 4}
    path = tmp_path / "tone.pth"
    torch.save(arrays, path)
    cfg = {"data_path": str(path), "data_sha256": file_sha256(path), "scale": 2,
           "split_guard": 32, "context": 32, "train_fraction": .6, "validation_fraction": .2,
           "fft_length": 256, "batch_samples": 1024, "fs": 4096, "frequency_unit": "Hz", "band": [-1024, 1024]}
    return torch, np, cfg, arrays


def test_training_diagnostics_compute_tone_and_exclude_held_out_values(tmp_path: Path) -> None:
    torch, np, cfg, arrays = capture(tmp_path)
    metadata: dict[str, Any] = {}
    splits, bounds = load_splits(torch, cfg, final=False, raw_metadata=metadata)
    first = data_diagnostics(torch, np, splits["train"], cfg, metadata, tmp_path / "first")
    summary = first["summary"]
    assert summary["statistics_split"] == "train"
    assert summary["held_out_statistics_included"] is False
    assert summary["training_samples"] == bounds["train"][1]
    assert summary["raw_arrays"]["x"] == {"shape": [16, 32768], "dtype": "torch.complex128", "all_finite": True}
    assert summary["scale_divisor"] == 2
    for name, multiplier in (("x", 1), ("y", 4), ("nf", 1 / 16)):
        stats = summary["arrays"][name]
        assert stats["channel_mean_power_scaled"] == pytest.approx([multiplier * c * c / 4 for c in range(1, 17)])
        assert stats["spectrum_peak_frequency"] == pytest.approx(128)
        assert stats["spectrum_objective_band_power_fraction"] > .999
        assert stats["mean_abs_off_diagonal_correlation"] == pytest.approx(1, abs=1e-5)
    assert summary["spectrum_frame_count"] == 4
    assert Path(first["plot_path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert file_sha256(Path(first["path"])) == first["sha256"]
    detail = json.loads(Path(first["path"]).read_text())
    assert len(detail["frequency"]) == cfg["fft_length"]
    assert len(detail["details"]["x"]["abs_centered_channel_correlation"]) == 16

    # Change every sample after training, including guards, validation, and test.
    # Their amplitudes must not influence any research-visible diagnostic value.
    for value in arrays.values():
        value[:, bounds["train"][1]:] *= 31
    torch.save(arrays, cfg["data_path"])
    cfg["data_sha256"] = file_sha256(Path(cfg["data_path"]))
    new_metadata: dict[str, Any] = {}
    changed, _ = load_splits(torch, cfg, final=False, raw_metadata=new_metadata)
    second = data_diagnostics(torch, np, changed["train"], cfg, new_metadata, tmp_path / "second")
    assert second["summary"] == summary
    new_detail = json.loads(Path(second["path"]).read_text())
    assert new_detail["details"] == detail["details"]
    assert new_detail["data_sha256"] != detail["data_sha256"]


def test_nonfinite_capture_fails_before_diagnostic_success(tmp_path: Path) -> None:
    torch, _, cfg, arrays = capture(tmp_path)
    # Actual malformed tensor data, without patching the loader or finite check.
    arrays["nf"][0, 0] = complex(float("nan"), 0)
    torch.save(arrays, cfg["data_path"])
    cfg["data_sha256"] = file_sha256(Path(cfg["data_path"]))
    with pytest.raises(ValueError, match="finite x/y/nf"):
        load_splits(torch, cfg, final=False)


def test_spectral_frame_budget_must_be_positive(tmp_path: Path) -> None:
    torch, np, cfg, _ = capture(tmp_path)
    splits, _ = load_splits(torch, cfg, final=False)
    cfg["diagnostic_spectrum_frames"] = 0
    with pytest.raises(ValueError, match="positive spectral frame budget"):
        data_diagnostics(torch, np, splits["train"], cfg, {}, tmp_path / "diagnostics")
