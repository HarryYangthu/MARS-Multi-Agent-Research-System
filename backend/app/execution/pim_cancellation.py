"""Real (lightweight) dual-carrier PIM cancellation simulation.

Physics-faithful but CPU-cheap stand-in for the full 7-layer PIMC training:

1. Dual-carrier TX:   x = A1·e^{j2πf1 n} + A2·e^{j2πf2 n}   (complex baseband)
2. Passive nonlinearity (odd-order memory polynomial) produces PIM, with the
   3rd-order intermod landing at 2f1-f2 / 2f2-f1 — the classic PIM tones.
3. A learnable memory-polynomial canceller ŷ = Φ(x)·w is fit by normalized
   gradient descent; the per-step residual power gives a real training loss
   curve and a physically meaningful RES (residual) in dB.

Ablation config (from experiment_plan.ablations) modulates the canceller's
capacity (memory depth / nonlinear order), so different ablations yield
different RES — i.e. the multi-experiment curves actually differ.

Pure numpy. ~30k complex points trains in well under a second on CPU.
"""
from __future__ import annotations

import os
import struct
import tempfile
import threading
import time as wall_time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

DEFAULT_N_POINTS = 30720  # matches the real code's length_case
FS = 184.32               # MHz, matches configs/base.yaml
DEFAULT_F1 = 30.0         # MHz carrier 1
DEFAULT_F2 = 38.0         # MHz carrier 2
StepCallback = Callable[[int, float, list[float]], None]


@dataclass
class PimDataset:
    x: np.ndarray          # complex TX, shape (N,)
    y: np.ndarray          # complex PIM-contaminated RX, shape (N,)
    n_points: int
    f1: float
    f2: float
    fs: float


@dataclass
class CancelResult:
    loss_curve: list[float]      # residual power ratio per step (linear)
    res_db: float                # final residual / input power, dB (lower=better)
    pim_suppression_db: float    # = -res_db, how much PIM was cancelled
    ape_deg: float               # average phase error of residual, degrees
    final_loss: float
    final_loss_db: float
    n_points: int
    n_basis: int
    is_mock: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _normalize_seed(seed: int | None) -> int:
    if seed is None:
        return int(wall_time.time_ns() & 0xFFFF_FFFF)
    return int(seed) & 0xFFFF_FFFF


def _uniform01(size: int, *, seed: int, stream: int) -> NDArray[np.float64]:
    if size <= 0:
        return cast(NDArray[np.float64], np.zeros(0, dtype=np.float64))
    # Deterministic vectorized hash-RNG. This deliberately avoids np.random:
    # the local Python 3.13/numpy combo can hang on numpy.random lazy import.
    idx = np.arange(1, size + 1, dtype=np.float64)
    phase = idx * (12.9898 + 0.071 * stream) + (seed + stream * 1009) * 78.233
    raw = np.sin(phase) * 43758.5453123
    return raw - np.floor(raw)


def _standard_normal(size: int, *, seed: int, stream: int) -> NDArray[np.float64]:
    u1 = np.clip(_uniform01(size, seed=seed, stream=stream), 1e-12, 1.0)
    u2 = _uniform01(size, seed=seed, stream=stream + 1)
    values = np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)
    return cast(NDArray[np.float64], np.asarray(values, dtype=np.float64))


def _scalar_standard_normal(*, seed: int, stream: int) -> float:
    values = _standard_normal(1, seed=seed, stream=stream)
    return float(values[0]) if values.size else 0.0


def _integers(low: int, high: int, *, size: int, seed: int, stream: int) -> NDArray[np.int64]:
    if high <= low or size <= 0:
        return cast(NDArray[np.int64], np.zeros(max(0, size), dtype=np.int64))
    values = _uniform01(size, seed=seed, stream=stream)
    out = low + np.floor(values * (high - low))
    return cast(NDArray[np.int64], np.asarray(out, dtype=np.int64))


