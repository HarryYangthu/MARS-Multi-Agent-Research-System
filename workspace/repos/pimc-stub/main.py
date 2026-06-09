"""Real (small) CPU training driver for the moe-pimc stub repo.

This is the in-repo *synthetic* trainer (CLAUDE.md #6 allows synthetic data
scripts in the repo). MARS's real execution backend invokes it as a
subprocess; it trains a tiny 1-hidden-layer MLP to cancel a synthetic
polynomial PIM signal and reports genuinely-computed metrics.

CLI contract (the seam app/execution/simulation_runner.py drives):
    python main.py --experiment-id E --config '{...}' --seed S \
        --output-dir DIR --steps N

Outputs in DIR:
    metrics.json  {loss, RES, PIM, APE}
    loss.json     {"values": [...per-step loss...]}

Streams per-step progress to stdout as ``@curve <step> <loss>`` lines so the
runner can forward live curve points to the UI. numpy-only; CPU; seconds.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


def _polynomial_pim(x: np.ndarray, *, order: int = 5) -> np.ndarray:
    """Volterra-style odd-order polynomial nonlinearity (the PIM to cancel)."""
    out = np.zeros_like(x)
    for k in range(1, order + 1, 2):
        out = out + (1.0 / math.factorial(k)) * np.power(x, k)
    return out


def _make_data(n: int, snr_db: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    x = rng.standard_normal(n).astype(np.float64)
    clean = _polynomial_pim(x)
    noise_var = float(np.var(clean)) * 10 ** (-snr_db / 10)
    y = clean + rng.normal(0.0, math.sqrt(max(noise_var, 1e-12)), size=n)
    return x, y


def train(
    *,
    experiment_id: str,
    config: dict,
    seed: int,
    output_dir: Path,
    steps: int,
    n_samples: int = 512,
) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    x, y = _make_data(n_samples, float(config.get("snr_db", 30.0)), rng)

    # Polynomial feature lift so a tiny MLP can fit the odd nonlinearity.
    phi = np.stack([x, x**3, x**5], axis=1)  # (N, 3)
    phi = (phi - phi.mean(0)) / (phi.std(0) + 1e-8)
    y_mean = float(y.mean())
    y_std = float(y.std()) + 1e-8
    y_n = (y - y_mean) / y_std

    hidden = int(config.get("hidden", 16))
    lr = float(config.get("lr", 0.1))
    w1 = rng.standard_normal((3, hidden)) * 0.5
    b1 = np.zeros(hidden)
    w2 = rng.standard_normal((hidden, 1)) * 0.5
    b2 = np.zeros(1)

    n = phi.shape[0]
    losses: list[float] = []
    for step in range(steps):
        # forward
        z1 = phi @ w1 + b1            # (N, H)
        h = np.tanh(z1)
        yhat = (h @ w2 + b2).ravel()  # (N,)
        resid = yhat - y_n
        loss = float(np.mean(resid**2))
        losses.append(loss)
        print(f"@curve {step} {loss:.6f}", flush=True)

        # backward (MSE)
        g_yhat = (2.0 / n) * resid          # (N,)
        g_w2 = h.T @ g_yhat[:, None]        # (H, 1)
        g_b2 = np.array([g_yhat.sum()])
        g_h = g_yhat[:, None] @ w2.T        # (N, H)
        g_z1 = g_h * (1.0 - h**2)           # tanh'
        g_w1 = phi.T @ g_z1                 # (3, H)
        g_b1 = g_z1.sum(0)
        w2 -= lr * g_w2
        b2 -= lr * g_b2
        w1 -= lr * g_w1
        b1 -= lr * g_b1

    # Final metrics in physical units (de-normalised residual).
    z1 = phi @ w1 + b1
    yhat = (np.tanh(z1) @ w2 + b2).ravel() * y_std + y_mean
    residual = y - yhat
    res_power = float(np.mean(residual**2))
    sig_power = float(np.mean(y**2)) + 1e-12
    metrics = {
        "loss": losses[-1] if losses else 0.0,
        "RES": 10.0 * math.log10(res_power / sig_power),      # residual suppression (dB)
        "PIM": 10.0 * math.log10(res_power + 1e-12),          # residual PIM power (dB)
        "APE": float(np.mean(np.abs(residual) / (np.abs(y) + 1e-6)) * 100.0),  # avg % error
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (output_dir / "loss.json").write_text(json.dumps({"values": losses}), encoding="utf-8")
    print(
        f"@done {experiment_id} loss={metrics['loss']:.6f} RES={metrics['RES']:.2f}dB",
        flush=True,
    )
    return metrics


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--config", default="{}")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--n-samples", type=int, default=512)
    args = p.parse_args()
    try:
        config = json.loads(args.config) if args.config else {}
        if not isinstance(config, dict):
            config = {}
    except json.JSONDecodeError:
        config = {}
    train(
        experiment_id=args.experiment_id,
        config=config,
        seed=args.seed,
        output_dir=Path(args.output_dir),
        steps=args.steps,
        n_samples=args.n_samples,
    )


if __name__ == "__main__":
    main()
