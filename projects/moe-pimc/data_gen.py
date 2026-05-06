"""Synthetic PIM data generator for the moe-pimc project.

Lives in the project repo so MARS can drive Mock Simulation without
touching production data. Pure Python + numpy; no GPU required.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np


@dataclass
class PimSignal:
    x: np.ndarray
    y: np.ndarray
    stream_label: int


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


if __name__ == "__main__":
    sigs = generate(n_samples=128, streams=2, seed=42)
    for s in sigs:
        print(f"stream={s.stream_label} x.shape={s.x.shape} y.var={s.y.var():.4f}")
    print("loss[:5]=", loss_curve(steps=5, seed=42))