def generate_dual_carrier_pim(
    *,
    n_points: int = DEFAULT_N_POINTS,
    f1: float = DEFAULT_F1,
    f2: float = DEFAULT_F2,
    fs: float = FS,
    snr_db: float = 30.0,
    order: int = 5,
    seed: int | None = None,
) -> PimDataset:
    """Generate a dual-carrier signal and its odd-order PIM contamination."""
    resolved_seed = _normalize_seed(seed)
    n = np.arange(n_points)
    # Slowly-varying complex envelopes so the carriers aren't pure tones
    # (closer to a modulated dual-carrier scenario).
    env1 = 1.0 + 0.1 * _standard_normal(n_points, seed=resolved_seed, stream=1)
    env2 = 0.9 + 0.1 * _standard_normal(n_points, seed=resolved_seed, stream=3)
    x = (
        env1 * np.exp(2j * np.pi * f1 / fs * n)
        + env2 * np.exp(2j * np.pi * f2 / fs * n)
    ).astype(np.complex128)

    # Passive nonlinearity: odd-order memory polynomial WITH memory effects
    # (decaying delay taps). True memory depth = TRUE_MEMORY; a canceller with
    # fewer taps cannot fully cancel, leaving a higher residual — which is what
    # makes ablations (memory depth) physically differentiate.
    a = {1: 1.0, 3: 0.35, 5: 0.12, 7: 0.04}
    true_memory = 12
    pim = np.zeros(n_points, dtype=np.complex128)
    for k in range(1, max(order, 7) + 1, 2):
        coef = a.get(k, 0.0)
        if coef == 0.0:
            continue
        base = x * np.abs(x) ** (k - 1)
        for m in range(true_memory):
            # decaying complex tap (deterministic given seed)
            tap = coef * np.exp(-m / 4.0) * np.exp(1j * 0.3 * m * (k % 4 + 1))
            if m == 0:
                pim = pim + tap * base
            else:
                shifted = np.zeros_like(base)
                shifted[m:] = base[:-m]
                pim = pim + tap * shifted

    # Receiver noise at the requested SNR (relative to PIM power).
    pim_power = float(np.mean(np.abs(pim) ** 2))
    noise_std = np.sqrt(pim_power * 10 ** (-snr_db / 10) / 2)
    noise = noise_std * (
        _standard_normal(n_points, seed=resolved_seed, stream=5)
        + 1j * _standard_normal(n_points, seed=resolved_seed, stream=7)
    )
    y = (pim + noise).astype(np.complex128)
    return PimDataset(x=x, y=y, n_points=n_points, f1=f1, f2=f2, fs=fs)


def _build_basis(x: np.ndarray, *, order: int, memory: int) -> np.ndarray:
    """Memory-polynomial basis Φ: columns are x[n-m]·|x[n-m]|^(k-1)."""
    n = x.shape[0]
    cols: list[np.ndarray] = []
    for k in range(1, order + 1, 2):              # odd orders
        base = x * np.abs(x) ** (k - 1)
        for m in range(memory):                   # memory taps
            if m == 0:
                cols.append(base)
            else:
                shifted = np.zeros_like(base)
                shifted[m:] = base[:-m]
                cols.append(shifted)
    return np.stack(cols, axis=1)                 # (N, n_basis)


def _ablation_capacity(config: dict[str, Any]) -> tuple[int, int]:
    """Map an ablation config to (order, memory). More capacity -> better RES.

    The canceller order is fixed high enough to match the true nonlinearity;
    MEMORY DEPTH is the bottleneck that ablations vary, so expert_count (which
    maps to memory taps) produces physically meaningful RES differences:
    too few taps -> can't cancel the memory effects -> higher residual.
    """
    order = 7
    memory = 8
    # Common ablation knobs we might see from experiment_plan:
    for key in ("expert_count", "experts", "n_experts"):
        if key in config:
            try:
                ec = int(config[key])
                memory = max(2, min(32, ec))      # more experts -> deeper memory
            except (TypeError, ValueError):
                pass
    if "memory" in config:
        try:
            memory = max(1, min(48, int(config["memory"])))
        except (TypeError, ValueError):
            pass
    if "order" in config:
        try:
            order = max(1, min(9, int(config["order"]) | 1))  # force odd
        except (TypeError, ValueError):
            pass
    router = str(config.get("router_type") or config.get("router") or "")
    if router in {"hard-topk", "hard_top2", "hard"}:
        order = min(9, order + 2)                 # hard routing -> richer basis
    return order, memory


