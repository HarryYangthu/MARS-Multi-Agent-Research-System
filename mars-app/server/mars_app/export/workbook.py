"""Per-iteration data as an editable Excel workbook (styling from MARS-Product reporting/generators.py)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from mars_app.export.context import overview_lines
from mars_app.export.fmt import metric_label

WIDE_COLUMNS = {"改动", "机制判断", "下一步", "消息", "预检", "标题", "值", "说明"}


def _cell(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bool):
        return "是" if value else "否"
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _sheet(book: Workbook, name: str, rows: list[list[Any]]) -> Any:
    sheet = book.create_sheet(name)
    for row in rows:
        sheet.append([_cell(value) for value in row])
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # text from the run (e.g. a model's assessment) must never become a formula
    sheet.freeze_panes = "A2"
    if sheet.max_row > 1:
        sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[1].height = 26
    for cell in sheet[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="18243A")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for column in sheet.columns:
        header = str(column[0].value or "")
        sheet.column_dimensions[get_column_letter(column[0].column)].width = 60 if header in WIDE_COLUMNS else min(28, max(10, len(header) * 2 + 2))
        for cell in column[1:]:
            cell.font = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=isinstance(cell.value, str) and header in WIDE_COLUMNS)
            if isinstance(cell.value, float):
                cell.number_format = "0.000"
    return sheet


def write(path: Path, ctx: dict[str, Any]) -> None:
    book = Workbook()
    del book["Sheet"]
    contract = ctx["contract"]
    iterations = [(route, it) for route in ctx["routes"] for it in route["iterations"]]

    _sheet(book, "概览", [["字段", "值"], ["运行", ctx["run_id"]], ["标题", ctx["title"]], ["类型", ctx["kind_label"]],
                          ["状态", ctx["state_label"]], ["内核阶段", ctx["phase"]], ["开始", ctx["started"]],
                          ["最后事件", (ctx["last_event"] or {}).get("ts")], ["导出时间", ctx["exported_at"]],
                          ["Step1 轮数", len(ctx["rounds"])], ["路线数", len(ctx["routes"])], ["迭代数", len(iterations)],
                          ["运行目录", ctx["run_dir"]],
                          *[["概要", line] for line in overview_lines(ctx)],
                          ["说明", "数字来自项目评测命令的真实执行记录；导出不调用模型、不做新的判定。"]])

    rows: list[list[Any]] = [["类别", "键", "说明", "值"]]
    rows += [["门槛", label, "", value] for label, value in ctx["targets"]]
    rows += [["预算", k, ctx["budget_labels"].get(k, ""), v] for k, v in (contract.get("budget") or {}).items()]
    rows += [["约束", f"#{i + 1}", "", c] for i, c in enumerate(contract.get("constraints") or [])]
    rows += [["目标描述", "objective", "", str(contract.get("objective") or "").strip()]]
    _sheet(book, "研究约定", rows)

    _sheet(book, "Step1排序", [["轮", "方案", "标题", "评分", "预检", "重复于", "进入Step2", "一句话"],
                               *[[r_["round"], r["id"], r["title"], r["score"], "；".join(r["precheck"]) or "通过",
                                  r["duplicate_of"], r["handed_off"], r["one_sentence"]]
                                 for r_ in ctx["rounds"] for r in r_["ranking"]]])

    ds = ctx["datasets"]  # one column per dataset the run was measured on

    def each(values: dict[str, Any]) -> list[Any]:
        return [values.get(d) for d in ds]

    metrics = ctx["metrics"]
    names = list(metrics.get("fields") or {})
    _sheet(book, "Step2逐轮", [["路线", "路线状态", "轮", "改动", "拦截于", "检查门槛", *[f"筛选主指标差于基线 {d}" for d in ds],
                                "筛选主指标差于基线均值", "筛选通过", "验收通过", "决策", "决策调用失败", "机制判断", "下一步", "分支", "提交"],
                               *[[route["id"], route["status_label"], it["iteration"], it["summary"], it["stopped_at"],
                                  (it.get("check_gates") or {}).get("all"), *each(it["screen_gap"]), it["screen_mean_gap"],
                                  it["screen_passed"], it["verify_passed"], it["verdict"], it["decision_error"],
                                  it["assessment"], it["next_change"], it["branch"], (it["commit"] or "")[:10]]
                                 for route, it in iterations]])

    def detail(stage: str) -> list[list[Any]]:
        header = ["路线", "轮", "数据", *[f"{metric_label(metrics, n)} 候选" for n in names], *[f"{metric_label(metrics, n)} 基线" for n in names],
                  *[f"{metric_label(metrics, n)} 差于基线" for n in names], "通过", "错误"]
        rows = [[route["id"], it["iteration"], d, *[(e.get("metrics") or {}).get(n) for n in names],
                 *[(e.get("baseline_metrics") or {}).get(n) for n in names], *[(e.get("worse_by") or {}).get(n) for n in names],
                 e.get("passed"), e.get("error")]
                for route, it in iterations for d, e in it[stage].items()]
        return [header, *rows]

    _sheet(book, "筛选明细", detail("screen"))
    _sheet(book, "验收", detail("verify"))

    _sheet(book, "事件", [["序号", "时间", "阶段", "消息"],
                          *[[e.get("index"), e.get("ts"), e.get("stage"), e.get("message")] for e in ctx["events"]]])

    routes = [r for r in ctx["routes"] if any(it["screen_mean_gap"] is not None for it in r["iterations"])]
    if routes:
        depth = max(int(it["iteration"] or 0) for r in routes for it in r["iterations"])
        by_iteration = {r["id"]: {it["iteration"]: it["screen_mean_gap"] for it in r["iterations"]} for r in routes}
        sheet = _sheet(book, "图表", [["轮", *[r["id"] for r in routes]],
                                      *[[k, *[by_iteration[r["id"]].get(k) for r in routes]] for k in range(1, depth + 1)]])
        chart = LineChart()
        chart.title = f"各路线筛选：{metric_label(metrics, metrics.get('primary') or '')} 差于基线（数据集平均，越低越好）"
        chart.y_axis.title, chart.x_axis.title = "差于基线", "轮"
        chart.add_data(Reference(sheet, min_col=2, max_col=len(routes) + 1, min_row=1, max_row=depth + 1), titles_from_data=True)
        chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=depth + 1))
        chart.width, chart.height = 24, 12
        sheet.add_chart(chart, f"{get_column_letter(len(routes) + 3)}2")
    book.save(path)
