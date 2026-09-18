"""Inspectable diagnostics from training data and actual optimizer observations.

These summaries are descriptive, not replacements for the frozen evaluator.
The diagnostic entry point accepts only the training split: held-out samples
cannot accidentally enter plots or research context through this module.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import atomic_json
from app.harness.research_trial import file_sha256


def plotting() -> Any:
    matplotlib = importlib.import_module("matplotlib")
    matplotlib.use("Agg", force=True)
    return importlib.import_module("matplotlib.pyplot")


def save_figure(figure: Any, path: Path) -> None:
    """Replace complete PNGs so a live viewer never reads a partial figure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".tmp.png")
    figure.savefig(temporary, dpi=140, bbox_inches="tight")
    temporary.replace(path)


def data_diagnostics(torch: Any, np: Any, train: dict[str, Any], cfg: dict[str, Any],
                     raw_metadata: dict[str, Any], out: Path) -> dict[str, Any]:
    """Compute full-training powers/correlation and bounded, distributed spectra."""
    length = int(cfg["fft_length"])
    # The external static PIMC protocol expresses fs/band in MHz (MIGRATION.md).
    frequency_unit = str(cfg.get("frequency_unit", "MHz"))
    samples = len(train["x"])
    hop = length // 2
    available = (samples - length) // hop + 1
    requested_frames = int(cfg.get("diagnostic_spectrum_frames", max(1, cfg["batch_samples"] // length)))
    if available < 1 or requested_frames < 1:
        raise ValueError("Training diagnostics need a positive spectral frame budget")
    starts = np.unique(np.linspace(0, available - 1, min(available, requested_frames), dtype=int)) * hop
    # [frequency] vectors; PSD normalization is independent of model scoring.
    frequency = torch.fft.fftshift(torch.fft.fftfreq(length, d=1 / cfg["fs"]))
    window = torch.kaiser_window(length, periodic=False, beta=10)
    normalizer = cfg["fs"] * window.square().sum()
    band = (frequency > cfg["band"][0]) & (frequency < cfg["band"][1])
    if not band.any():
        raise ValueError("Empty diagnostic objective frequency band")
    arrays: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    for name in ("x", "y", "nf"):
        # value [time,channels]; all amplitude statistics use training samples only.
        value = train[name]
        channel_power = value.abs().square().mean(dim=0)
        centered = value - value.mean(dim=0, keepdim=True)
        # [channels,time] @ [time,channels] -> [channels,channels].
        covariance = centered.mH @ centered / samples
        variance = covariance.diagonal().real.clamp_min(0)
        denominator = (variance[:, None] * variance[None, :]).sqrt()
        correlation = (covariance.abs() / denominator.clamp_min(torch.finfo(value.real.dtype).tiny)).clamp(0, 1)
        off_diagonal = correlation[~torch.eye(value.shape[1], dtype=torch.bool)]
        # [sampled_frames,channels,frequency], bounded by the recorded frame budget.
        frames = torch.stack([value[int(start):int(start) + length].T for start in starts])
        spectra = torch.fft.fftshift(torch.fft.fft(frames * window, dim=-1), dim=-1)
        channel_psd = spectra.abs().square().mean(dim=0) / normalizer
        mean_psd = channel_psd.mean(dim=0)
        tiny = torch.finfo(mean_psd.dtype).tiny
        mean_psd_db = 10 * mean_psd.clamp_min(tiny).log10()
        arrays[name] = {
            "channel_mean_power_scaled": channel_power.tolist(),
            "channel_rms_scaled": channel_power.sqrt().tolist(),
            "peak_amplitude_scaled": float(value.abs().max()),
            "mean_power_scaled": float(channel_power.mean()),
            "zero_variance_channels": torch.where(variance == 0)[0].tolist(),
            "mean_abs_off_diagonal_correlation": float(off_diagonal.mean()),
            "max_abs_off_diagonal_correlation": float(off_diagonal.max()),
            "spectrum_peak_frequency": float(frequency[mean_psd.argmax()]),
            "spectrum_objective_band_power_fraction": float(mean_psd[band].sum() / mean_psd.sum().clamp_min(tiny)),
        }
        detail[name] = {"mean_psd_scaled_squared_per_frequency_unit": mean_psd.tolist(),
                        "mean_psd_db_scaled_squared_per_frequency_unit": mean_psd_db.tolist(),
                        "abs_centered_channel_correlation": correlation.tolist()}

    summary: dict[str, Any] = {
        "statistics_split": "train", "held_out_statistics_included": False,
        "raw_arrays": raw_metadata, "raw_layout": "channels,time", "model_layout": "time,channels",
        "model_dtype": "torch.complex64", "scale_divisor": cfg["scale"],
        "training_samples": samples, "channels": int(train["x"].shape[1]),
        "frequency_unit": frequency_unit, "sampling_frequency": cfg["fs"], "objective_band": list(cfg["band"]),
        "spectrum_method": "mean periodogram of evenly distributed training frames; Kaiser beta=10",
        "spectrum_fft_length": length, "spectrum_frame_count": len(starts),
        "spectrum_requested_frames": requested_frames,
        "arrays": arrays,
    }
    document: dict[str, Any] = {"schema": "pimc_train_diagnostics.v1", "data_sha256": cfg["data_sha256"],
                "summary": summary, "spectrum_frame_starts_in_train": starts.tolist(),
                "frequency": frequency.tolist(), "details": detail}
    path = out.resolve() / "data_diagnostics.json"
    atomic_json(path, document)
    plt = plotting()
    figure, axes = plt.subplots(1, 3, figsize=(16, 4.4), constrained_layout=True)
    for name in ("x", "y", "nf"):
        axes[0].plot(np.arange(1, summary["channels"] + 1), arrays[name]["channel_mean_power_scaled"], marker=".", label=name)
        axes[1].plot(document["frequency"],
                     detail[name]["mean_psd_db_scaled_squared_per_frequency_unit"], label=name)
    axes[0].set(xlabel="Channel", ylabel="Mean |signal / scale| squared", title="Training channel power", yscale="log")
    axes[0].legend()
    axes[1].axvspan(cfg["band"][0], cfg["band"][1], color="gray", alpha=.15)
    axes[1].set(xlabel=f"Frequency ({frequency_unit})", ylabel=f"PSD (dB, scaled squared / {frequency_unit})", title="Training spectrum")
    axes[1].legend()
    heatmap = axes[2].imshow(detail["x"]["abs_centered_channel_correlation"], vmin=0, vmax=1, cmap="viridis")
    axes[2].set(xlabel="Channel (zero based)", ylabel="Channel (zero based)", title="Input correlation magnitude")
    figure.colorbar(heatmap, ax=axes[2])
    figure.suptitle(f"Training-only data diagnostics | {samples:,} samples | {len(starts)} spectral frames")
    plot_path = out.resolve() / "data_diagnostics.png"
    save_figure(figure, plot_path)
    plt.close(figure)
    return {"path": str(path), "sha256": file_sha256(path), "plot_path": str(plot_path),
            "plot_sha256": file_sha256(plot_path), "summary": summary}


def training_curve(steps: list[dict[str, Any]], validation: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    """Render actual observations without exposing the reserved final-test split."""
    plt = plotting()
    figure, axes = plt.subplots(2, 1, figsize=(9, 6), constrained_layout=True)
    if steps:
        axes[0].plot([row["optimizer_step"] for row in steps], [row["training_loss"] for row in steps])
    axes[0].set(xlabel="Optimizer step", ylabel="Frozen training objective", title="Observed training loss")
    if validation:
        axes[1].plot([row["optimizer_steps"] for row in validation],
                     [row["validation"]["RES_db"] for row in validation], marker="o")
        best = min(validation, key=lambda row: row["validation"]["RES_db"])
        axes[1].scatter([best["optimizer_steps"]], [best["validation"]["RES_db"]],
                        marker="*", s=160, color="red", label="Selected checkpoint")
        axes[1].legend()
    axes[1].set(xlabel="Optimizer step", ylabel="Validation RES_db (lower is better)", title="Frozen validation evaluator")
    for axis in axes:
        axis.grid(alpha=.2)
    path = out.resolve() / "training_curve.png"
    save_figure(figure, path)
    plt.close(figure)
    return {"path": str(path), "sha256": file_sha256(path), "contains_test_data": False,
            "optimizer_observations": len(steps), "validation_observations": len(validation)}
