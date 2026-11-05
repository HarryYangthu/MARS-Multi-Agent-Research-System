"""Full run report as an editable Word document (styles and tables from MARS-Product reporting/generators.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from mars_app.export import markdown
from mars_app.export.context import overview_lines
from mars_app.export.fmt import METRIC_HEADER, clip, datasets_label, metric_rows, num, pair, score, short, signed, target, yes_no

def iteration_header(datasets: list[str]) -> list[str]:
    ds = datasets_label(datasets)
    return ["轮", "改动", "拦截于", "检查门槛", f"筛选主指标差于基线 {ds}", "验收", "决策"]


def _setup(title: str) -> Any:
    document = Document()
    section = document.sections[0]
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Inches(.7)
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3"):
        style = document.styles[name]
        style.font.name, style.font.color.rgb = "Arial", RGBColor(0, 0, 0)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    document.styles["Normal"].font.size = Pt(10.5)
    document.styles["Normal"].paragraph_format.space_after = Pt(6)
    document.styles["Normal"].paragraph_format.line_spacing = 1.15
    document.core_properties.title, document.core_properties.author = title, "MARS"
    return document


def _table(document: Any, rows: list[list[str]], widths: list[float] | None = None) -> None:
    if not rows:
        return
    width = max(len(row) for row in rows)
    widths = widths or [6.9 / width] * width
    table = document.add_table(rows=0, cols=width)
    table.style, table.autofit = "Table Grid", False
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{side}")
        for key, value in (("val", "single"), ("sz", "4"), ("color", "D8DEE9")):
            edge.set(qn(f"w:{key}"), value)
        borders.append(edge)
    table._tbl.tblPr.append(borders)
    layout = OxmlElement("w:tblLayout")  # fixed layout: Word and WPS honour the column widths below
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)
    for index, values in enumerate(rows):
        cells = table.add_row().cells
        for column, cell in enumerate(cells):
            cell.width, cell.text = Inches(widths[column]), values[column] if column < len(values) else ""
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(2)
                for run in paragraph.runs:
                    run.font.size, run.bold = Pt(8.5), index == 0
            if index == 0:
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "E9EDF2")
                cell._tc.get_or_add_tcPr().append(shade)
        if index == 0:
            table.rows[index]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))  # repeat on each page
    for column, inches in zip(table.columns, widths):
        column.width = Inches(inches)
    document.add_paragraph()


def _markdown(document: Any, text: str) -> None:
    def add(tokens: list[dict[str, Any]], list_depth: int = 0) -> None:
        for token in tokens:
            kind = token.get("type")
            if kind == "heading":
                level = min(3, int(token.get("attrs", {}).get("level", 1)) + 1)  # nested under this export's sections
                document.add_heading(markdown.plain(token), level=level)
            elif kind in {"paragraph", "block_text"}:
                document.add_paragraph(markdown.plain(token), style="List Bullet" if list_depth else None)
            elif kind == "table":
                _table(document, markdown.table_rows(token))
            elif kind == "block_code":
                for run in document.add_paragraph(str(token.get("raw", ""))).runs:
                    run.font.name, run.font.size = "Courier New", Pt(8)
            elif kind == "list":
                add(token.get("children", []), list_depth + 1)
            elif token.get("children"):
                add(token["children"], list_depth)
    add(markdown.blocks(text))


def _picture(document: Any, path: Path | None, caption: str) -> None:
    if path is None:
        return
    document.add_picture(str(path), width=Inches(6.3))
    document.add_paragraph(caption).runs[0].font.size = Pt(9)


def iteration_row(it: dict[str, Any]) -> list[str]:
    return [str(it["iteration"]), short(it["summary"], 70), it["stopped_at"] or "—", yes_no((it.get("check_gates") or {}).get("all")),
            pair(it["screen_gap"], 4, sign=True), yes_no(it["verify_passed"]),
            "决策调用失败" if it["decision_error"] else (it["verdict"] or "—")]


def write(path: Path, ctx: dict[str, Any]) -> None:
    contract = ctx["contract"]
    document = _setup(ctx["title"])
    document.add_heading(ctx["title"], level=0)
    document.add_paragraph(f"运行 {ctx['run_id']} · {ctx['kind_label']} · {ctx['state_label']} · 导出于 {ctx['exported_at']}\n"
                           "本文件由 mars-app 从运行产物整理生成：所有数字来自项目评测命令的真实执行记录，导出过程不调用模型、不做新的判定。")

    document.add_heading("1 概要", level=1)
    for line in overview_lines(ctx):
        document.add_paragraph(line, style="List Bullet")

    document.add_heading("2 研究约定", level=1)
    document.add_paragraph(str(contract.get("objective") or "").strip())
    if ctx["targets"]:
        _table(document, [["门槛", "要求"], *[[label, value] for label, value in ctx["targets"]]], [4.4, 2.5])
    for item in contract.get("constraints") or []:
        document.add_paragraph(str(item), style="List Bullet")
    budget = contract.get("budget") or {}
    _table(document, [["预算", "值"], *[[label, target(budget[k])] for k, label in ctx["budget_labels"].items() if k in budget]],
           [4.4, 2.5])

    if ctx["user_proposal"]:
        document.add_heading("用户方案原文", level=2)
        document.add_paragraph(ctx["user_proposal"].strip())

    document.add_heading("3 Step1 · 提方案", level=1)
    if not ctx["rounds"]:
        document.add_paragraph("Step1 尚未产出。")
    for round_ in ctx["rounds"]:
        document.add_heading(f"第 {round_['round'][1:]} 轮", level=2)
        done = "、".join(s["label"] for s in round_["stages"] if s["done"]) or "尚无"
        document.add_paragraph(f"已完成阶段：{done}。证据卡 {round_['cards']['verified']}/{round_['cards']['total']} 张原文引用核验通过。")
        for direction in round_["directions"]:
            document.add_paragraph(f"{direction['id']} {direction['name']}：{short(direction['hypothesis'], 200)}", style="List Bullet")
        if round_["ranking"]:
            _table(document, [["方案", "评分", "预检", "进入 Step2"],
                              *[[f"{r['id']} {short(r['title'], 60)}", score(r["score"]), "；".join(r["precheck"]) or "通过",
                                 "是" if r["handed_off"] else ""] for r in round_["ranking"]]], [3.4, 0.8, 2.0, 0.7])
        for package in round_["handoff_details"]:
            document.add_heading(f"{package['id']} {package['title']}", level=3)
            for line in [f"{label}：{value}" for label, value in package["fields"] if value]:
                document.add_paragraph(line, style="List Bullet")

    document.add_heading("4 Step2 · 进化", level=1)
    if not ctx["routes"]:
        document.add_paragraph("尚未进入 Step2。")
    elif any(ctx["baseline_plots"].values()):
        document.add_paragraph("下面为筛选预算的基线评测图；各轮候选图按实际评测阶段标注。")
        for dataset, plots in ctx["baseline_plots"].items():
            for plot in plots:
                _picture(document, plot, f"{dataset} 基线（筛选预算）· {plot.name}")
    for route in ctx["routes"]:
        document.add_heading(f"路线 {route['id']}：{route['title']}（{route['status_label']}，{len(route['iterations'])} 轮）", level=2)
        if route["iterations"]:
            _table(document, [iteration_header(ctx["datasets"]), *[iteration_row(it) for it in route["iterations"]]],
                   [0.3, 2.4, 0.7, 0.7, 1.5, 0.5, 0.7])
        for it in route["iterations"]:
            text = f"第 {it['iteration']} 轮：{it['assessment'] or '（无判断）'}"
            if it["next_change"]:
                text += f"\n下一步：{it['next_change']}"
            document.add_paragraph(clip(text, 900))
        best = route["best"]
        if best:
            stage = "完整验收" if best["verify"] else "筛选"
            document.add_heading(f"重点查看：第 {best['iteration']} 轮（按{stage}结果）", level=3)
            _table(document, [METRIC_HEADER, *metric_rows(best["verify"] or best["screen"], ctx["metrics"])])
            for dataset, s in (best["verify"] or best["screen"]).items():
                for plot in s["plots"]:
                    _picture(document, plot, f"{route['id']} 第 {best['iteration']} 轮 · {dataset} · {stage} · {plot.name}")
        for it in route["iterations"]:
            if it["verify"]:
                document.add_heading(f"第 {it['iteration']} 轮完整预算验收：{yes_no(it['verify_passed'])}", level=3)
                _table(document, [METRIC_HEADER, *metric_rows(it["verify"], ctx["metrics"])])
                for dataset, v in it["verify"].items():
                    for plot in v["plots"]:
                        _picture(document, plot, f"{route['id']} 第 {it['iteration']} 轮 · {dataset} · 完整预算 · {plot.name}")

    if ctx["report_markdown"]:
        document.add_heading("5 内核报告原文", level=1)
        document.add_paragraph("以下为内核在运行结束时写出的 report.md，未经改动。")
        _markdown(document, ctx["report_markdown"])

    document.add_heading("来源", level=1)
    document.add_paragraph(f"运行目录：{ctx['run_dir']}")
    document.add_paragraph("使用的产物：" + "、".join(ctx["sources"][:60]) + ("……" if len(ctx["sources"]) > 60 else ""))
    document.add_paragraph("筛选为短预算（研究约定 budget.screen），主指标对比多种子基线中位数与噪声门槛；验收为单种子完整预算，跨种子稳定性需另行验证。")
    document.save(str(path))
