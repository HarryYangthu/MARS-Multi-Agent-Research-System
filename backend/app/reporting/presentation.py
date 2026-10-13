"""Portable OOXML presentation writer: editable text/tables and real plot images."""
from __future__ import annotations

import math
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NAMESPACE = f'xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"'

def xml(text: object) -> str:
    return escape(str(text), {'"': "&quot;"})

def rels(entries: list[tuple[str, str, str]]) -> str:
    return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(
        f'<Relationship Id="{xml(identity)}" Type="{R}/{kind}" Target="{xml(target)}"/>' for identity, kind, target in entries) + '</Relationships>'

def paragraphs(lines: list[str], size: int, color: str = "18243A") -> str:
    return ''.join(f'<a:p><a:pPr><a:lnSpc><a:spcPct val="115000"/></a:lnSpc><a:spcAft><a:spcPts val="500"/></a:spcAft></a:pPr><a:r><a:rPr lang="zh-CN" sz="{size}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/></a:rPr><a:t>{xml(line)}</a:t></a:r><a:endParaRPr sz="{size}"/></a:p>' for line in lines)

def shape(identity: int, text: list[str], x: int, y: int, width: int, height: int, size: int, color: str = "18243A") -> str:
    return f'<p:sp><p:nvSpPr><p:cNvPr id="{identity}" name="Text {identity}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square" lIns="0" rIns="0" tIns="0" bIns="0"/><a:lstStyle/>{paragraphs(text, size, color)}</p:txBody></p:sp>'

def tree() -> str:
    return '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'

_TOKEN_RE = None

_NO_LINE_START = "。，、；：！？）】》」』%‰·…"

def wrapped(text: str, width: int = 80) -> list[str]:
    """Line-wrap text without breaking inside ASCII tokens, with CJK kinsoku.

    ASCII runs ([A-Za-z0-9_.:/\\()+-]) stay intact; lines never start with
    closing CJK punctuation (禁则处理).
    """
    lines: list[str] = []
    for original in text.splitlines():
        current, used = "", 0
        index = 0
        while index < len(original):
            char = original[index]
            if ord(char) > 255:
                token, units = char, 2
            else:
                start = index
                while index < len(original) and ord(original[index]) <= 255:
                    index += 1
                token, units = original[start:index], index - start
                index -= 1
            if used + units > width and current:
                lines.append(current)
                current, used = "", 0
                if token.strip() == "":
                    token = token.lstrip()
                    units = len(token)
                    if not token:
                        index += 1
                        continue
            current += token
            used += units
            index += 1
        if current:
            lines.append(current)
    merged: list[str] = []
    for line in lines:
        while line and line[0] in _NO_LINE_START and merged:
            merged[-1] += line[0]
            line = line[1:]
        merged.append(line)
    # move a trailing opening bracket to the next line (禁则: no line ends with it)
    for index in range(len(merged) - 1):
        while merged[index] and merged[index][-1] in "(（「『【":
            merged[index + 1] = merged[index][-1] + merged[index + 1]
            merged[index] = merged[index][:-1]
    return [line for line in merged if line != ""]


_BREAK_AFTER = "./_-）】》」』、，"

def cell_lines(value: str, width: int) -> list[str]:
    """Wrap a table cell at identifier-safe breakpoints (after ./_ etc.).

    Long paths and metric names stay readable instead of being cut mid-word
    by PowerPoint's character-level cell wrapping.
    """
    text = str(value)
    if not text:
        return [""]
    raw_segments: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in _BREAK_AFTER and ord(char) > 255 or char in "./_-":
            raw_segments.append(current)
            current = ""
    if current:
        raw_segments.append(current)
    # '.' only separates long path-like names: never inside numbers
    # (5427.2 / +0.02016) and never inside short names (evaluation.json).
    segments: list[str] = []
    for segment in raw_segments:
        if (segments and segments[-1].endswith(".")
                and (len(segments[-1]) >= 2 and segments[-1][-2].isdigit()
                     or segment[:1].isdigit() or len(segments[-1]) < 13)):
            segments[-1] += segment
        else:
            segments.append(segment)
    lines: list[str] = []
    line = ""
    used = 0
    for segment in segments:
        units = sum(2 if ord(c) > 255 else 1 for c in segment)
        if used + units > width and line:
            lines.append(line)
            line, used = "", 0
        line += segment
        used += units
    if line:
        lines.append(line)
    result: list[str] = []
    for text_line in lines:
        while text_line and text_line[0] in _NO_LINE_START and result:
            result[-1] += text_line[0]
            text_line = text_line[1:]
        result.append(text_line)
    return result or [""]

