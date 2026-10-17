"""Presentation-grade chart renders for report exports (PNG bytes).

All charts are drawn from the recorded data pack only; no synthesized points.
Fonts fall back to any installed CJK font so labels stay readable on Windows.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

_PALETTE = ["#475FA8", "#279685", "#BE7A34", "#8465A6", "#478DA6", "#B85B65"]
_GRID = "#D8DEE9"
_TEXT = "#18243A"
_font_configured = False


def _setup_font() -> None:
    global _font_configured
    if _font_configured:
        return
    candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC", "Arial Unicode MS"]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            plt.rcParams["font.family"] = [name, "DejaVu Sans"]
            break
    else:
        plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["axes.unicode_minus"] = False
    _font_configured = True


def _figure(width: float, height: float) -> tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
    _setup_font()
    figure, axis = plt.subplots(figsize=(width, height), dpi=160)
    figure.patch.set_facecolor("white")
    axis.set_facecolor("white")
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(_GRID)
    axis.tick_params(colors="#475569", labelsize=9)
    axis.grid(axis="y", color=_GRID, linewidth=0.8, alpha=0.7)
    axis.set_axisbelow(True)
    return figure, axis


def _png(figure: matplotlib.figure.Figure) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)
    return buffer.getvalue()


def _short(label: str, limit: int = 22) -> str:
    text = str(label).replace("_", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def metric_comparison_bars(
    rows: list[dict[str, Any]],
    metric: str,
    *,
    title: str,
    unit: str = "",
    better: str = "higher",
    highlight: str | None = None,
    threshold: float | None = None,
    threshold_label: str | None = None,
    baseline: str | None = None,
) -> bytes:
    """Vertical bars comparing one metric across experiments, deltas vs baseline."""
    labels = [_short(row.get("experiment_id", "?")) for row in rows]
    values: list[float] = []
    for row in rows:
        value = row.get(metric)
        if isinstance(value, (int, float)):
            values.append(float(value))
    if len(values) != len(rows):
        raise ValueError(f"metric {metric} missing in some rows")
    baseline_value = None
    if baseline is not None:
        for row in rows:
            if row.get("experiment_id") == baseline and isinstance(row.get(metric), (int, float)):
                baseline_value = float(row[metric])
    figure, axis = _figure(8.6, 4.6)
    legend_handles: list[Any] = []
    from matplotlib.patches import Patch

    for row in rows:
        if highlight and row.get("experiment_id") == highlight:
            color, label = "#BE7A34", "本轮候选"
        elif baseline and row.get("experiment_id") == baseline:
            color, label = "#94A3B8", "基线对照"
        else:
            color, label = "#279685", "其他实验臂"
        if not any(str(h.get_label()) == label for h in legend_handles):
            legend_handles.append(Patch(facecolor=color, label=label))
    colors = []
    for row in rows:
        if highlight and row.get("experiment_id") == highlight:
            colors.append("#BE7A34")
        elif baseline and row.get("experiment_id") == baseline:
            colors.append("#94A3B8")
        else:
            colors.append("#279685")
    bars = axis.bar(labels, values, color=colors, width=0.58, zorder=3)
    top = max(values) if values else 1.0
    bottom = min(values) if values else 0.0
    span = max(top - bottom, abs(top) * 0.02, 1e-9)
    axis.set_ylim(max(0.0, bottom - span * 0.35), top + span * 0.45)
    for bar, value in zip(bars, values):
        delta = ""
        if baseline_value is not None and not math.isclose(value, baseline_value, rel_tol=0, abs_tol=1e-12):
            diff = value - baseline_value
            delta = f"  ({'+' if diff >= 0 else ''}{diff:.4g})"
        axis.annotate(
            f"{value:.4g}{delta}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center", va="bottom", fontsize=9.5, color=_TEXT, xytext=(0, 3), textcoords="offset points")
    if threshold is not None and math.isfinite(threshold):
        axis.axhline(threshold, color="#B85B65", linestyle="--", linewidth=1.4, zorder=2)
        axis.annotate(threshold_label or f"门限 {threshold:.6g}", (0.995, threshold),
                      xycoords=("axes fraction", "data"), ha="right", va="bottom",
                      fontsize=9, color="#B85B65")
    arrow = "↑ 越高越好" if better == "higher" else "↓ 越低越好"
    axis.set_ylabel(f"{title}（{unit}）" if unit else title, fontsize=10, color=_TEXT)
    axis.set_title(f"{title} · {arrow}", fontsize=12.5, color=_TEXT, fontweight="bold", loc="left", pad=12)
    if len(legend_handles) > 1:
        axis.legend(handles=legend_handles, frameon=False, fontsize=9, loc="upper left", bbox_to_anchor=(0.0, 1.0))
    return _png(figure)


def convergence_lines(curves: list[dict[str, Any]], *, title: str = "训练收敛曲线") -> bytes:
    """Loss-vs-step lines for every recorded experiment curve."""
    figure, axis = _figure(8.6, 4.6)
    plotted = 0
    for index, curve in enumerate(curves):
        points = [p for p in curve.get("points", []) if isinstance(p, dict)
                  and isinstance(p.get("loss"), (int, float))]
        if not points:
            continue
        x_key = "sample_index" if isinstance(points[0].get("sample_index"), (int, float)) else "optimizer_step"
        xs = [float(p.get(x_key, i)) for i, p in enumerate(points)]
        ys = [float(p["loss"]) for p in points]
        label = _short(curve.get("experiment_id", f"curve {index + 1}"))
        axis.plot(xs, ys, marker="o", markersize=3.5, linewidth=1.8, color=_PALETTE[index % len(_PALETTE)], label=label)
        plotted += 1
    if not plotted:
        raise ValueError("no curve points recorded")
    axis.set_xlabel("记录点序号 / 优化器步数", fontsize=10, color=_TEXT)
    axis.set_ylabel("记录损失（原始线性值）", fontsize=10, color=_TEXT)
    axis.set_title(title, fontsize=12.5, color=_TEXT, fontweight="bold", loc="left", pad=12)
    axis.legend(frameon=False, fontsize=9)
    return _png(figure)


def model_diagram(family_terms: list[str], *, shared_note: str = "共享 L30 · Nspline=32 样条") -> bytes:
    """Block diagram of the PP-prior PIM cancellation architecture."""
    _setup_font()
    figure, axis = plt.subplots(figsize=(9.4, 4.9), dpi=160)
    axis.set_xlim(0, 100)
    axis.set_ylim(0, 56)
    axis.axis("off")
    figure.patch.set_facecolor("white")

    def box(x: float, y: float, w: float, h: float, text: str, *, color: str = "#475FA8", fill: str = "#EEF2FF", size: float = 10) -> None:
        axis.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6",
                                      linewidth=1.6, edgecolor=color, facecolor=fill))
        axis.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=_TEXT)

    def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "") -> None:
        axis.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=16,
                                       linewidth=1.5, color="#64748B"))
        if label:
            axis.text((x1 + x2) / 2, (y1 + y2) / 2 + 1.2, label, ha="center", va="bottom", fontsize=8.5, color="#64748B")

    box(2, 24, 13, 9, "TX 基带 x(t)\n16 通道", color="#334155", fill="#F1F5F9")
    arrow(15.6, 28.5, 21.5, 28.5)
    terms_text = "\n".join(f"Φ{k + 1}: {name}" for k, name in enumerate(family_terms)) or "PP 频移族基项"
    box(21.5, 18, 24, 21, f"PP 频移族基项\n（载波分辨）\n{terms_text}", fill="#EEF2FF")
    arrow(46.1, 28.5, 52, 28.5)
    box(52, 22, 18, 13, f"LUT 样条\n{shared_note}", fill="#ECFDF5", color="#279685")
    arrow(70.6, 28.5, 76.5, 28.5)
    box(76.5, 24, 15, 9, "Σ PIM 估计 ŷ", fill="#FFF7ED", color="#BE7A34")
    box(76.5, 6, 15, 9, "RX r(t)\n= PIM + 噪声", color="#334155", fill="#F1F5F9")
    arrow(84, 15.6, 84, 23.4, "对消 r − ŷ")
    arrow(91.5, 28.5, 96, 28.5)
    axis.text(96.4, 28.5, "→ 残余 RES", ha="left", va="center", fontsize=10, color=_TEXT, fontweight="bold")
    axis.text(2, 50, "PP 先验 PIM 对消模型结构（按报告方法节绘制）", fontsize=12.5, color=_TEXT, fontweight="bold")
    axis.text(2, 46.2, "目标：以真实频移先验构造可学习族基项，在参数预算内最大化对消增益", fontsize=9, color="#64748B")
    return _png(figure)


def path_timeline(steps: list[dict[str, Any]]) -> bytes:
    """Horizontal timeline of attempted approaches (from recorded experiment roles)."""
    _setup_font()
    figure, axis = plt.subplots(figsize=(9.4, 3.4), dpi=160)
    axis.set_xlim(0, 100)
    axis.set_ylim(0, 34)
    axis.axis("off")
    figure.patch.set_facecolor("white")
    if not steps:
        raise ValueError("no path steps")
    count = len(steps)
    span = 88 / count
    for index, step in enumerate(steps):
        x = 6 + index * span
        color = _PALETTE[index % len(_PALETTE)]
        axis.add_patch(FancyBboxPatch((x, 12), span - 5, 13, boxstyle="round,pad=0.5",
                                      linewidth=1.5, edgecolor=color, facecolor="white"))
        title = _short(step.get("title", "?"), 16)
        note = _short(step.get("note", ""), 26)
        axis.text(x + (span - 5) / 2, 20.5, title, ha="center", va="center", fontsize=10, color=color, fontweight="bold")
        axis.text(x + (span - 5) / 2, 15.5, note, ha="center", va="center", fontsize=8, color="#64748B")
        if index < count - 1:
            axis.add_patch(FancyArrowPatch((x + span - 4.6, 18.5), (x + span - 0.6, 18.5),
                                           arrowstyle="-|>", mutation_scale=14, linewidth=1.4, color="#94A3B8"))
    axis.text(6, 30, "尝试的方案路径（按实验角色排序）", fontsize=12.5, color=_TEXT, fontweight="bold")
    return _png(figure)


def write_chart(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path