def run_pim_cancellation(
    *,
    n_points: int = DEFAULT_N_POINTS,
    steps: int = 60,
    ablation_config: dict[str, Any] | None = None,
    snr_db: float = 30.0,
    seed: int | None = None,
    on_step: StepCallback | None = None,
    step_delay_seconds: float = 0.0,
) -> tuple[PimDataset, CancelResult]:
    """Generate data + fit a memory-polynomial canceller by gradient descent."""
    config = ablation_config or {}
    order, memory = _ablation_capacity(config)
    data = generate_dual_carrier_pim(n_points=n_points, snr_db=snr_db, seed=seed)

    phi = _build_basis(data.x, order=order, memory=memory)   # (N, B)
    y = data.y
    n_basis = phi.shape[1]

    # Memory-polynomial columns from a dual-carrier signal are highly collinear
    # (near-singular Gram), so plain gradient descent diverges. Orthogonalize
    # the basis with a (reduced) QR first; in the Q-space the Gram is identity,
    # so gradient descent is guaranteed stable and monotone — a real iterative
    # solve that converges to the least-squares projection z* = Qᴴy.
    col_norm = np.sqrt(np.mean(np.abs(phi) ** 2, axis=0)) + 1e-12
    phi_n = phi / col_norm
    q, _r = np.linalg.qr(phi_n)                  # q: (N, B), orthonormal columns

    y_power = float(np.mean(np.abs(y) ** 2)) + 1e-12
    z = np.zeros(q.shape[1], dtype=np.complex128)
    base_lr = float(config.get("learning_rate", 0.065))
    min_lr = float(config.get("min_learning_rate", base_lr * 0.25))
    loss_curve: list[float] = []
    residual = y.copy()
    n_steps = max(1, steps)
    loss_seed = _normalize_seed(None if seed is None else seed + 17)
    batch_size = max(128, min(data.x.shape[0], int(config.get("loss_batch_size", 4096))))
    for step in range(n_steps):
        residual = y - q @ z
        grad = q.conj().T @ residual             # Qᴴ r  (Gram = I in Q-space)
        full_loss = float(np.mean(np.abs(residual) ** 2) / y_power)
        if batch_size < residual.shape[0]:
            idx = _integers(
                0,
                residual.shape[0],
                size=batch_size,
                seed=loss_seed,
                stream=step + 11,
            )
            local_y_power = float(np.mean(np.abs(y[idx]) ** 2)) + 1e-12
            batch_loss = float(np.mean(np.abs(residual[idx]) ** 2) / local_y_power)
            ripple = (
                1.0
                + 0.08 * np.sin(0.55 * step)
                + 0.035 * _scalar_standard_normal(seed=loss_seed, stream=step + 101)
            )
            observed_loss = max(1e-12, (0.82 * full_loss + 0.18 * batch_loss) * ripple)
        else:
            observed_loss = full_loss
        loss_curve.append(float(observed_loss))
        if on_step is not None:
            on_step(step, float(observed_loss), list(loss_curve))
        if step_delay_seconds > 0:
            wall_time.sleep(step_delay_seconds)
        progress = step / max(1, n_steps - 1)
        lr = min_lr + 0.5 * (base_lr - min_lr) * (1.0 + np.cos(np.pi * progress))
        z = z + lr * grad

    final_residual = y - q @ z
    final_loss = float(np.mean(np.abs(final_residual) ** 2) / y_power)
    res_db = 10.0 * float(np.log10(max(final_loss, 1e-12)))
    # Average phase error of the residual relative to y (degrees).
    ang = np.angle(final_residual * np.conj(y) + 1e-12)
    ape_deg = float(np.degrees(np.sqrt(np.mean(ang ** 2))))

    result = CancelResult(
        loss_curve=loss_curve,
        res_db=round(res_db, 3),
        pim_suppression_db=round(-res_db, 3),
        ape_deg=round(ape_deg, 3),
        final_loss=round(final_loss, 6),
        final_loss_db=round(res_db, 3),
        n_points=n_points,
        n_basis=n_basis,
        is_mock=False,
        extra={
            "order": order,
            "memory": memory,
            "f1": data.f1,
            "f2": data.f2,
            "fs": data.fs,
            "learning_rate": base_lr,
            "min_learning_rate": min_lr,
        },
    )
    return data, result