_TABLE_ROW_HEIGHT = 520000


def native_table(rows: list[list[str]]) -> str:
    return _native_table_frame(rows)


_TABLE_FONT_SIZE = 1200
_TABLE_HEIGHT_BUDGET = 4600000  # footer sits at y=6370000; frame starts at 1500000


def _row_height(row: list[str], cols: int, cell_units: int) -> int:
    lines = 1
    for value in row:
        lines = max(lines, len(cell_lines(str(value), cell_units)))
    return max(_TABLE_ROW_HEIGHT, lines * 260000 + 140000)


def native_table_pages(rows: list[list[str]]) -> list[str]:
    """Split long tables into slide-sized frames, repeating the header row.

    Pagination accounts for wrapped-cell row heights so frames never run
    into the slide footer.
    """
    cols = max(len(row) for row in rows)
    cell_units = _cell_units(cols)
    header = rows[:1]
    header_height = _row_height(header[0], cols, cell_units)
    pages: list[str] = []
    current: list[list[str]] = list(header)
    used = header_height
    for row in rows[1:]:
        height = _row_height(row, cols, cell_units)
        if used + height > _TABLE_HEIGHT_BUDGET and len(current) > len(header):
            pages.append(_native_table_frame(current))
            current = list(header)
            used = header_height
        current.append(row)
        used += height
    pages.append(_native_table_frame(current))
    return pages


