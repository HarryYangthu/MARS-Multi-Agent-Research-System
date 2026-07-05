"""Synthetic PIM data generator for the pimc project.

Lives in the project repo so MARS can drive Mock Simulation without
touching production data. Pure Python + numpy; no GPU required.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger


@dataclass
class PimSignal:
    x: np.ndarray
    y: np.ndarray
    stream_label: int


@dataclass
class DualCarrierIm3Measurement:
    txa: np.ndarray
    rxa: np.ndarray
    nfa: np.ndarray
    metadata: dict[str, Any]


def _polynomial_pim(x: np.ndarray, *, order: int = 5) -> np.ndarray:
    """Volterra-style polynomial nonlinearity (odd-order)."""
    out = np.zeros_like(x)
    for k in range(1, order + 1, 2):
        coef = 1.0 / math.factorial(k)
        out = out + coef * np.power(x, k)
    return out


def generate(
    *,
    n_samples: int = 1024,
    streams: int = 2,
    snr_db: float = 30.0,
    seed: int | None = None,
) -> list[PimSignal]:
    """Produce ``streams`` worth of (x, y) pairs."""
    rng = np.random.default_rng(seed)
    out: list[PimSignal] = []
    for s in range(streams):
        # x: (B, T) real samples
        x = rng.standard_normal(n_samples).astype(np.float32)
        clean = _polynomial_pim(x)
        noise_var = np.var(clean) * 10 ** (-snr_db / 10)
        y = clean + rng.normal(0, math.sqrt(noise_var), size=n_samples).astype(np.float32)
        out.append(PimSignal(x=x, y=y, stream_label=s))
    return out


def loss_curve(steps: int = 100, *, template: str = "exponential_decay", seed: int | None = None) -> list[float]:
    rng = random.Random(seed)
    out: list[float] = []
    if template == "exponential_decay":
        base = 1.0
        for i in range(steps):
            v = base * math.exp(-i / max(1, steps // 4)) + rng.uniform(-0.01, 0.01)
            out.append(max(0.0, v))
    elif template == "noisy_decay":
        for i in range(steps):
            v = max(0.0, 1.0 / (1 + i * 0.05) + rng.uniform(-0.05, 0.05))
            out.append(v)
    else:  # plateau
        for i in range(steps):
            v = 0.4 + rng.uniform(-0.02, 0.02) if i > steps // 3 else 1.0 - i / max(1, steps // 3)
            out.append(max(0.0, v))
    return out


def generate_dual_carrier_pim(
    *,
    n_points: int = 30720,
    f1: float = 30.0,
    f2: float = 38.0,
    fs: float = 184.32,
    snr_db: float = 30.0,
    order: int = 7,
    true_memory: int = 12,
    seed: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate a dual-carrier complex baseband signal and its odd-order PIM.

    Returns (x, y) complex arrays of shape (n_points,):
      x  : transmitted dual-carrier signal (two carriers at f1, f2 MHz)
      y  : PIM-contaminated receive signal (odd-order memory polynomial + noise)

    Mirrors backend/app/execution/pim_cancellation.py so this standalone data
    matches what the Execution Agent simulates. Pure numpy, no GPU.
    """
    rng = np.random.default_rng(seed)
    n = np.arange(n_points)
    env1 = 1.0 + 0.1 * rng.standard_normal(n_points)
    env2 = 0.9 + 0.1 * rng.standard_normal(n_points)
    x = (
        env1 * np.exp(2j * np.pi * f1 / fs * n)
        + env2 * np.exp(2j * np.pi * f2 / fs * n)
    ).astype(np.complex128)

    a = {1: 1.0, 3: 0.35, 5: 0.12, 7: 0.04}
    pim = np.zeros(n_points, dtype=np.complex128)
    for k in range(1, max(order, 7) + 1, 2):
        coef = a.get(k, 0.0)
        if coef == 0.0:
            continue
        base = x * np.abs(x) ** (k - 1)
        for m in range(true_memory):
            tap = coef * np.exp(-m / 4.0) * np.exp(1j * 0.3 * m * (k % 4 + 1))
            if m == 0:
                pim = pim + tap * base
            else:
                shifted = np.zeros_like(base)
                shifted[m:] = base[:-m]
                pim = pim + tap * shifted

    pim_power = float(np.mean(np.abs(pim) ** 2))
    noise_std = np.sqrt(pim_power * 10 ** (-snr_db / 10) / 2)
    noise = noise_std * (rng.standard_normal(n_points) + 1j * rng.standard_normal(n_points))
    y = (pim + noise).astype(np.complex128)
    return x, y