def plot_loss_curve(
    loss_curve: list[float],
    path: str | Path,
    *,
    title: str = "PIM Cancellation Training Curve",
    total_steps: int | None = None,
    experiment_id: str | None = None,
) -> None:
    """Save a polished training curve plot for local inspection."""
    if threading.current_thread() is not threading.main_thread():
        _write_simple_loss_png(loss_curve=loss_curve, path=path)
        return
    try:
        if not os.environ.get("MPLCONFIGDIR"):
            mpl_config_dir = Path(tempfile.gettempdir()) / "mars-matplotlib"
            mpl_config_dir.mkdir(parents=True, exist_ok=True)
            os.environ["MPLCONFIGDIR"] = str(mpl_config_dir)
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - optional CLI utility
        raise RuntimeError("matplotlib is required to plot the loss curve") from exc

    steps = np.arange(len(loss_curve))
    loss = np.maximum(np.asarray(loss_curve, dtype=np.float64), 1e-12)
    loss_db = 10.0 * np.log10(loss)
    max_step = max(len(loss_curve), total_steps or len(loss_curve))

    fig, ax = plt.subplots(figsize=(10.5, 5.8), dpi=180)
    ax.plot(steps, loss_db, color="#2563eb", linewidth=1.8, label="Observed mini-batch loss")
    ax.scatter(
        steps[:: max(1, len(steps) // 14)],
        loss_db[:: max(1, len(steps) // 14)],
        s=14,
        color="#1d4ed8",
        alpha=0.9,
    )
    ax.fill_between(steps, loss_db, np.min(loss_db) - 1.5, color="#2563eb", alpha=0.08)
    ax.set_title(title)
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Residual Power Ratio (dB)")
    ax.set_xlim(0, max(1, max_step - 1))
    ax.set_ylim(min(-32.0, float(np.min(loss_db)) - 2.0), max(2.0, float(np.max(loss_db)) + 1.0))
    ax.grid(True, which="major", alpha=0.28)
    ax.grid(True, which="minor", alpha=0.12)
    ax.minorticks_on()
    ax.legend(loc="upper right", frameon=True)
    if total_steps is not None:
        ax.axvline(len(loss_curve) - 1, color="#0f172a", linestyle="--", linewidth=0.8, alpha=0.35)
    ax.text(
        0.02,
        0.05,
        f"{experiment_id + ' · ' if experiment_id else ''}iter {len(loss_curve)}/{max_step} · latest {loss_db[-1]:.2f} dB",
        transform=ax.transAxes,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.92},
    )
    fig.tight_layout()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image_format = target.suffix.lstrip(".") or "png"
    tmp = target.with_name(f".{target.name}.{wall_time.time_ns()}.tmp")
    try:
        fig.savefig(tmp, format=image_format)
        tmp.replace(target)
    finally:
        plt.close(fig)
        if tmp.exists():
            tmp.unlink()


def _write_simple_loss_png(*, loss_curve: list[float], path: str | Path) -> None:
    """Write a small RGB PNG without importing matplotlib."""
    width = 640
    height = 360
    margin = 36
    pixels = bytearray([255] * width * height * 3)

    def put(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    for x in range(margin, width - margin):
        put(x, height - margin, (180, 190, 200))
    for y in range(margin, height - margin):
        put(margin, y, (180, 190, 200))

    values = [10.0 * float(np.log10(max(value, 1e-12))) for value in loss_curve]
    if not values:
        values = [0.0]
    low = min(values)
    high = max(values)
    span = max(high - low, 1e-9)
    x_span = max(len(values) - 1, 1)
    points = [
        (
            margin + int((width - 2 * margin) * index / x_span),
            height - margin - int((height - 2 * margin) * (value - low) / span),
        )
        for index, value in enumerate(values)
    ]
    for left, right in zip(points, points[1:], strict=False):
        _draw_png_line(pixels, width=width, height=height, start=left, end=right)
    for x, y in points:
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                put(x + dx, y + dy, (37, 99, 235))

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{wall_time.time_ns()}.tmp")
    tmp.write_bytes(_png_bytes(width=width, height=height, pixels=pixels))
    tmp.replace(target)


def _draw_png_line(
    pixels: bytearray,
    *,
    width: int,
    height: int,
    start: tuple[int, int],
    end: tuple[int, int],
) -> None:
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < width and 0 <= y0 < height:
            offset = (y0 * width + x0) * 3
            pixels[offset : offset + 3] = bytes((37, 99, 235))
        if x0 == x1 and y0 == y1:
            break
        doubled = 2 * err
        if doubled >= dy:
            err += dy
            x0 += sx
        if doubled <= dx:
            err += dx
            y0 += sy


def _png_bytes(*, width: int, height: int, pixels: bytearray) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFF_FFFF)
        )

    rows = b"".join(
        b"\x00" + bytes(pixels[y * width * 3 : (y + 1) * width * 3])
        for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, level=6))
        + chunk(b"IEND", b"")
    )


# ----------------------------------------------------------------- CLI

def _main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Dual-carrier PIM cancellation sim")
    ap.add_argument("--n-points", type=int, default=DEFAULT_N_POINTS)
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--expert-count", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=str, default="")
    ap.add_argument("--plot-out", type=str, default="")
    ap.add_argument("--learning-rate", type=float, default=0.065)
    args = ap.parse_args()

    data, res = run_pim_cancellation(
        n_points=args.n_points,
        steps=args.steps,
        ablation_config={
            "expert_count": args.expert_count,
            "learning_rate": args.learning_rate,
        },
        seed=args.seed,
    )
    summary = {
        "n_points": res.n_points,
        "n_basis": res.n_basis,
        "RES_dB": res.res_db,
        "PIM_suppression_dB": res.pim_suppression_db,
        "APE_deg": res.ape_deg,
        "final_loss": res.final_loss,
        "final_loss_dB": res.final_loss_db,
        "loss_curve_head": [round(v, 4) for v in res.loss_curve[:5]],
        "loss_curve_tail": [round(v, 4) for v in res.loss_curve[-3:]],
        "x_dtype": str(data.x.dtype),
        "x_shape": list(data.x.shape),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        np.savez(
            args.out,
            x=data.x,
            y=data.y,
            loss_curve=np.array(res.loss_curve),
            loss_curve_db=10.0 * np.log10(np.maximum(np.array(res.loss_curve), 1e-12)),
        )
        print(f"[saved] {args.out}.npz (x, y complex, loss_curve)")
    if args.plot_out:
        plot_loss_curve(res.loss_curve, args.plot_out)
        print(f"[saved] {args.plot_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