def _cell_units(cols: int) -> int:
    # Conservative chars-per-column estimate at 12pt Arial.
    return max(10, (10800000 // max(1, cols)) // 12700 // 140)


def _native_table_frame(rows: list[list[str]]) -> str:
    cols = max(len(row) for row in rows)
    widths = [10800000 // cols] * cols
    cell_units = _cell_units(cols)
    body = ''.join('<a:tr h="760000">' + ''.join(
        f'<a:tc><a:txBody><a:bodyPr wrap="square"/><a:lstStyle/>{paragraphs(cell_lines(str(value), cell_units), _TABLE_FONT_SIZE, "FFFFFF" if index == 0 else "18243A")}</a:txBody><a:tcPr><a:solidFill><a:srgbClr val="{"18243A" if index == 0 else "EEF2F6"}"/></a:solidFill></a:tcPr></a:tc>'
        for value in [*row, *[""] * (cols - len(row))]) + '</a:tr>' for index, row in enumerate(rows))
    grid = "".join(f'<a:gridCol w="{width}"/>' for width in widths)
    return f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Editable results table"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="700000" y="1500000"/><a:ext cx="10800000" cy="{_TABLE_ROW_HEIGHT * len(rows)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>{body}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'

def write_deck(path: Path, pack: dict[str, Any], blocks: list[dict[str, Any]], plain: Callable[[dict[str, Any]], str], table_rows: Callable[[dict[str, Any]], list[list[str]]]) -> None:
    slides: list[tuple[str, str, Path | None]] = []

    def text_slides(title: str, text: str) -> None:
        if len(wrapped(title, 56)) > 2:
            text = title + '\n\n' + text
            title = "报告正文"
        lines = wrapped(text)
        pages = max(1, math.ceil(len(lines) / 10))
        per_page = max(1, math.ceil(len(lines) / pages))
        for offset in range(0, len(lines), per_page):
            slides.append((title, shape(3, lines[offset:offset + per_page], 700000, 1500000, 10800000, 4700000, 2000), None))

    text_slides("研究报告", f"{pack.get('task')}\n\n{pack.get('run_id')}\n\n已审核报告 · 原始指标与记录\n完整正文及证据路径见配套 Word / Excel。")
    metrics = pack.get("metrics", [])
    columns = [key for key in ("RES", "APE", "loss", "parameter_counts") if any(key in row for row in metrics)]
    for offset in range(0, len(metrics), 4):
        rows = [["实验", *columns]]
        for row in metrics[offset:offset + 4]:
            values = []
            for key in columns:
                value = row.get(key)
                values.append(str(int(value)) if key == "parameter_counts" and isinstance(value, (int, float)) and math.isfinite(value) and value == int(value) else f"{value:.6f}" if key == "loss" and isinstance(value, (int, float)) else f"{value:.2f}" if isinstance(value, float) and math.isfinite(value) else str(value) if value is not None else "未记录")
            rows.append([str(row.get("experiment_id", "")), *values])
        slides.append(("真实实验指标", native_table(rows), None))
    title = "报告正文"
    pending: list[str] = []
    for block in blocks:
        if block.get("type") == "heading":
            if pending:
                text_slides(title, '\n\n'.join(pending))
                pending = []
            title = plain(block)
        elif block.get("type") == "table":
            rows = table_rows(block)
            # Evidence tables often contain paths longer than a slide column.
            # Preserve their contents as paginated editable text, without clipping.
            pending.append('\n\n'.join(' | '.join(row) for row in rows))
        elif block.get("type") not in {"blank_line", "thematic_break"}:
            pending.append(plain(block))
    if pending:
        text_slides(title, '\n\n'.join(pending))
    root = Path(str(pack.get("run_root", ""))).resolve()
    for plot in pack.get("plots", []):
        plot_path = (root / str(plot["path"])).resolve()
        if plot_path.is_relative_to(root) and plot_path.suffix.lower() == ".png":
            slides.append(("已记录的训练曲线", '', plot_path))
    text_slides("来源与阅读说明", f"已审核报告：{pack.get('report_source')}\nSHA-256：{pack.get('report_source_sha256')}\n\nPPT 保留报告内容，按页面换行。图像为已保存实验产物。\n局限、假设与事实保持原报告表述，导出不进行新的科学判定。")
    save_deck(path, slides)

def narrative_text_slides(title: str, text: str) -> list[tuple[str, str, Path | None]]:
    """Paginate narrative text into (title, body, image) slide tuples.

    Pipe-delimited table rows are rendered as a native editable table slide
    instead of raw text, and continuation pages are marked.
    """
    lines = wrapped(text)
    pages = max(1, math.ceil(len(lines) / 11))
    per_page = max(1, math.ceil(len(lines) / pages))
    slides: list[tuple[str, str, Path | None]] = []
    for offset in range(0, len(lines), per_page):
        page_title = title if offset == 0 else f"{title}（续）"
        slides.append((page_title, shape(3, lines[offset:offset + per_page], 700000, 1500000, 10800000, 4700000, 2000), None))
    return slides

def table_rows_from_pipe_text(text: str) -> list[list[str]] | None:
    """Detect a consecutive pipe-table block in section text; return its rows."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if " | " in stripped:
            rows.append([cell.strip() for cell in stripped.split("|")])
        elif rows:
            break
    return rows if len(rows) >= 2 else None

def save_deck(path: Path, slides: list[tuple[str, str, Path | None]]) -> None:
    """Serialize slide tuples (title, body XML, optional image) into a pptx."""
    content_types = [('presentation.xml', 'presentation'), ('slideMasters/slideMaster1.xml', 'slideMaster'), ('slideLayouts/slideLayout1.xml', 'slideLayout')]
    content_types += [(f'slides/slide{i}.xml', 'slide') for i in range(1, len(slides) + 1)]
    types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
    types += ''.join(f'<Override PartName="/ppt/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.{kind}{".main" if kind == "presentation" else ""}+xml"/>' for name, kind in content_types)
    types += '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', types)
        archive.writestr('_rels/.rels', rels([('rId1', 'officeDocument', 'ppt/presentation.xml')]))
        ids = ''.join(f'<p:sldId id="{256 + i}" r:id="s{i}"/>' for i in range(1, len(slides) + 1))
        archive.writestr('ppt/presentation.xml', f'<p:presentation {NAMESPACE}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="master"/></p:sldMasterIdLst><p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
        archive.writestr('ppt/_rels/presentation.xml.rels', rels([('master', 'slideMaster', 'slideMasters/slideMaster1.xml'), *[(f's{i}', 'slide', f'slides/slide{i}.xml') for i in range(1, len(slides) + 1)]]))
        archive.writestr('ppt/slideMasters/slideMaster1.xml', f'<p:sldMaster {NAMESPACE}><p:cSld><p:spTree>{tree()}</p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="layout"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>')
        archive.writestr('ppt/slideMasters/_rels/slideMaster1.xml.rels', rels([('layout', 'slideLayout', '../slideLayouts/slideLayout1.xml'), ('theme', 'theme', '../theme/theme1.xml')]))
        archive.writestr('ppt/slideLayouts/slideLayout1.xml', f'<p:sldLayout {NAMESPACE} type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>{tree()}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
        archive.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels', rels([('master', 'slideMaster', '../slideMasters/slideMaster1.xml')]))
        archive.writestr('ppt/theme/theme1.xml', _theme())
        for i, (title, body, image) in enumerate(slides, 1):
            entries = [('layout', 'slideLayout', '../slideLayouts/slideLayout1.xml')]
            if image:
                from PIL import Image
                with Image.open(image) as source:
                    width, height = source.size
                scale = min(10800000 / width, 4400000 / height)
                cx, cy = round(width * scale), round(height * scale)
                archive.write(image, f'ppt/media/image{i}.png')
                entries.append(('image', 'image', f'../media/image{i}.png'))
                body = f'<p:pic><p:nvPicPr><p:cNvPr id="4" name="Recorded curve"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="image"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="{(12192000 - cx) // 2}" y="1500000"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
            header_lines = wrapped(title, 56)
            header = shape(2, header_lines, 700000, 400000, 10800000, 1000000, 2800)
            footer = shape(5, [f'MARS · 已审核研究报告                                      {i} / {len(slides)}'], 700000, 6370000, 10800000, 270000, 1000, '667085')
            archive.writestr(f'ppt/slides/slide{i}.xml', f'<p:sld {NAMESPACE}><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree>{tree()}{header}{body}{footer}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
            archive.writestr(f'ppt/slides/_rels/slide{i}.xml.rels', rels(entries))

def _temp_chart(png: bytes, name: str) -> Path:
    import tempfile

    target = Path(tempfile.gettempdir()) / f"mars-chart-{name}.png"
    target.write_bytes(png)
    return target

def _theme() -> str:
    colors = {'dk1': '18243A', 'lt1': 'FFFFFF', 'dk2': '334155', 'lt2': 'EEF2F6', 'accent1': '475FA8', 'accent2': '279685', 'accent3': 'BE7A34', 'accent4': '8465A6', 'accent5': '478DA6', 'accent6': 'B85B65', 'hlink': '475FA8', 'folHlink': '8465A6'}
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    return f'<a:theme xmlns:a="{A}" name="MARS Research"><a:themeElements><a:clrScheme name="Research">' + ''.join(f'<a:{name}><a:srgbClr val="{color}"/></a:{name}>' for name, color in colors.items()) + '</a:clrScheme><a:fontScheme name="Arial"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:minorFont></a:fontScheme><a:fmtScheme name="Research"><a:fillStyleLst>' + fill * 3 + '</a:fillStyleLst><a:lnStyleLst>' + ('<a:ln w="9525">' + fill + '<a:prstDash val="solid"/></a:ln>') * 3 + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3 + '</a:effectStyleLst><a:bgFillStyleLst>' + fill * 3 + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'
