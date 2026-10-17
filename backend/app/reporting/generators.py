"""Editable Office exports of saved reports and observed experiment data."""
from __future__ import annotations

import json
import re
import math
from pathlib import Path
from typing import Any

import mistune
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from app.reporting.html_report import write_research_html as _write_research_html
from app.reporting.narrative import build_narrative
from app.reporting.presentation import write_deck


def report_blocks(pack: dict[str, Any]) -> list[dict[str, Any]]:
    result = mistune.create_markdown(renderer="ast", plugins=["table"])(str(pack.get("report_markdown") or pack.get("report_markdown_excerpt") or ""))
    return result if isinstance(result, list) else []


def plain(token: dict[str, Any]) -> str:
    if token.get("type") in {"softbreak", "linebreak"}:
        return "\n"
    value = str(token.get("raw", "")) + "".join(plain(child) for child in token.get("children", []))
    if token.get("type") in {"link", "image"}:
        return value + f" ({token.get('attrs', {}).get('url', '')})"
    return value


def table_rows(token: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for section in token.get("children", []):
        if section.get("type") == "table_head":
            rows.append([plain(cell) for cell in section.get("children", [])])
        else:
            rows.extend([[plain(cell) for cell in row.get("children", [])] for row in section.get("children", [])])
    return rows


def _cell(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return json.dumps(value, ensure_ascii=False, allow_nan=False) if isinstance(value, (dict, list)) else value


def write_results_workbook(path: Path, data_pack: dict[str, Any]) -> None:
    book = Workbook()
    del book["Sheet"]
    metrics = data_pack.get("metrics", [])
    keys = list(dict.fromkeys(str(key) for row in metrics for key in row))
    sheets: list[tuple[str, list[list[Any]]]] = [
        ("Overview", [["Field", "Value"], ["Task", data_pack.get("task")], ["Run", data_pack.get("run_id")],
            ["Project", data_pack.get("project")], ["Approved report", data_pack.get("report_approved")],
            ["Report SHA-256", data_pack.get("report_source_sha256")], ["Experiments", len(metrics)]]),
        ("Metrics", [keys or ["No metrics available"], *[[row.get(key) for key in keys] for row in metrics]]),
        ("Sources", [["Source", "SHA-256"], *[[ref, data_pack.get("source_hashes", {}).get(ref)] for ref in data_pack.get("source_refs", [])]]),
        ("Limitations", [["Input limitations"], *[[reason] for reason in data_pack.get("degraded_reasons", [])],
            ["Only recorded observations are exported. Scientific conclusions and limitations are in the approved report."]]),
        ("Report", [["Full approved report"], *[[line] for line in str(data_pack.get("report_markdown", "")).splitlines()]]),
    ]
    for index, curve in enumerate(data_pack.get("curves", []), 1):
        points = [point for point in curve.get("points", []) if isinstance(point, dict)]
        headers = list(dict.fromkeys(str(key) for point in points for key in point))
        sheets.append((f"Steps_{index}", [headers or ["No step records"], *[[point.get(key) for key in headers] for point in points]]))
    for name, rows in sheets:
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append([_cell(value) for value in row])
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"  # Imported report text must never become a formula.
        sheet.freeze_panes, sheet.auto_filter.ref = "A2", sheet.dimensions
        sheet.sheet_view.showGridLines = False
        sheet.row_dimensions[1].height = 28
        for cell in sheet[1]:
            cell.font = Font(name="Arial", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="18243A")
        for column in sheet.columns:
            header = str(column[0].value or "")
            sheet.column_dimensions[get_column_letter(column[0].column)].width = 95 if name == "Report" else min(50, max(18, len(header) + 3))
            for cell in column[1:]:
                cell.font = Font(name="Arial", size=11)
                cell.alignment = Alignment(vertical="top", wrap_text=isinstance(cell.value, str))
                if isinstance(cell.value, float):
                    cell.number_format = "0.000000" if "loss" in header or header == "lr" else "0.00"
        curve_key = next((key for key in ("training_loss", "loss", "value") if key in rows[0]), None)
        if name.startswith("Steps_") and curve_key and sheet.max_row > 1:
            chart = LineChart()
            chart.title = f"{data_pack['curves'][int(name.split('_')[1]) - 1].get('experiment_id', name)} · training loss"
            step_key = "optimizer_step" if "optimizer_step" in rows[0] else "sample_index"
            chart.y_axis.title, chart.x_axis.title = f"Recorded {curve_key}", "Optimizer step" if step_key == "optimizer_step" else "Sample index"
            chart.add_data(Reference(sheet, min_col=rows[0].index(curve_key) + 1, min_row=1, max_row=sheet.max_row), titles_from_data=True)
            if step_key in rows[0]:
                chart.set_categories(Reference(sheet, min_col=rows[0].index(step_key) + 1, min_row=2, max_row=sheet.max_row))
            chart.width, chart.height = 24, 12
            sheet.add_chart(chart, f"{get_column_letter(sheet.max_column + 2)}2")
    book.save(path)


def _native_table(document: Any, rows: list[list[str]]) -> None:
    if not rows:
        return
    width = max(len(row) for row in rows)
    table = document.add_table(rows=0, cols=width)
    table.style, table.autofit = "Table Grid", False
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{side}")
        for key, value in (("val", "single"), ("sz", "4"), ("color", "D8DEE9")):
            edge.set(qn(f"w:{key}"), value)
        borders.append(edge)
    table._tbl.tblPr.append(borders)
    for index, values in enumerate(rows):
        cells = table.add_row().cells
        for column, cell in enumerate(cells):
            cell.width, cell.text = Inches(6.5 / width), values[column] if column < len(values) else ""
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(4)
                for run in paragraph.runs:
                    run.font.size, run.bold = Pt(9), index == 0
            if index == 0:
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "E9EDF2")
                cell._tc.get_or_add_tcPr().append(shade)
        if index == 0:
            table.rows[index]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    document.add_paragraph()


def write_research_docx(path: Path, data_pack: dict[str, Any]) -> None:
    from app.reporting import charts as chart_factory

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
    document.core_properties.title, document.core_properties.author = str(data_pack.get("task", "Research report")), "MARS"
    story = build_narrative(data_pack, report_blocks(data_pack))

    # Presentation cover: what/when/verdict before any detail.
    document.add_heading("研究汇报（已审核）", level=0)
    document.add_paragraph(f"任务：{story.run_id}\n生成：{story.created[:19].replace('T', ' ')}\n数据来源：真实作业回执（evaluation.json / metrics.json）")
    if story.verdict_text:
        verdict_line = next((line for line in story.verdict_text.splitlines() if line.strip()), "")
        document.add_paragraph(f"结论概要：{verdict_line[:160]}")

    def section_paragraphs(text: str, heading: str) -> None:
        document.add_heading(heading, level=1)
        for part in [p for p in text.split("\n\n") if p.strip()]:
            document.add_paragraph(part.strip())
        if not text.strip():
            document.add_paragraph("（报告未包含该章节内容）")

    section_paragraphs(story.purpose, "1 研究目的")
    document.add_heading("2 关键原理", level=1)
    for formula in ("r(t) = x_PIM(t) + n(t)；ŷ(t) = f_θ(x(t)) ≈ x_PIM(t)；e(t) = r(t) − ŷ(t)",
                    "RES = 10·log10 mean_ch(P_err / P_nf)",
                    "Φ_k(x) = 频移(族中心_k − f_rx) → LUT 样条 → 共享 L30"):
        for run in document.add_paragraph(formula).runs:
            run.font.name, run.font.size, run.font.bold = "Cambria Math", Pt(11.5), False
    _embed(document, chart_factory.model_diagram(story.family_terms), "模型结构框图（按报告方法节绘制）")
    for part in [p for p in story.principle_text.split("\n\n") if p.strip()]:
        document.add_paragraph(part.strip())

    document.add_heading("3 尝试的方案路径", level=1)
    if story.paths:
        _embed(document, chart_factory.path_timeline([{"title": p.title, "note": p.note} for p in story.paths]), "方案路径时间线")
    for card in story.experiments:
        note = f"：{card.note}" if card.note else ""
        document.add_paragraph(f"{card.experiment_id}（{card.role}）{note}", style="List Bullet")

    section_paragraphs(story.design_text, "4 实验设计")
    # The full metrics table stays out of the docx narrative: the appendix
    # preserves the report's own tables verbatim and charts carry the numbers.

    document.add_heading("5 实验结果", level=1)
    baseline_id = next((m.get("experiment_id") for m in story.metrics if "baseline" in str(m.get("experiment_id", "")).lower()), None)
    candidate_id = next((m.get("experiment_id") for m in story.metrics if "3term" in str(m.get("experiment_id", "")).lower()),
                        next((m.get("experiment_id") for m in story.metrics if "2term" in str(m.get("experiment_id", "")).lower()), None))
    for key, label, unit, better in (("improvement_db", "对消增益", "dB", "higher"),
                                     ("RES", "平均残余 RES", "dB", "lower"),
                                     ("maxRES", "最大残余 maxRES", "dB", "lower")):
        if not any(isinstance(row.get(key), (int, float)) for row in story.metrics):
            continue
        try:
            _embed(document, chart_factory.metric_comparison_bars(
                story.metrics, key, title=label, unit=unit, better=better,
                highlight=candidate_id, baseline=baseline_id), f"{label} · 实测对比")
        except ValueError:
            continue
    try:
        _embed(document, chart_factory.convergence_lines(story.curves), "训练收敛曲线")
    except ValueError:
        pass
    for part in [p for p in story.results_text.split("\n\n") if p.strip()]:
        document.add_paragraph(part.strip())

    section_paragraphs(story.verdict_text, "6 结论（达标判定）")
    section_paragraphs(story.limitations_text, "7 边界与局限")

    document.add_heading("附录 · 已审核报告原文（完整保留）", level=1)

    def add(tokens: list[dict[str, Any]], *, list_depth: int = 0) -> None:
        for token in tokens:
            kind = token.get("type")
            if kind == "heading":
                level = min(3, int(token.get("attrs", {}).get("level", 1)))
                document.add_heading(plain(token), level=level)
            elif kind in {"paragraph", "block_text"}:
                document.add_paragraph(plain(token), style="List Bullet" if list_depth else None).paragraph_format.widow_control = True
            elif kind == "table":
                _native_table(document, table_rows(token))
            elif kind == "block_code":
                for run in document.add_paragraph(str(token.get("raw", ""))).runs:
                    run.font.name, run.font.size = "Courier New", Pt(8)
            elif kind == "list":
                add(token.get("children", []), list_depth=list_depth + 1)
            elif token.get("children"):
                add(token["children"], list_depth=list_depth)
    add(report_blocks(data_pack))

    document.add_heading("导出来源", level=1)
    document.add_paragraph(f"任务：{data_pack.get('run_id')}\n报告：{data_pack.get('report_source')}\nSHA-256：{data_pack.get('report_source_sha256')}")
    for plot in data_pack.get("plots", []):
        root = Path(str(data_pack.get("run_root", ""))).resolve()
        image = (root / str(plot["path"])).resolve()
        if image.is_relative_to(root) and image.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            document.add_heading("已记录的训练曲线", level=2)
            document.add_picture(str(image), width=Inches(6.3))
            document.add_paragraph(str(plot["path"]))
    document.save(str(path))


def _metric_table_rows(metrics: list[dict[str, Any]]) -> list[list[str]]:
    keys = ["improvement_db", "RES", "maxRES", "PER_THD", "parameters_real_equivalent", "optimizer_steps"]
    header = ["实验", "对消增益(dB)", "RES(dB)", "maxRES(dB)", "PER_THD", "参数量", "更新步数"]
    rows = [header]
    for row in metrics:
        cells = [str(row.get("experiment_id", ""))]
        for key in keys:
            value = row.get(key)
            cells.append(f"{value:.4f}" if isinstance(value, float) and key in ("improvement_db", "RES", "maxRES")
                         else f"{value:.6g}" if isinstance(value, (int, float)) else "—")
        rows.append(cells)
    return rows


def _embed(document: Any, png: bytes, caption: str) -> None:
    import io

    from docx.shared import Inches as _Inches

    document.add_picture(io.BytesIO(png), width=_Inches(6.3))
    if document.paragraphs:
        document.paragraphs[-1].alignment = 1  # center the chart
    caption_paragraph = document.add_paragraph(caption)
    for run in caption_paragraph.runs:
        run.font.size, run.font.italic, run.font.color.rgb = Pt(9), True, RGBColor(0x64, 0x74, 0x8B)


def write_research_html(path: Path, data_pack: dict[str, Any]) -> None:
    """Bundle-facing wrapper: build markdown AST and render the HTML presentation report."""
    _write_research_html(path, data_pack, report_blocks(data_pack))


def write_research_deck(path: Path, data_pack: dict[str, Any]) -> None:
    """Presentation deck in reporting order: cover/agenda/principle/path/design/results/verdict."""
    from app.reporting import charts as chart_factory
    from app.reporting.presentation import (
        _temp_chart, native_table, native_table_pages, narrative_text_slides, save_deck, table_rows_from_pipe_text,
    )

    def section_slides(title: str, text: str, slides: list[tuple[str, str, Path | None]]) -> None:
        """Render a narrative section; pipe tables become native table slides."""
        rows = table_rows_from_pipe_text(text)
        if rows:
            remainder = "\n".join(line for line in text.splitlines()
                                  if line.strip() not in {" | ".join(r) for r in rows})
            slides.extend(narrative_text_slides(title, remainder.strip()))
            for page_index, frame in enumerate(native_table_pages(rows), 1):
                suffix = "" if len(native_table_pages(rows)) == 1 else f"（{page_index}）"
                slides.append((f"{title} · 对照表{suffix}", frame, None))
        else:
            slides.extend(narrative_text_slides(title, text))

    story = build_narrative(data_pack, report_blocks(data_pack))
    slides: list[tuple[str, str, Path | None]] = []
    baseline_row = next((m for m in story.metrics if "baseline" in str(m.get("experiment_id", "")).lower()), None)
    candidate_row = next((m for m in story.metrics if "3term" in str(m.get("experiment_id", "")).lower()),
                          next((m for m in story.metrics if "2term" in str(m.get("experiment_id", "")).lower()), None))
    verdict_line = ""
    if baseline_row and candidate_row:
        try:
            gain = float(candidate_row["improvement_db"])
            base_gain = float(baseline_row["improvement_db"])
            params = float(candidate_row["parameters_real_equivalent"])
            cover_base_params = float(baseline_row["parameters_real_equivalent"])
            verdict_line = (f"参数量 {params:.0f} ≤ 基线×80%（{cover_base_params * 0.8:.1f}）"
                            f"· 对消增益 {gain:.3f} dB ≥ 基线 {base_gain:.3f} dB")
        except (KeyError, TypeError, ValueError):
            verdict_line = ""
    slides.append((story.report_title,
                   _deck_body(f"MARS · 已审核研究报告\n\n{story.task or ''}\n\n任务：{story.run_id}\n生成：{story.created[:19].replace('T', ' ')}\n数据来源：真实作业回执"
                              + (f"\n\n结论概要：{verdict_line}" if verdict_line else "")), None))
    slides.append(("目录", _deck_body("1  研究目的\n\n2  关键原理（模型框图 · 公式）\n\n3  尝试的方案路径\n\n4  实验设计\n\n5  实验结果（图表 · 判读）\n\n6  结论（达标判定）\n\n7  边界与证据\n\n附录 · 已审核报告原文"), None))
    section_slides("1 研究目的", story.purpose or "（报告未包含摘要）", slides)
    if story.hypothesis:
        slides.extend(narrative_text_slides("可证伪假设", story.hypothesis))
    slides.append(("2 关键原理 · 模型结构", "", _temp_chart(chart_factory.model_diagram(story.family_terms), "diagram")))
    section_slides("2 关键原理 · 方法说明", story.principle_text, slides)
    if story.paths:
        try:
            slides.append(("3 尝试的方案路径", "", _temp_chart(
                chart_factory.path_timeline([{"title": p.title, "note": p.note} for p in story.paths]), "timeline")))
        except ValueError:
            pass
    roles = "\n\n".join(f"{c.experiment_id}（{c.role}）{('：' + c.note) if c.note else ''}" for c in story.experiments)
    slides.extend(narrative_text_slides("3 方案路径 · 实验角色", roles))
    if story.design_text:
        section_slides("4 实验设计", story.design_text, slides)
    rows = _metric_table_rows(story.metrics)
    slides.append(("4 实验设计 · 四臂执行矩阵", native_table(rows), None))
    baseline_id = next((m.get("experiment_id") for m in story.metrics if "baseline" in str(m.get("experiment_id", "")).lower()), None)
    candidate_id = next((m.get("experiment_id") for m in story.metrics if "3term" in str(m.get("experiment_id", "")).lower()),
                        next((m.get("experiment_id") for m in story.metrics if "2term" in str(m.get("experiment_id", "")).lower()), None))
    base_params = next((m.get("parameters_real_equivalent") for m in story.metrics if m.get("experiment_id") == baseline_id), None)
    if isinstance(base_params, (int, float)):
        try:
            slides.append(("5 实验结果 · 参数量", "", _temp_chart(chart_factory.metric_comparison_bars(
                story.metrics, "parameters_real_equivalent", title="参数量（实数等效）", unit="", better="lower",
                highlight=candidate_id, threshold=float(base_params) * 0.8,
                threshold_label=f"基线×80% = {float(base_params) * 0.8:.1f}", baseline=baseline_id), "params")))
        except ValueError:
            pass
    for key, label in (("improvement_db", "对消增益"), ("RES", "平均残余 RES"), ("maxRES", "最大残余 maxRES")):
        if not any(isinstance(row.get(key), (int, float)) for row in story.metrics):
            continue
        try:
            slides.append((f"5 实验结果 · {label}", "", _temp_chart(chart_factory.metric_comparison_bars(
                story.metrics, key, title=label, unit="dB", better="lower" if key != "improvement_db" else "higher",
                highlight=candidate_id, baseline=baseline_id), f"metric-{key}")))
        except ValueError:
            continue
    try:
        slides.append(("5 实验结果 · 训练收敛", "", _temp_chart(chart_factory.convergence_lines(story.curves), "convergence")))
    except ValueError:
        pass
    if story.results_text:
        section_slides("5 实验结果 · 判读", story.results_text, slides)
    if story.verdict_text:
        section_slides("6 结论（达标判定）", story.verdict_text, slides)
    if story.limitations_text:
        section_slides("7 边界与局限", story.limitations_text, slides)
    # Appendix: the full approved text, paginated, so nothing is dropped by
    # the narrative restructuring above. Tables stay native and editable.
    appendix_title = "附录 · 报告原文"
    pending: list[str] = []
    for block in report_blocks(data_pack):
        if block.get("type") == "heading":
            if pending:
                slides.extend(narrative_text_slides(appendix_title or "附录 · 报告原文", '\n\n'.join(pending)))
                pending = []
            appendix_title = plain(block) or appendix_title
        elif block.get("type") == "table":
            rows = table_rows(block)
            pages = native_table_pages(rows)
            for page_index, frame in enumerate(pages, 1):
                suffix = "" if len(pages) == 1 else f"（{page_index}）"
                slides.append(((appendix_title or "附录") + " · 表格" + suffix, frame, None))
        elif block.get("type") not in {"blank_line", "thematic_break"}:
            pending.append(plain(block))
    if pending:
        slides.extend(narrative_text_slides(appendix_title or "附录 · 报告原文", '\n\n'.join(pending)))
    slides.append(("来源与阅读说明", _deck_body(
        f"已审核报告：{data_pack.get('report_source')}\nSHA-256：{str(data_pack.get('report_source_sha256', ''))[:32]}…\n\n"
        "图表由真实作业回执绘制；局限、假设与事实保持原报告表述，导出不进行新的科学判定。\n完整正文见配套 Word / HTML。"), None))
    save_deck(path, slides)


def _deck_body(text: str) -> str:
    from app.reporting.presentation import shape

    lines: list[str] = []
    for paragraph in text.split("\n\n"):
        lines.extend(paragraph.splitlines() or [""])
    return shape(3, [line for line in lines if line != ""] or [" "], 700000, 1500000, 10800000, 4700000, 2000)


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str)
