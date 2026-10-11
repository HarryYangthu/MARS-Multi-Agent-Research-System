"""Derived scientific figures from saved measurements; never runs or approves a job.

The fixed-window PIM export adapter checks its recomputed values against the
receipt-bound channel table. An unknown protocol or mismatch omits validation
figures instead of silently changing the scientific measurement definition.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
import zipfile

import numpy as np
from numpy.typing import NDArray
import yaml

from app.settings import repo_root

Array = NDArray[np.float64]


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def db_mean(values: Array) -> float:
    """Average linear powers, then convert to dB; not an arithmetic dB mean."""
    if not values.size or not np.isfinite(values).all():
        raise ValueError("Expected finite power ratios")
    return float(10 * np.log10(np.mean(np.power(10.0, values / 10))))


def training_rows(data: bytes) -> list[dict[str, float]]:
    # A concurrent writer may be halfway through the last row.
    text = data.decode("utf-8-sig")
    text = text[:text.rfind("\n") + 1]
    reader = csv.DictReader(io.StringIO(text))
    keys = ["updates", "mean_loss_sum", "train_PIM_native", "train_RES_native"]
    if not reader.fieldnames or not set(keys).issubset(reader.fieldnames):
        raise ValueError("Unsupported training.csv columns")
    rows = []
    for raw in reader:
        if None in raw or any(value is None for value in raw.values()):
            continue
        row = {key: float(raw[key]) if raw[key] else float("nan") for key in keys}
        if np.isfinite(row["updates"]) and row["updates"] >= 0 and row["updates"].is_integer():
            rows.append(row)
    return rows


def _source(directory: Path, name: str, policy: dict[str, Any]) -> bytes:
    path = directory / name
    if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Unsafe figure source")
    if path.stat().st_size > int(policy["max_source_bytes"]):
        raise ValueError("Figure source exceeds size policy")
    return path.read_bytes()


def verified_sources(directory: Path, policy: dict[str, Any]) -> dict[str, bytes]:
    receipt_data = _source(directory, "execution_receipt.json", policy)
    receipt = json.loads(receipt_data)
    if (receipt.get("schema") != "local_command_receipt.v1" or receipt.get("status") != "completed"
            or receipt.get("returncode") != 0 or receipt.get("invocation_id") != directory.name
            or receipt.get("experiment_id") != directory.parent.name
            or receipt.get("run_id") != directory.parents[3].name):
        raise ValueError("No matching completed process receipt")
    result_data = _source(directory, "result.json", policy)
    result = json.loads(result_data)
    if (_digest(result_data) != receipt.get("result_sha256") or result.get("status") != "completed"
            or any(result.get(key) != receipt.get(key) for key in ("run_id", "experiment_id", "invocation_id"))):
        raise ValueError("Result identity/hash mismatch")
    sources = {"execution_receipt.json": receipt_data, "result.json": result_data}
    for name in ("evaluation.json", "config.json", "channel_performance.csv", "validation_signals.npz"):
        data = _source(directory, name, policy)
        expected = (directory / name).resolve()
        records = [row for row in receipt.get("evidence", []) if Path(row.get("path", "")).resolve() == expected]
        if len(records) != 1 or records[0].get("sha256") != _digest(data) or records[0].get("bytes") != len(data):
            raise ValueError(f"Receipt evidence mismatch: {name}")
        sources[name] = data
    return sources


def validation_data(sources: dict[str, bytes], policy: dict[str, Any]) -> dict[str, Any]:
    from scipy.signal import welch

    cfg, evaluation = (json.loads(sources[name]) for name in ("config.json", "evaluation.json"))
    protocol = evaluation["protocol"]
    if protocol["residual"] != "numpy.convolve(prefiltered_RX - prediction, FIR, same)":
        raise ValueError("Unsupported residual protocol")
    centers = np.asarray(protocol["centers_mhz"], dtype=float)
    width, fs = float(protocol["width_mhz"]), float(cfg["Fs_MHz"])
    channels, trim = int(cfg["Nrx"]), int(protocol["trim_each_edge"])
    if (not np.isfinite(centers).all() or not centers.size or width <= 0 or not np.isfinite(width)
            or fs <= 0 or not np.isfinite(fs) or trim < 0 or channels <= 0):
        raise ValueError("Invalid validation protocol")
    with zipfile.ZipFile(io.BytesIO(sources["validation_signals.npz"])) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > int(policy["max_unpacked_bytes"]):
            raise ValueError("Validation signals exceed unpacked size policy")
    with np.load(io.BytesIO(sources["validation_signals.npz"]), allow_pickle=False) as saved:
        # RX / NF / prediction: [band * receiver, time]; FIR: [band, taps].
        rx, noise, prediction, fir = (saved[key] for key in ("rx", "nf", "prediction", "fir"))
    if (rx.ndim != 2 or noise.shape != rx.shape or prediction.shape != rx.shape or fir.ndim != 2
            or rx.shape[0] != channels * len(centers) or fir.shape[0] != len(centers)
            or rx.shape[0] > int(policy["max_channels"]) or rx.shape[1] > int(policy["max_samples"])
            or fir.shape[1] > rx.shape[1] or any(not np.isfinite(a).all() for a in (rx, noise, prediction, fir))):
        raise ValueError("Invalid validation signal shapes/values")
    effective = rx.shape[1] - 2 * trim
    settings = policy["welch"]
    if effective < int(settings["segment"]) or effective != protocol["points"]:
        raise ValueError("Validation window mismatch")
    # Rebuild the documented residual, not the separate native-protocol error array.
    residual = np.stack([np.convolve(row, fir[index // channels], mode="same")
                         for index, row in enumerate(rx - prediction)])
    spectra = []
    for values in (rx, residual, noise):
        freq, psd = welch(values[:, trim:rx.shape[1] - trim].astype(np.complex128), fs=fs,
                          window=np.kaiser(settings["segment"], settings["kaiser_beta"]),
                          nperseg=settings["segment"], noverlap=settings["overlap"], nfft=settings["fft"],
                          detrend=False, return_onesided=False, scaling="density", axis=-1)
        spectra.append(np.asarray(psd, dtype=float))
    powers = []
    for psd in spectra:
        per_band = []
        for band, center in enumerate(centers):
            mask = (freq > center - width / 2) & (freq < center + width / 2)
            if not np.any(mask):
                raise ValueError("Validation band contains no frequency bins")
            per_band.append(10 * np.log10(psd[band * channels:(band + 1) * channels, mask].mean(axis=-1) * fs))
        powers.append(np.concatenate(per_band))
    pim, res = powers[0] - powers[2], powers[1] - powers[2]
    records = list(csv.DictReader(io.StringIO(sources["channel_performance.csv"].decode("utf-8-sig"))))
    table = np.asarray([[float(row[key]) for key in ("channel", "PIM", "RES", "improvement_db")] for row in records])
    expected = np.column_stack((np.arange(len(pim)), pim, res, pim - res))
    if table.shape != expected.shape or not np.allclose(table, expected, rtol=1e-6, atol=1e-6):
        raise ValueError("Recomputed spectrum disagrees with saved channel metrics")
    if not np.allclose([db_mean(pim), db_mean(res), db_mean(pim) - db_mean(res)],
                       [evaluation[key] for key in ("PIM", "RES", "improvement_db")], rtol=1e-6, atol=1e-6):
        raise ValueError("Aggregate metric mismatch")
    # Compare experiments only when the actual validation RX/NF and protocol match.
    comparison = _digest(rx.tobytes() + noise.tobytes() + json.dumps(protocol, sort_keys=True).encode() + str(fs).encode())
    order = np.argsort(freq)
    return {"pim": pim, "res": res, "freq": freq[order], "spectra": [a[:, order] for a in spectra],
            "centers": centers, "width": width, "channels": channels, "evaluation": evaluation,
            "comparison_key": comparison, "fs": fs}


def render_research_figures(execution: Path) -> dict[str, Any]:
    """Write only execution/visualizations; keep raw artifacts and run state intact."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    policy = yaml.safe_load((repo_root() / "configs/research_figures.yaml").read_text(encoding="utf-8"))
    execution = execution.resolve(strict=True)
    output = execution / "visualizations"
    if output.is_symlink() or not output.resolve().is_relative_to(execution):
        raise ValueError("Unsafe visualization output")
    output.mkdir(exist_ok=True)
    manifest: dict[str, Any] = {"schema": "research_figures.v1", "figures": [], "limitations": [], "sources": {}}
    traces, validations = [], []
    folders = sorted((execution / "local_commands").glob("*/*"))[-int(policy["max_invocations"]):]
    for folder in folders:
        if folder.is_symlink() or folder.parent.is_symlink() or not folder.resolve().is_relative_to(execution):
            continue
        label = f"{folder.parent.name} / {folder.name[:8]}"
        try:
            data = _source(folder, "training.csv", policy)
            rows = training_rows(data)
            manifest["sources"][(folder.relative_to(execution) / "training.csv").as_posix()] = _digest(data)
            status = "unfinished"
            if (folder / "execution_receipt.json").exists():
                status = json.loads(_source(folder, "execution_receipt.json", policy)).get("status", "unknown")
            if rows:
                traces.append((label, rows, status))
            sources = verified_sources(folder, policy)
            validation = validation_data(sources, policy)
            validation.update(label=label, slug=re.sub(r"[^A-Za-z0-9_-]", "_", folder.parent.name + "_" + folder.name[:8]))
            validations.append(validation)
            for name, value in sources.items():
                manifest["sources"][str((folder / name).relative_to(execution)).replace("\\", "/")] = _digest(value)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            reason = "Required export file not yet available" if isinstance(exc, OSError) else str(exc)
            manifest["limitations"].append(f"{label}: validation figures unavailable ({reason})")

    def save(fig: Any, name: str, title: str, caption: str) -> None:
        for extension in ("svg", "png"):
            temporary_image = output / f"{name}.{extension}.tmp"
            fig.savefig(temporary_image, format=extension, dpi=policy["dpi"], bbox_inches="tight")
            temporary_image.replace(output / f"{name}.{extension}")
        plt.close(fig)
        manifest["figures"].append({"path": f"visualizations/{name}.svg", "png": f"visualizations/{name}.png",
                                    "title": title, "caption": caption,
                                    "sha256": _digest((output / f"{name}.svg").read_bytes())})

    def layout(title: str, subtitle: str, footer: str, count: int = 2) -> tuple[Any, Any]:
        fig, axes = plt.subplots(1, count, figsize=(12.8, 6.7), squeeze=False)
        fig.subplots_adjust(left=.075, right=.96, top=.76, bottom=.19, wspace=.3)
        fig.suptitle(title, x=.06, y=.955, ha="left", fontsize=19, fontweight="bold")
        fig.text(.06, .858, subtitle, fontsize=11, color="#475569", linespacing=1.6)
        fig.text(.06, .055, footer, fontsize=9, color="#475569", linespacing=1.5)
        for ax in axes.flat:
            ax.spines[["top", "right"]].set_visible(False)
            ax.grid(axis="y", alpha=.18)
            ax.set_axisbelow(True)
        return fig, axes[0]

    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "path",
                         "axes.edgecolor": "#cbd5e1", "text.color": "#0f172a", "axes.labelcolor": "#334155"}):
        if traces:
            fig, axes = layout("Training convergence | measured records", "Actual optimizer updates on the x-axis. Failed and unfinished jobs remain visible.",
                               "Source: training.csv snapshots (hashes in figures.json). Unsmoothed; missing loss is not replaced by zero.\nTraining native protocol only; these curves do not establish final validation performance.")
            for index, (label, rows, status) in enumerate(traces):
                x = [r["updates"] for r in rows]
                for ax, key in zip(axes, ("mean_loss_sum", "train_RES_native")):
                    ax.plot(x, [r[key] for r in rows], marker="o", markersize=3,
                            linestyle=("-", "--", "-.", ":")[index % 4], label=f"{label.split(' / ')[0]} [{status}]")
                    ax.set_xlabel("Optimizer updates")
            axes[0].set_ylabel("Mean training loss (linear)")
            axes[1].set_ylabel("Residual above noise floor (dB)")
            axes[0].legend(fontsize=8, frameon=False, loc="upper left")
            save(fig, "training_overview", "训练收敛与残余功率", "按实际更新次数对齐各实验；失败作业也保留。损失与训练残余功率分开绘制，不能当作最终验证结论。")
        for item in validations:
            pim, res, evaluation = item["pim"], item["res"], item["evaluation"]
            protocol = evaluation["protocol"]
            footer = (f"Fixed-window validation: centers {item['centers'].tolist()} MHz; width {item['width']:g} MHz; "
                      f"trim {protocol['trim_each_edge']} samples/edge; N={protocol['points']}.\n"
                      "Sources: receipt-bound evaluation.json, channel_performance.csv, validation_signals.npz; see figures.json for SHA-256.")
            fig, axes = layout(f"Channel cancellation | {item['label'].split(' / ')[0]}",
                               f"Aggregate PIM {evaluation['PIM']:.2f} dB  →  RES {evaluation['RES']:.2f} dB  |  Reduction {evaluation['improvement_db']:.2f} dB\n"
                               "Aggregate = linear-power mean, converted to dB. One run; no confidence intervals.", footer)
            channels = np.arange(1, len(pim) + 1)
            axes[0].vlines(channels, res, pim, color="#cbd5e1", linewidth=3)
            axes[0].scatter(channels, pim, label="Before: PIM", color="#475569", marker="s")
            axes[0].scatter(channels, res, label="After: RES", color="#0891b2", marker="o")
            axes[0].axhline(0, color="#94a3b8", linestyle="--", label="Noise floor")
            axes[0].set_ylabel("Band power above noise floor (dB)")
            axes[0].legend(fontsize=9, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.01), ncol=3)
            delta = pim - res
            axes[1].bar(channels, delta, color=np.where(delta >= 0, "#0891b2", "#dc2626"), width=.65)
            axes[1].axhline(0, color="#64748b", linewidth=1)
            axes[1].set_ylabel("PIM − RES per channel (dB)")
            worst = int(np.argmax(res))
            axes[0].annotate(f"Highest RES: CH {worst + 1}\n{res[worst]:.2f} dB", (channels[worst], res[worst]),
                             xytext=(8, 12), textcoords="offset points", fontsize=9)
            for ax in axes:
                ax.set_xticks(channels[::max(1, len(channels) // 16)])
                ax.set_xlabel("Receiver channel (1-based, band-major)")
            save(fig, item["slug"] + "_channels", f"{item['label']} · 各通道对消效果",
                 "左图对照每个通道的对消前/后带内功率，虚线为噪声底；右图显示改善量。最高残余通道单独标注；均值在线性功率域计算。")
            for band, center in enumerate(item["centers"]):
                fig, axes = layout(f"Validation spectrum | {item['label'].split(' / ')[0]} | band {band + 1}",
                                   "Before cancellation, recomputed residual, and measured noise floor.\nMean PSD across receivers in linear power; no physical dBm calibration.", footer)
                zoom_values = []
                zoom_mask = np.abs(item["freq"] - center) <= item["width"]
                for psd, color, style, label in zip(item["spectra"], ("#475569", "#0891b2", "#d97706"),
                                                     ("--", "-", ":"), ("Before (RX)", "After (residual)", "Noise floor")):
                    # PSD: [receiver, frequency]; reduce receivers BEFORE taking log10.
                    mean = psd[band * item["channels"]:(band + 1) * item["channels"]].mean(axis=0)
                    curve = 10 * np.log10(np.maximum(mean, np.finfo(float).tiny))
                    zoom_values.extend(curve[zoom_mask].tolist())
                    for ax in axes:
                        ax.plot(item["freq"], curve, color=color, linestyle=style, linewidth=1.6, label=label)
                for ax in axes:
                    ax.axvspan(center - item["width"] / 2, center + item["width"] / 2, color="#0891b2", alpha=.08)
                    ax.set_xlabel("Baseband frequency (MHz)")
                    ax.set_ylabel("Digital PSD (dB/MHz, uncalibrated)")
                axes[0].set_title("Full sampled spectrum", fontsize=11)
                axes[0].legend(fontsize=9, frameon=False, loc="upper right")
                axes[1].set_xlim(center - item["width"], center + item["width"])
                padding = max(1, (max(zoom_values) - min(zoom_values)) * .15)
                axes[1].set_ylim(min(zoom_values) - padding, max(zoom_values) + padding)
                axes[1].set_title("Observation band detail (shaded = scoring band)", fontsize=10)
                save(fig, item["slug"] + f"_spectrum_{band + 1}", f"{item['label']} · 频谱与观测带宽",
                     "全频谱与目标频带放大图并列，展示对消前、按最终验证协议重算的残余、实测噪声底。功率先跨通道平均再转 dB；未标定为 dBm。")
        if validations and len({v["comparison_key"] for v in validations}) == 1:
            fig, axes = layout("Comparable validation runs | measured outcomes", "Same saved RX/noise samples and fixed-window protocol. Lower RES is better.\nOnly completed, receipt-consistent exports are included; no missing baseline is fabricated.",
                               "Source: receipt-bound evaluation.json; exact inputs and hashes recorded in figures.json.\nSingle runs, not statistical significance or scientific acceptance. Failed/unfinished runs remain in the training figure.")
            labels = [v["label"].split(" / ")[0] + "\n" + v["label"].split(" / ")[1] for v in validations]
            for ax, key, ylabel in zip(axes, ("RES", "improvement_db"), ("Residual above noise (dB)", "Cancellation improvement (dB)")):
                values = [float(v["evaluation"][key]) for v in validations]
                bars = ax.bar(labels, values, color="#0891b2", width=.55)
                ax.bar_label(bars, fmt="%.2f", padding=4)
                ax.margins(y=.2)
                ax.set_ylabel(ylabel)
                ax.tick_params(axis="x", labelsize=8)
            save(fig, "validation_comparison", "同口径验证对比", "只纳入已完成且证据哈希一致的作业，并检查验证输入和协议相同。缺少完整验证结果的实验不会代入训练指标冒充成绩。")
        elif validations:
            manifest["limitations"].append("Validation inputs/protocols differ; no cross-experiment comparison generated.")
    temporary = output / "figures.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output / "figures.json")
    return manifest


if __name__ == "__main__":
    import argparse
    from loguru import logger
    parser = argparse.ArgumentParser(description="Render derived figures from saved execution evidence")
    parser.add_argument("execution", type=Path)
    args = parser.parse_args()
    result = render_research_figures(args.execution)
    logger.info("Saved {} figures; {} limitations", len(result["figures"]), len(result["limitations"]))