def _frequency_grid_mhz(n_points: int, fs_mhz: float) -> np.ndarray:
    return np.fft.fftshift(np.fft.fftfreq(n_points, d=1.0 / (fs_mhz * 1e6))) / 1e6


def _wrap_freq_mhz(freq_mhz: float, fs_mhz: float) -> float:
    return ((freq_mhz + fs_mhz / 2.0) % fs_mhz) - fs_mhz / 2.0


def _scale_to_power(signal: np.ndarray, target_power_w: float) -> np.ndarray:
    power = float(np.mean(np.abs(signal) ** 2))
    if power <= 0.0:
        raise ValueError("cannot scale a zero-power signal")
    return signal * math.sqrt(target_power_w / power)


def _band_limited_complex_noise(
    *,
    n_points: int,
    fs_mhz: float,
    center_mhz: float,
    bandwidth_mhz: float,
    rng: np.random.Generator,
) -> np.ndarray:
    freq_mhz = _frequency_grid_mhz(n_points, fs_mhz)
    spectrum = np.zeros(n_points, dtype=np.complex128)
    distance = np.abs(freq_mhz - center_mhz)
    mask = distance <= bandwidth_mhz / 2.0
    edge_mhz = min(1.0, bandwidth_mhz / 8.0)
    taper = np.ones(n_points)
    if edge_mhz > 0.0:
        edge_mask = (distance > bandwidth_mhz / 2.0 - edge_mhz) & mask
        taper[edge_mask] = 0.5 * (
            1.0
            + np.cos(
                np.pi
                * (distance[edge_mask] - (bandwidth_mhz / 2.0 - edge_mhz))
                / edge_mhz
            )
        )
    random_bins = rng.standard_normal(n_points) + 1j * rng.standard_normal(n_points)
    spectrum[mask] = random_bins[mask] * taper[mask]
    return (np.fft.ifft(np.fft.ifftshift(spectrum)) * n_points).astype(np.complex128)


def _fft_filter(signal: np.ndarray, *, fs_mhz: float, center_mhz: float, bandwidth_mhz: float) -> np.ndarray:
    freq_mhz = _frequency_grid_mhz(signal.size, fs_mhz)
    spectrum = np.fft.fftshift(np.fft.fft(signal))
    mask = np.abs(freq_mhz - center_mhz) <= bandwidth_mhz / 2.0
    filtered = np.zeros_like(spectrum)
    filtered[mask] = spectrum[mask]
    return np.fft.ifft(np.fft.ifftshift(filtered)).astype(np.complex128)


def _noise_from_density_dbm_per_mhz(
    *,
    n_points: int,
    fs_mhz: float,
    density_dbm_per_mhz: float,
    rng: np.random.Generator,
) -> np.ndarray:
    density_w_per_mhz = 1e-3 * 10 ** (density_dbm_per_mhz / 10.0)
    total_power_w = density_w_per_mhz * fs_mhz
    noise_std = math.sqrt(total_power_w / 2.0)
    return (
        noise_std * (rng.standard_normal(n_points) + 1j * rng.standard_normal(n_points))
    ).astype(np.complex128)


