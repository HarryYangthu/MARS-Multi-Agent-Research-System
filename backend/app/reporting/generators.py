"""Pure-Python Office deliverable writers used by V2 report bundles."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


def write_results_workbook(path: Path, data_pack: dict[str, Any]) -> None:
    sheets = [
        ("Summary", _summary_rows(data_pack)),
        ("Metrics", _metrics_rows(data_pack)),
        ("Sources", [["Source"], *[[ref] for ref in _list(data_pack.get("source_refs"))]]),
        ("QA", [["Check", "Detail"], *[[reason, "degraded"] for reason in _list(data_pack.get("degraded_reasons"))]]),
    ]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _xlsx_content_types(len(sheets)))
        zf.writestr("_rels/.rels", _office_document_rels("xl/workbook.xml"))
        zf.writestr("xl/workbook.xml", _workbook_xml([name for name, _ in sheets]))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        zf.writestr("xl/styles.xml", _xlsx_styles())
        for index, (_, rows) in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))


def write_research_docx(path: Path, data_pack: dict[str, Any]) -> None:
    summary = _dict(data_pack.get("summary"))
    best = _dict(summary.get("best_experiment"))
    paragraphs = [
        f"MARS Research Report - {data_pack.get('task', '')}",
        f"Run: {data_pack.get('run_id', '')}",
        f"Project: {data_pack.get('project', '')}",
        f"Experiments: {summary.get('experiment_count', 0)}",
        f"Primary metric: {summary.get('primary_metric', 'n/a')}",
        f"Best experiment: {best.get('experiment_id', 'n/a')}",
    ]
    if data_pack.get("degraded"):
        paragraphs.append("Report generated in degraded mode: " + "; ".join(_list(data_pack.get("degraded_reasons"))))
    excerpt = str(data_pack.get("report_markdown_excerpt", "") or "")
    if excerpt:
        paragraphs.append("Writing Agent approved markdown excerpt:")
        paragraphs.append(excerpt)

    body = "".join(f"<w:p><w:r><w:t>{_xml(text)}</w:t></w:r></w:p>" for text in paragraphs)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _docx_content_types())
        zf.writestr("_rels/.rels", _office_document_rels("word/document.xml"))
        zf.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}<w:sectPr/></w:body></w:document>"
            ),
        )


def write_research_deck(path: Path, data_pack: dict[str, Any]) -> None:
    summary = _dict(data_pack.get("summary"))
    reasons = _list(data_pack.get("degraded_reasons"))
    slides = [
        (
            "MARS Research Run",
            [
                f"Task: {data_pack.get('task', '')}",
                f"Run: {data_pack.get('run_id', '')}",
                f"Project: {data_pack.get('project', '')}",
            ],
        ),
        (
            "Simulation Summary",
            [
                f"Experiments: {summary.get('experiment_count', 0)}",
                f"Primary metric: {summary.get('primary_metric', 'n/a')}",
                f"Best: {_dict(summary.get('best_experiment')).get('experiment_id', 'n/a')}",
            ],
        ),
        (
            "QA Status",
            reasons if reasons else ["All configured structural checks passed."],
        ),
    ]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _pptx_content_types(len(slides)))
        zf.writestr("_rels/.rels", _office_document_rels("ppt/presentation.xml"))
        zf.writestr("ppt/presentation.xml", _presentation_xml(len(slides)))
        zf.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels(len(slides)))
        for index, (title, bullets) in enumerate(slides, start=1):
            zf.writestr(f"ppt/slides/slide{index}.xml", _slide_xml(title, bullets))
            zf.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", _empty_rels())


def _summary_rows(data_pack: dict[str, Any]) -> list[list[Any]]:
    summary = _dict(data_pack.get("summary"))
    rows: list[list[Any]] = [
        ["Field", "Value"],
        ["run_id", data_pack.get("run_id", "")],
        ["project", data_pack.get("project", "")],
        ["task", data_pack.get("task", "")],
        ["experiment_count", summary.get("experiment_count", 0)],
        ["primary_metric", summary.get("primary_metric", "")],
        ["degraded", data_pack.get("degraded", False)],
    ]
    best = _dict(summary.get("best_experiment"))
    for key, value in best.items():
        rows.append([f"best.{key}", value])
    return rows


def _metrics_rows(data_pack: dict[str, Any]) -> list[list[Any]]:
    metrics = [row for row in _list(data_pack.get("metrics")) if isinstance(row, dict)]
    keys: list[str] = []
    for row in metrics:
        for key in row:
            if key not in keys:
                keys.append(str(key))
    if not keys:
        return [["metric", "value"], ["status", "no metrics available"]]
    return [keys, *[[row.get(key, "") for key in keys] for row in metrics]]


def _worksheet_xml(rows: list[list[Any]]) -> str:
    rendered_rows: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{_column_name(col_index)}{row_index}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{_xml(str(value))}</t></is></c>')
        rendered_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(rendered_rows)}</sheetData></worksheet>'
    )


def _xlsx_content_types(sheet_count: int) -> str:
    sheets = "".join(
        '<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.format(index=index)
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{sheets}</Types>"
    )


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{_xml(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def _workbook_rels(sheet_count: int) -> str:
    rels = "".join(
        '<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet{index}.xml"/>'.format(index=index)
        for index in range(1, sheet_count + 1)
    )
    rels += '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'


def _xlsx_styles() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellXfs>'
        '</styleSheet>'
    )


def _docx_content_types() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )


def _pptx_content_types(slide_count: int) -> str:
    slides = "".join(
        '<Override PartName="/ppt/slides/slide{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'.format(index=index)
        for index in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        f"{slides}</Types>"
    )


def _presentation_xml(slide_count: int) -> str:
    slide_ids = "".join(f'<p:sldId id="{256 + index}" r:id="rId{index}"/>' for index in range(1, slide_count + 1))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx=\"12192000\" cy=\"6858000\" type=\"screen16x9\"/></p:presentation>"
    )


def _presentation_rels(slide_count: int) -> str:
    rels = "".join(
        '<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
        'Target="slides/slide{index}.xml"/>'.format(index=index)
        for index in range(1, slide_count + 1)
    )
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'


def _slide_xml(title: str, bullets: list[Any]) -> str:
    bullet_xml = "".join(f"<a:p><a:r><a:t>{_xml(str(item))}</a:t></a:r></a:p>" for item in bullets)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
        f'{_shape_xml(2, "Title", title, 700000, 450000, 10800000, 800000)}'
        f'{_shape_xml(3, "Body", bullet_xml, 900000, 1500000, 10300000, 4300000, raw=True)}'
        '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def _shape_xml(
    shape_id: int,
    name: str,
    text: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    *,
    raw: bool = False,
) -> str:
    paragraphs = text if raw else f"<a:p><a:r><a:t>{_xml(text)}</a:t></a:r></a:p>"
    return (
        "<p:sp>"
        f'<p:nvSpPr><p:cNvPr id="{shape_id}" name="{_xml(name)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        f"<p:txBody><a:bodyPr/><a:lstStyle/>{paragraphs}</p:txBody>"
        "</p:sp>"
    )


def _office_document_rels(target: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{target}"/>'
        '</Relationships>'
    )


def _empty_rels() -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _xml(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)

