"""Weekly-summary deck as a portable OOXML presentation: editable text and tables, the evaluation images.

The writer is MARS-Product's reporting/presentation.py; image sizes are read from the PNG header so no imaging
library is needed.
"""
from __future__ import annotations

import math
import struct
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from mars_app.export.context import overview_lines
from mars_app.export.fmt import METRIC_HEADER, clip, datasets_label, metric_label, metric_rows, pair, score, short, signed, target

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NAMESPACE = f'xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"'
SLIDE_W, SLIDE_H, LEFT, BODY_TOP, BODY_W = 12192000, 6858000, 700000, 1500000, 10800000
ROW_H = 520000
ROWS_PER_SLIDE = 7


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


def wrapped(text: str, width: int = 80) -> list[str]:
    """Wrap by display width (CJK counts double) so text stays inside the slide."""
    lines: list[str] = []
    for original in text.splitlines():
        current, used = "", 0
        for char in original:
            units = 2 if ord(char) > 255 else 1
            if used + units > width:
                lines.append(current)
                current, used = "", 0
            current += char
            used += units
        lines.append(current)
    return lines


def native_table(rows: list[list[str]], weights: list[int] | None = None) -> str:
    cols = max(len(row) for row in rows)
    weights = weights or [1] * cols
    widths = [BODY_W * w // sum(weights) for w in weights]
    body = ''.join(f'<a:tr h="{ROW_H}">' + ''.join(
        f'<a:tc><a:txBody><a:bodyPr wrap="square"/><a:lstStyle/>{paragraphs([value], 1200, "FFFFFF" if index == 0 else "18243A")}</a:txBody><a:tcPr marL="72000" marR="72000" marT="36000" marB="36000"><a:solidFill><a:srgbClr val="{"18243A" if index == 0 else "EEF2F6"}"/></a:solidFill></a:tcPr></a:tc>'
        for value in [*row, *[""] * (cols - len(row))]) + '</a:tr>' for index, row in enumerate(rows))
    grid = "".join(f'<a:gridCol w="{width}"/>' for width in widths)
    return f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Editable table"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="{LEFT}" y="{BODY_TOP}"/><a:ext cx="{BODY_W}" cy="{ROW_H * len(rows)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>{body}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the IHDR chunk (bytes 16–24 of every PNG)."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} 不是 PNG")
    return struct.unpack(">II", header[16:24])


class Deck:
    def __init__(self) -> None:
        self.slides: list[tuple[str, str, Path | None]] = []

    def text(self, title: str, text: str, size: int = 1800) -> None:
        """Paginate by estimated line count but keep whole paragraphs, so PowerPoint wraps them itself and words and
        numbers are never split; only a paragraph longer than a page is cut, at an estimated line boundary."""
        width = int(BODY_W / 914400 * 72 * 100 / size * 2 * 0.85)  # display units per line (CJK = 2), with margin
        line = (size / 100 * 1.2 * 1.15) * 12700  # sz is 1/100 pt; font line height ≈ 1.2 em, 115 % spacing (EMU)
        after = 5 * 12700  # 5 pt after each paragraph
        budget = 4500000  # from BODY_TOP down to just above the footer
        pages: list[list[str]] = [[]]
        used = 0.0
        for paragraph in text.splitlines() or [""]:
            pieces = wrapped(paragraph, width) or [""]
            per_page = max(1, int((budget - after) // line))
            for start in range(0, len(pieces), per_page):  # a paragraph taller than a page is split
                chunk = pieces[start:start + per_page]
                height = len(chunk) * line + after
                if pages[-1] and used + height > budget:
                    pages.append([])
                    used = 0.0
                pages[-1].append(paragraph if len(chunk) == len(pieces) else "".join(chunk))
                used += height
        for index, page in enumerate(pages, 1):
            suffix = f"（{index}）" if len(pages) > 1 else ""
            self.slides.append((title + suffix, shape(3, page, LEFT, BODY_TOP, BODY_W, budget, size), None))

    def table(self, title: str, rows: list[list[str]], weights: list[int] | None = None) -> None:
        header, body = rows[0], rows[1:]
        pages = max(1, math.ceil(len(body) / ROWS_PER_SLIDE))
        for page in range(pages):
            chunk = body[page * ROWS_PER_SLIDE:(page + 1) * ROWS_PER_SLIDE]
            suffix = f"（{page + 1}/{pages}）" if pages > 1 else ""
            self.slides.append((title + suffix, native_table([header, *chunk], weights), None))

    def image(self, title: str, path: Path | None) -> None:
        if path is not None:
            self.slides.append((title, "", path))

    def save(self, path: Path, footer_label: str) -> None:
        slides = self.slides
        content_types = [('presentation.xml', 'presentation'), ('slideMasters/slideMaster1.xml', 'slideMaster'), ('slideLayouts/slideLayout1.xml', 'slideLayout')]
        content_types += [(f'slides/slide{i}.xml', 'slide') for i in range(1, len(slides) + 1)]
        types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
        types += ''.join(f'<Override PartName="/ppt/{name}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.{kind}{".main" if kind == "presentation" else ""}+xml"/>' for name, kind in content_types)
        types += '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', types)
            archive.writestr('_rels/.rels', rels([('rId1', 'officeDocument', 'ppt/presentation.xml')]))
            ids = ''.join(f'<p:sldId id="{256 + i}" r:id="s{i}"/>' for i in range(1, len(slides) + 1))
            archive.writestr('ppt/presentation.xml', f'<p:presentation {NAMESPACE}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="master"/></p:sldMasterIdLst><p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
            archive.writestr('ppt/_rels/presentation.xml.rels', rels([('master', 'slideMaster', 'slideMasters/slideMaster1.xml'), *[(f's{i}', 'slide', f'slides/slide{i}.xml') for i in range(1, len(slides) + 1)]]))
            archive.writestr('ppt/slideMasters/slideMaster1.xml', f'<p:sldMaster {NAMESPACE}><p:cSld><p:spTree>{tree()}</p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="layout"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>')
            archive.writestr('ppt/slideMasters/_rels/slideMaster1.xml.rels', rels([('layout', 'slideLayout', '../slideLayouts/slideLayout1.xml'), ('theme', 'theme', '../theme/theme1.xml')]))
            archive.writestr('ppt/slideLayouts/slideLayout1.xml', f'<p:sldLayout {NAMESPACE} type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>{tree()}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
            archive.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels', rels([('master', 'slideMaster', '../slideMasters/slideMaster1.xml')]))
            archive.writestr('ppt/theme/theme1.xml', _theme())
            for i, (title, body, image) in enumerate(slides, 1):
                entries = [('layout', 'slideLayout', '../slideLayouts/slideLayout1.xml')]
                if image:
                    width, height = png_size(image)
                    scale = min(BODY_W / width, 4600000 / height)
                    cx, cy = round(width * scale), round(height * scale)
                    archive.write(image, f'ppt/media/image{i}.png')
                    entries.append(('image', 'image', f'../media/image{i}.png'))
                    body = f'<p:pic><p:nvPicPr><p:cNvPr id="4" name="Figure"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="image"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="{(SLIDE_W - cx) // 2}" y="{BODY_TOP}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
                header = shape(2, [title], LEFT, 400000, BODY_W, 1000000, 2600)
                footer = shape(5, [f'{footer_label}                                      {i} / {len(slides)}'], LEFT, 6370000, BODY_W, 270000, 1000, '667085')
                archive.writestr(f'ppt/slides/slide{i}.xml', f'<p:sld {NAMESPACE}><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree>{tree()}{header}{body}{footer}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
                archive.writestr(f'ppt/slides/_rels/slide{i}.xml.rels', rels(entries))


def _theme() -> str:
    colors = {'dk1': '18243A', 'lt1': 'FFFFFF', 'dk2': '334155', 'lt2': 'EEF2F6', 'accent1': '475FA8', 'accent2': '279685', 'accent3': 'BE7A34', 'accent4': '8465A6', 'accent5': '478DA6', 'accent6': 'B85B65', 'hlink': '475FA8', 'folHlink': '8465A6'}
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    return f'<a:theme xmlns:a="{A}" name="MARS Research"><a:themeElements><a:clrScheme name="Research">' + ''.join(f'<a:{name}><a:srgbClr val="{color}"/></a:{name}>' for name, color in colors.items()) + '</a:clrScheme><a:fontScheme name="Arial"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:minorFont></a:fontScheme><a:fmtScheme name="Research"><a:fillStyleLst>' + fill * 3 + '</a:fillStyleLst><a:lnStyleLst>' + ('<a:ln w="9525">' + fill + '<a:prstDash val="solid"/></a:ln>') * 3 + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3 + '</a:effectStyleLst><a:bgFillStyleLst>' + fill * 3 + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'


def write(path: Path, ctx: dict[str, Any]) -> None:
    deck = Deck()
    contract = ctx["contract"]
    deck.text(ctx["title"], f"{ctx['run_id']} · {ctx['kind_label']} · {ctx['state_label']}\n导出于 {ctx['exported_at']}\n\n"
              "数字来自项目评测命令的真实执行记录；完整内容见配套 Word / Excel。", size=2000)
    if ctx["targets"]:
        deck.table("门槛", [["门槛", "要求"], *[[label, value] for label, value in ctx["targets"]]], [3, 2])
    if contract.get("constraints"):
        deck.text("约束", "\n".join(f"· {c}" for c in contract["constraints"]))
    deck.text("概要", "\n".join(f"· {line}" for line in overview_lines(ctx)))
    for round_ in ctx["rounds"]:
        if round_["ranking"]:
            deck.table(f"Step1 第 {round_['round'][1:]} 轮 · 评审排序",
                       [["方案", "评分", "预检", "进入 Step2"],
                        *[[f"{r['id']} {short(r['title'], 34)}", score(r["score"]), short("；".join(r["precheck"]) or "通过", 26),
                           "是" if r["handed_off"] else ""] for r in round_["ranking"]]], [6, 1, 3, 1])
    if ctx["routes"]:
        primary = metric_label(ctx["metrics"], ctx["metrics"].get("primary") or "")
        deck.table("Step2 · 路线一览",
                   [["路线", "状态", "轮数", "最好一轮", f"筛选{primary}差于基线 {datasets_label(ctx['datasets'])}", "检查门槛"],
                    *[[f"{r['id']} {short(r['title'], 16)}", r["status_label"], str(len(r["iterations"])),
                       f"第 {r['best']['iteration']} 轮" if r["best"] else "—",
                       pair(r["best"]["screen_gap"], 4, sign=True) if r["best"] else "—",
                       {True: "通过", False: "未通过"}.get((r["best"].get("check_gates") or {}).get("all"), "—") if r["best"] else "—"]
                      for r in ctx["routes"]]],
                   [4, 2, 1, 2, 4, 2])
    for route in ctx["routes"]:
        best = route["best"]
        if best:
            stage = "完整验收" if best["verify"] else "筛选"
            measured = best["verify"] or best["screen"]
            deck.table(f"{route['id']} 第 {best['iteration']} 轮 · {stage}", [METRIC_HEADER, *metric_rows(measured, ctx["metrics"])], [2, 3, 2, 2, 2, 2])
            for dataset, s in measured.items():
                for plot in s["plots"]:
                    deck.image(f"{route['id']} 第 {best['iteration']} 轮 · {dataset} · {stage} · {plot.name}", plot)
        if route["iterations"]:
            last = route["iterations"][-1]
            text = f"第 {last['iteration']} 轮 · {last['verdict'] or '—'}\n{last['assessment'] or '（无判断）'}"
            if last["next_change"]:
                text += f"\n\n下一步：{last['next_change']}"
            deck.text(f"{route['id']} 最近一轮的判断", clip(text, 900), size=1600)
    deck.text("说明", "· 筛选为短预算，主指标对比多种子基线中位数与噪声门槛；验收为单种子完整预算。\n"
              "· 导出只整理运行产物，不调用模型、不做新的判定。\n"
              f"· 运行目录：{ctx['run_dir']}")
    deck.save(path, f"MARS · {ctx['run_id']}")