def generate_dual_carrier_im3_measurement(
    *,
    n_points: int = 90_000,
    fs_mhz: float = 184.32,
    carrier_spacing_mhz: float = 45.0,
    carrier_bandwidth_mhz: float = 10.0,
    lo_diff_mhz: float = -107.3,
    im3_filter_bandwidth_mhz: float = 60.0,
    txa_noise_floor_dbm_per_mhz: float = -40.0,
    rxa_noise_floor_dbm_per_mhz: float = -105.0,
    tx_carrier_power_dbm: float = 3.0,
    rxa_im3_power_dbm: float = -76.7,
    seed: int | None = None,
) -> DualCarrierIm3Measurement:
    """Generate the project measurement fixture: txa, rxa and nfa.

    The signals are complex baseband arrays of shape (n_points,). ``txa`` is
    two 10 MHz carriers centered at +/-22.5 MHz. IM3 is generated as
    c1*c1*conj(c2) and c2*c2*conj(c1), shifted by ``lo_diff_mhz`` into the RX
    sampling frame, filtered branch-wise with a 60 MHz filter, then shifted to
    0 Hz. ``nfa`` is the RX noise floor, and ``rxa`` is IM3 + ``nfa``.
    """
    rng = np.random.default_rng(seed)
    n = np.arange(n_points)
    c1_center_mhz = -carrier_spacing_mhz / 2.0
    c2_center_mhz = carrier_spacing_mhz / 2.0

    c1 = _band_limited_complex_noise(
        n_points=n_points,
        fs_mhz=fs_mhz,
        center_mhz=c1_center_mhz,
        bandwidth_mhz=carrier_bandwidth_mhz,
        rng=rng,
    )
    c2 = _band_limited_complex_noise(
        n_points=n_points,
        fs_mhz=fs_mhz,
        center_mhz=c2_center_mhz,
        bandwidth_mhz=carrier_bandwidth_mhz,
        rng=rng,
    )
    carrier_power_w = 1e-3 * 10 ** (tx_carrier_power_dbm / 10.0)
    c1 = _scale_to_power(c1, carrier_power_w)
    c2 = _scale_to_power(c2, carrier_power_w)
    tx_clean = c1 + c2
    tx_noise = _noise_from_density_dbm_per_mhz(
        n_points=n_points,
        fs_mhz=fs_mhz,
        density_dbm_per_mhz=txa_noise_floor_dbm_per_mhz,
        rng=rng,
    )
    txa = (tx_clean + tx_noise).astype(np.complex128)

    lower_im3_tx_center_mhz = 2.0 * c1_center_mhz - c2_center_mhz
    upper_im3_tx_center_mhz = 2.0 * c2_center_mhz - c1_center_mhz
    lower_im3 = c1 * c1 * np.conj(c2)
    upper_im3 = c2 * c2 * np.conj(c1)
    rx_shift = np.exp(-2j * np.pi * lo_diff_mhz / fs_mhz * n)
    lower_rx_raw = lower_im3 * rx_shift
    upper_rx_raw = upper_im3 * rx_shift
    lower_rx_center_mhz = _wrap_freq_mhz(lower_im3_tx_center_mhz - lo_diff_mhz, fs_mhz)
    upper_rx_center_mhz = _wrap_freq_mhz(upper_im3_tx_center_mhz - lo_diff_mhz, fs_mhz)

    lower_filtered = _fft_filter(
        lower_rx_raw,
        fs_mhz=fs_mhz,
        center_mhz=lower_rx_center_mhz,
        bandwidth_mhz=im3_filter_bandwidth_mhz,
    )
    upper_filtered = _fft_filter(
        upper_rx_raw,
        fs_mhz=fs_mhz,
        center_mhz=upper_rx_center_mhz,
        bandwidth_mhz=im3_filter_bandwidth_mhz,
    )
    lower_baseband = lower_filtered * np.exp(-2j * np.pi * lower_rx_center_mhz / fs_mhz * n)
    upper_baseband = upper_filtered * np.exp(-2j * np.pi * upper_rx_center_mhz / fs_mhz * n)
    rxa_clean = _scale_to_power(lower_baseband + upper_baseband, 1e-3 * 10 ** (rxa_im3_power_dbm / 10.0))
    nfa = _noise_from_density_dbm_per_mhz(
        n_points=n_points,
        fs_mhz=fs_mhz,
        density_dbm_per_mhz=rxa_noise_floor_dbm_per_mhz,
        rng=rng,
    )
    rxa = (rxa_clean + nfa).astype(np.complex128)

    metadata = {
        "schema": "pimc_dual_carrier_im3.v1",
        "n_points": n_points,
        "fs_mhz": fs_mhz,
        "carrier_centers_mhz": [c1_center_mhz, c2_center_mhz],
        "carrier_spacing_mhz": carrier_spacing_mhz,
        "carrier_bandwidth_mhz": carrier_bandwidth_mhz,
        "lo_diff_mhz": lo_diff_mhz,
        "lo_diff_definition": "LO_rx - LO_tx",
        "im3_centers_tx_frame_mhz": [lower_im3_tx_center_mhz, upper_im3_tx_center_mhz],
        "im3_centers_rx_sampled_mhz": [lower_rx_center_mhz, upper_rx_center_mhz],
        "im3_filter_bandwidth_mhz": im3_filter_bandwidth_mhz,
        "txa_noise_floor_dbm_per_mhz": txa_noise_floor_dbm_per_mhz,
        "rxa_noise_floor_dbm_per_mhz": rxa_noise_floor_dbm_per_mhz,
        "tx_carrier_power_dbm_each": tx_carrier_power_dbm,
        "rxa_im3_power_dbm": rxa_im3_power_dbm,
        "seed": seed,
        "txa_power_w": float(np.mean(np.abs(txa) ** 2)),
        "rxa_power_w": float(np.mean(np.abs(rxa) ** 2)),
        "nfa_power_w": float(np.mean(np.abs(nfa) ** 2)),
    }
    return DualCarrierIm3Measurement(txa=txa, rxa=rxa, nfa=nfa, metadata=metadata)


