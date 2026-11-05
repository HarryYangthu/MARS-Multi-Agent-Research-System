"""Cell formatting shared by the Word, PowerPoint and Excel writers."""
from __future__ import annotations

from typing import Any


def num(value: Any, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else ("—" if value in (None, "") else str(value))


def signed(value: Any, digits: int = 3) -> str:
    return f"{value:+.{digits}f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "—"


def pair(value: dict[str, Any] | None, digits: int = 3, sign: bool = False) -> str:
    """Per-dataset values in dataset order, e.g. "0.553 / 0.555"; any number of datasets."""
    value = value or {}
    show = signed if sign else num
    from mars_app.artifacts import dataset_order
    keys = sorted(value, key=dataset_order)
    return " / ".join(show(value[d], digits) for d in keys) or "—"


def datasets_label(datasets: list[str]) -> str:
    """"a/b" for column headers; datasets in the run's order."""
    return "/".join(datasets) or "—"


def metric_label(metrics: dict[str, Any], name: str) -> str:
    meta = (metrics.get("fields") or {}).get(name) or {}
    unit = f" ({meta['unit']})" if meta.get("unit") else ""
    return f"{meta.get('label', name)}{unit}"


def metric_rows(entries: dict[str, Any], metrics: dict[str, Any], baseline_key: str = "baseline_metrics") -> list[list[str]]:
    """Rows [dataset, metric, candidate, baseline, worse than baseline, gate] for every dataset and declared metric."""
    rows = []
    for dataset, entry in entries.items():
        gates = entry.get("gates") or {}
        for name in metrics.get("fields") or {}:
            value = (entry.get("metrics") or {}).get(name)
            if value is None:
                continue
            rows.append([str(dataset), metric_label(metrics, name), num(value, 4), num((entry.get(baseline_key) or {}).get(name), 4),
                         signed((entry.get("worse_by") or {}).get(name), 4),
                         yes_no(gates[name]) if name in gates else ("噪声门槛内" if name == entry.get("primary") and entry.get("passed") else
                                                                      "超出噪声门槛" if name == entry.get("primary") and entry.get("passed") is False else "")])
    return rows


METRIC_HEADER = ["数据", "指标", "候选", "基线", "差于基线", "判定"]


def short(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clip(text: Any, limit: int) -> str:
    """Like short() but keeps line breaks (for multi-line passages such as an assessment plus its next step)."""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def yes_no(value: Any) -> str:
    return ("通过" if value else "未通过") if isinstance(value, bool) else "—"


def score(value: Any) -> str:
    """Judges' mean score, or the core's label for a user proposal (e.g. "用户方案")."""
    return num(value, 2) if isinstance(value, (int, float)) else (str(value) if value else "—")


def target(value: Any) -> str:
    if isinstance(value, bool):  # before numbers: True == 1 in Python
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    return num(value, 2) if isinstance(value, float) else str(value)