def _welch_dbm_per_mhz(signal: np.ndarray, *, fs_mhz: float, nperseg: int = 8192) -> tuple[np.ndarray, np.ndarray]:
    if signal.size < nperseg:
        nperseg = signal.size
    step = max(1, nperseg // 2)
    window = np.hanning(nperseg)
    window_power = float(np.sum(window**2))
    acc: np.ndarray | None = None
    count = 0
    fs_hz = fs_mhz * 1e6
    for start in range(0, signal.size - nperseg + 1, step):
        segment = signal[start : start + nperseg] * window
        spectrum = np.fft.fftshift(np.fft.fft(segment, nperseg))
        psd_w_per_hz = (np.abs(spectrum) ** 2) / (fs_hz * window_power)
        psd_w_per_mhz = psd_w_per_hz * 1e6
        acc = psd_w_per_mhz if acc is None else acc + psd_w_per_mhz
        count += 1
    if acc is None or count == 0:
        raise ValueError("signal is too short for Welch spectrum")
    psd = acc / count
    freq_mhz = _frequency_grid_mhz(nperseg, fs_mhz)
    dbm_per_mhz = 10.0 * np.log10(np.maximum(psd, 1e-30) / 1e-3)
    return freq_mhz, dbm_per_mhz


def save_dual_carrier_im3_mat(path: str | Path, measurement: DualCarrierIm3Measurement) -> None:
    try:
        from scipy.io import savemat
    except ImportError as exc:  # pragma: no cover - optional local data utility
        raise RuntimeError("scipy is required to save .mat files") from exc

    savemat(
        path,
        {
            "txa": measurement.txa,
            "rxa": measurement.rxa,
            "nfa": measurement.nfa,
            "metadata": measurement.metadata,
        },
        do_compression=True,
    )


def plot_dual_carrier_im3_spectrum(path: str | Path, measurement: DualCarrierIm3Measurement) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - optional local data utility
        raise RuntimeError("matplotlib is required to plot spectra") from exc

    fs_mhz = float(measurement.metadata["fs_mhz"])
    tx_freq, tx_psd = _welch_dbm_per_mhz(measurement.txa, fs_mhz=fs_mhz)
    rx_freq, rx_psd = _welch_dbm_per_mhz(measurement.rxa, fs_mhz=fs_mhz)
    nf_freq, nf_psd = _welch_dbm_per_mhz(measurement.nfa, fs_mhz=fs_mhz)

    fig, ax = plt.subplots(figsize=(15, 7), dpi=160)
    ax.plot(tx_freq, tx_psd, color="#2563eb", linewidth=1.2, label="txa: two 10 MHz carriers")
    ax.plot(rx_freq, rx_psd, color="#dc2626", linewidth=1.1, label="rxa: IM3 branches")
    ax.plot(nf_freq, nf_psd, color="#16a34a", linewidth=1.0, label="nfa: rxa noise floor")
    ax.axhline(
        float(measurement.metadata["txa_noise_floor_dbm_per_mhz"]),
        color="#2563eb",
        linestyle="--",
        linewidth=0.8,
        alpha=0.55,
    )
    ax.axhline(
        float(measurement.metadata["rxa_noise_floor_dbm_per_mhz"]),
        color="#16a34a",
        linestyle="--",
        linewidth=0.8,
        alpha=0.55,
    )
    ax.axvspan(-30, 30, color="#dc2626", alpha=0.07, label="60 MHz RX filter span")
    for center in measurement.metadata["carrier_centers_mhz"]:
        ax.axvspan(center - 5.0, center + 5.0, color="#2563eb", alpha=0.08)
    ax.axvline(0.0, color="#111827", linewidth=0.8, alpha=0.45)
    ax.set_title(
        "Spectrum Overlay: fs=184.32 MHz, LO diff=-107.3 MHz, noise floors calibrated"
    )
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("PSD (dBm/MHz, Welch)")
    ax.set_xlim(-fs_mhz / 2.0, fs_mhz / 2.0)
    ax.set_ylim(-125, 5)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Generate dual-carrier PIM data")
    ap.add_argument("--n-points", type=int, default=30720, help="~30k complex points")
    ap.add_argument("--snr-db", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=str, default="", help="output .npz path (x, y)")
    ap.add_argument("--measurement", action="store_true", help="generate txa/rxa/nfa measurement data")
    ap.add_argument("--mat-out", type=str, default="", help="output .mat path (txa, rxa, nfa)")
    ap.add_argument("--plot-out", type=str, default="", help="output spectrum .png path")
    ap.add_argument("--fs-mhz", type=float, default=184.32)
    ap.add_argument("--lo-diff-mhz", type=float, default=-107.3)
    ap.add_argument("--txa-noise-floor-dbm", type=float, default=-40.0)
    ap.add_argument("--rxa-noise-floor-dbm", type=float, default=-105.0)
    ap.add_argument("--tx-carrier-power-dbm", type=float, default=3.0)
    ap.add_argument("--rxa-im3-power-dbm", type=float, default=-76.7)
    args = ap.parse_args()

    if args.measurement or args.mat_out or args.plot_out:
        measurement = generate_dual_carrier_im3_measurement(
            n_points=args.n_points,
            fs_mhz=args.fs_mhz,
            lo_diff_mhz=args.lo_diff_mhz,
            txa_noise_floor_dbm_per_mhz=args.txa_noise_floor_dbm,
            rxa_noise_floor_dbm_per_mhz=args.rxa_noise_floor_dbm,
            tx_carrier_power_dbm=args.tx_carrier_power_dbm,
            rxa_im3_power_dbm=args.rxa_im3_power_dbm,
            seed=args.seed,
        )
        logger.info("txa: {} {} (dual-carrier TX)", measurement.txa.shape, measurement.txa.dtype)
        logger.info("rxa: {} {} (IM3 + noise)", measurement.rxa.shape, measurement.rxa.dtype)
        logger.info("nfa: {} {} (RX noise floor)", measurement.nfa.shape, measurement.nfa.dtype)
        logger.info("metadata: {}", measurement.metadata)
        if args.mat_out:
            save_dual_carrier_im3_mat(args.mat_out, measurement)
            logger.info("[saved] {}", args.mat_out)
        if args.plot_out:
            plot_dual_carrier_im3_spectrum(args.plot_out, measurement)
            logger.info("[saved] {}", args.plot_out)
        return

    x, y = generate_dual_carrier_pim(
        n_points=args.n_points, snr_db=args.snr_db, seed=args.seed
    )
    pim_dbc = 10 * np.log10(np.mean(np.abs(y) ** 2) / np.mean(np.abs(x) ** 2))
    logger.info("x: {} {} (dual-carrier TX)", x.shape, x.dtype)
    logger.info("y: {} {} (PIM + noise)", y.shape, y.dtype)
    logger.info("PIM/carrier power ratio: {:.2f} dB", pim_dbc)
    if args.out:
        np.savez(args.out, x=x, y=y)
        logger.info("[saved] {}.npz", args.out)


if __name__ == "__main__":
    _cli()
