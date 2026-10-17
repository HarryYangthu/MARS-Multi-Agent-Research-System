"""Self-contained HTML presentation report (汇报版).

One file, offline-openable: inline CSS, base64 charts, no external assets.
Structure follows the reporting narrative: 目的 → 关键原理（公式/框图）→
方案路径 → 实验设计 → 实验结果（图表）→ 结论 → 边界与证据。
"""
from __future__ import annotations

import base64
import html
import math
import re
from pathlib import Path
from typing import Any

from app.reporting import charts
from app.reporting.narrative import Narrative, build_narrative

_METRIC_CARDS = [
    ("improvement_db", "对消增益", "dB", "higher"),
    ("RES", "平均残余 RES", "dB", "lower"),
    ("maxRES", "最大残余 maxRES", "dB", "lower"),
    ("PER_THD", "PER_THD", "dB", "lower"),
]


def _b64(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _para(text: str) -> str:
    return "".join(f"<p>{html.escape(part)}</p>" for part in text.split("\n\n") if part.strip())


def _render_section(text: str, intro: str = "") -> str:
    """Render a narrative section: prose paragraphs + real tables, no pipe dumps."""
    from app.reporting.narrative import split_prose_tables

    paragraphs, tables = split_prose_tables(text)
    parts: list[str] = []
    if intro:
        parts.append(f'<p class="lead">{html.escape(intro)}</p>')
    for para in paragraphs:
        parts.append(f"<p>{html.escape(para)}</p>")
    for rows in tables:
        body = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>" for row in rows)
        parts.append(f'<div class="table-wrap"><table>{body}</table></div>')
    if not parts:
        return ""
    return f'<div class="card">{"".join(parts)}</div>'


_PLAIN_LABELS = {"goal": "要解决的问题", "did": "我们做了什么", "result": "结果怎么样", "conclusion": "结论"}


def _plain_card(story: Narrative) -> str:
    from app.reporting.narrative import plain_summary

    lines = plain_summary(story)
    if not lines:
        return ""
    items = "".join(
        f'<div class="plain-row"><span class="plain-tag">{_PLAIN_LABELS[key]}</span>'
        f'<div class="plain-body"><p>{html.escape(text)}</p></div></div>'
        for key, text in lines.items())
    return (f'<section id="plain"><h2>一页白话摘要</h2>'
            f'<div class="card plain-card">{items}</div>'
            f'<p class="muted" style="margin:6px 0 0">先看这一页就能明白整件事；下面各章是给需要核对细节的人准备的。</p></section>')


def _frac(numerator: str, denominator: str) -> str:
    return (f'<span class="frac"><span class="num">{numerator}</span>'
            f'<span class="den">{denominator}</span></span>')


def _formula_block() -> str:
    return f"""
    <div class="formula-card">
      <div class="formula"><span class="var">r</span>(t) = <span class="var">x</span><sub>PIM</sub>(t) + <span class="var">n</span>(t)</div>
      <div class="formula"><span class="var">ŷ</span>(t) = <span class="var">f</span><sub>θ</sub>(<span class="var">x</span>(t)) ≈ <span class="var">x</span><sub>PIM</sub>(t)<span class="op">，</span><span class="var">e</span>(t) = <span class="var">r</span>(t) − <span class="var">ŷ</span>(t)</div>
      <div class="formula">RES = 10·log<sub>10</sub> mean<sub>ch</sub> {_frac('<i>P</i><sub>err</sub>', '<i>P</i><sub>nf</sub>')}</div>
      <div class="formula">Φ<sub>k</sub>(<span class="var">x</span>) = 频移(族中心<sub>k</sub> − <span class="var">f</span><sub>rx</sub>) → LUT 样条 → 共享 L30</div>
    </div>"""


def _kpi_strip(metrics: list[dict[str, Any]], baseline_id: str | None, candidate_id: str | None,
               verdict_text: str) -> str:
    baseline = next((m for m in metrics if m.get("experiment_id") == baseline_id), None)
    candidate = next((m for m in metrics if m.get("experiment_id") == candidate_id), None)
    if not baseline or not candidate:
        return ""
    cards: list[tuple[str, str, str]] = []

    def val(row: dict[str, Any], key: str) -> float | None:
        value = row.get(key)
        return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None

    gain = val(candidate, "improvement_db")
    base_gain = val(baseline, "improvement_db")
    params = val(candidate, "parameters_real_equivalent")
    base_params = val(baseline, "parameters_real_equivalent")
    if gain is not None:
        cards.append((f"{gain:.3f} dB", "候选对消增益", "improvement_db"))
    if gain is not None and base_gain is not None:
        cards.append((f"{'+' if gain >= base_gain else ''}{gain - base_gain:.4f} dB", "相对基线净差", "delta"))
    if params is not None and base_params:
        cards.append((f"{params:.0f}（{params / base_params:.1%}）", "参数量（实数等效）", "params"))
    if params is not None and base_params:
        cards.append((f"≤ 80% 门限 {'达标' if params <= base_params * 0.8 else '未达标'}", "参数预算判定", "verdict"))
    met = bool(re.search(r"达标|成立|满足", verdict_text)) if verdict_text else None
    items = "".join(
        f'<div class="kpi"><div class="kpi-value">{html.escape(v)}</div><div class="kpi-label">{html.escape(label)}</div></div>'
        for v, label, _ in cards)
    badge = ('<span class="badge ok">任务合同口径 · 达标</span>' if met
             else '<span class="badge warn">任务合同口径 · 见结论</span>') if verdict_text else ""
    return f'<div class="kpi-strip">{items}</div>{badge}' if items else ""


def _metric_table(metrics: list[dict[str, Any]]) -> str:
    keys = ["improvement_db", "RES", "maxRES", "PER_THD", "parameters_real_equivalent",
            "active_parameters_real_equivalent", "optimizer_steps", "duration_seconds"]
    header = ["实验"] + ["对消增益(dB)", "RES(dB)", "maxRES(dB)", "PER_THD", "参数量(实数等效)",
                         "活跃参数量", "更新步数", "训练时长(s)"]
    rows = [header]
    for row in metrics:
        cells = [html.escape(str(row.get("experiment_id", "")))]
        for key in keys:
            value = row.get(key)
            if isinstance(value, float) and math.isfinite(value):
                cells.append(f"{value:.4f}" if key in ("improvement_db", "RES", "maxRES") else f"{value:.6g}")
            else:
                cells.append("—")
        rows.append(cells)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table>{body}</table></div>'


def _experiment_cards(narrative: Narrative) -> str:
    cards = []
    for card in narrative.experiments:
        note = f'<p class="muted">{html.escape(card.note)}</p>' if card.note else ""
        cards.append(
            f'<div class="exp-card"><h4>{html.escape(card.experiment_id)}</h4>'
            f'<span class="role">{html.escape(card.role)}</span>{note}</div>')
    return "".join(cards)


def write_research_html(path: Path, data_pack: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
    story = build_narrative(data_pack, blocks)
    metrics = story.metrics
    baseline_id = next((m.get("experiment_id") for m in metrics if "baseline" in str(m.get("experiment_id", "")).lower()), None)
    candidate_id = next((m.get("experiment_id") for m in metrics if "3term" in str(m.get("experiment_id", "")).lower()),
                        next((m.get("experiment_id") for m in metrics if "2term" in str(m.get("experiment_id", "")).lower()), None))

    chart_imgs: list[str] = []
    diagram = charts.model_diagram(story.family_terms)
    chart_imgs.append(_b64(diagram))
    if story.paths:
        try:
            chart_imgs.append(_b64(charts.path_timeline([{"title": p.title, "note": p.note} for p in story.paths])))
        except ValueError:
            chart_imgs.append("")
    result_charts: list[str] = []
    for key, label, unit, better in _METRIC_CARDS:
        if any(isinstance(row.get(key), (int, float)) for row in metrics):
            png = charts.metric_comparison_bars(metrics, key, title=label, unit=unit, better=better,
                                                highlight=candidate_id, baseline=baseline_id)
            result_charts.append(f'<figure><img src="{_b64(png)}" alt="{html.escape(label)}对比"/><figcaption>{html.escape(label)} · 四臂实测对比（柱上括号为相对基线差值）</figcaption></figure>')
    if any(isinstance(p.get("loss"), (int, float)) for c in story.curves for p in c.get("points", [])):
        png = charts.convergence_lines(story.curves)
        result_charts.append(f'<figure><img src="{_b64(png)}" alt="训练收敛曲线"/><figcaption>训练收敛曲线（各臂记录损失，原始线性值）</figcaption></figure>')
    if any(isinstance(row.get("parameters_real_equivalent"), (int, float)) for row in metrics):
        base_params = next((row.get("parameters_real_equivalent") for row in metrics
                            if row.get("experiment_id") == baseline_id), None)
        threshold = float(base_params) * 0.8 if isinstance(base_params, (int, float)) else None
        png = charts.metric_comparison_bars(metrics, "parameters_real_equivalent", title="参数量（实数等效）",
                                            unit="", better="lower", highlight=candidate_id,
                                            threshold=threshold, threshold_label=f"基线×80% = {threshold:.1f}" if threshold else None,
                                            baseline=baseline_id)
        result_charts.insert(0, f'<figure><img src="{_b64(png)}" alt="参数量对比"/><figcaption>参数量对比 · 虚线为达标门限（基线×80%）</figcaption></figure>')

    timeline_img = chart_imgs[1] if len(chart_imgs) > 1 else ""
    subtitle = next((line.strip() for line in story.purpose.splitlines()
                     if len(line.strip()) >= 20 and not re.match(r"^\d+[\.、\s]", line.strip())), "")[:120]
    kpi = _kpi_strip(metrics, baseline_id, candidate_id, story.verdict_text)
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(story.title)} · 研究汇报</title>
<style>
  :root {{ --ink:#18243A; --muted:#64748B; --line:#D8DEE9; --brand:#475FA8; --accent:#BE7A34; --bg:#F6F8FB; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif; color:var(--ink); background:var(--bg); line-height:1.8; font-size:15.5px; }}
  h1, h2 {{ font-family:"Noto Serif SC","Source Han Serif SC","SimSun",Georgia,serif; }}
  .page {{ max-width:1080px; margin:0 auto; padding:0 32px 64px; background:#fff; box-shadow:0 0 24px rgba(24,36,58,.06); }}
  header.hero {{ padding:56px 0 28px; border-bottom:3px solid var(--brand); }}
  .kicker {{ color:var(--brand); font-weight:700; letter-spacing:.18em; font-size:12px; }}
  h1 {{ font-size:30px; margin:10px 0 6px; line-height:1.35; }}
  .meta {{ color:#475569; font-size:13.5px; }}
  nav {{ position:sticky; top:0; background:#fff; border-bottom:1px solid var(--line); display:flex; gap:18px; padding:12px 0; font-size:14px; z-index:5; flex-wrap:wrap; }}
  nav a {{ color:var(--muted); text-decoration:none; font-weight:600; }}
  nav a:hover {{ color:var(--brand); }}
  h2 {{ font-size:22px; margin:44px 0 14px; padding-left:12px; border-left:5px solid var(--brand); }}
  h3 {{ font-size:16px; margin:24px 0 8px; color:var(--brand); }}
  p {{ margin:8px 0; }}
  .muted {{ color:#475569; font-size:13.5px; }}
  .lead {{ color:#33415C; font-weight:600; }}
  .plain-card {{ background:linear-gradient(180deg,#FDFEFF,#F4F7FC); }}
  .plain-row {{ display:flex; gap:14px; align-items:flex-start; padding:10px 0; border-bottom:1px dashed var(--line); }}
  .plain-row:last-child {{ border-bottom:none; }}
  .plain-tag {{ flex:0 0 auto; margin-top:3px; background:var(--ink); color:#fff; border-radius:8px; padding:3px 12px; font-size:13px; font-weight:700; letter-spacing:.06em; }}
  .plain-body p {{ margin:0; }}
  .card {{ background:var(--bg); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin:14px 0; }}
  .formula-card {{ background:#FBFCFE; border:1px dashed var(--brand); border-radius:12px; padding:16px 22px; margin:14px 0; }}
  .formula {{ font-family:"Cambria Math",Georgia,serif; font-size:17px; margin:12px 0; display:flex; align-items:center; gap:2px; }}
  .formula .var {{ font-style:italic; color:var(--brand); }}
  .formula .op {{ color:var(--muted); margin:0 8px; }}
  .frac {{ display:inline-flex; flex-direction:column; align-items:center; vertical-align:middle; margin:0 4px; line-height:1.25; }}
  .frac .num {{ padding:0 6px 1px; border-bottom:1.4px solid var(--ink); }}
  .frac .den {{ padding:1px 6px 0; }}
  .kpi-strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin:18px 0 6px; }}
  .kpi {{ background:linear-gradient(180deg,#FBFCFE,#F1F5FB); border:1px solid var(--line); border-radius:12px; padding:14px 16px; text-align:center; }}
  .kpi-value {{ font-size:22px; font-weight:800; color:var(--brand); letter-spacing:.01em; }}
  .kpi-label {{ font-size:12px; color:var(--muted); margin-top:4px; font-weight:600; }}
  .hero .sub {{ font-size:15px; color:#33415C; margin:2px 0 8px; max-width:64em; }}
  figure {{ margin:22px 0; text-align:center; }}
  figure img {{ max-width:100%; border:1px solid var(--line); border-radius:10px; background:#fff; }}
  figcaption {{ font-size:12.5px; color:var(--muted); margin-top:8px; }}
  .exp-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; margin:16px 0; }}
  .exp-card {{ border:1px solid var(--line); border-radius:12px; padding:14px 16px; background:#fff; }}
  .exp-card h4 {{ margin:0 0 6px; font-size:14.5px; }}
  .role {{ display:inline-block; background:#EEF2FF; color:var(--brand); border-radius:999px; padding:2px 10px; font-size:12px; font-weight:700; }}
  .table-wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:13.5px; margin:12px 0; }}
  th {{ background:var(--ink); color:#fff; padding:8px 10px; text-align:left; font-weight:600; white-space:nowrap; }}
  td {{ border-bottom:1px solid var(--line); padding:8px 10px; white-space:nowrap; }}
  tr:nth-child(odd) td {{ background:#F8FAFC; }}
  tr:first-child td {{ background:var(--ink); color:#fff; font-weight:600; }}
  .verdict {{ border-left:5px solid var(--accent); }}
  .verdict h2 {{ border-color:var(--accent); }}
  .badge {{ display:inline-block; border-radius:999px; padding:4px 14px; font-weight:700; font-size:13px; margin:6px 0 0; vertical-align:middle; }}
  .badge.ok {{ background:#ECFDF5; color:#047857; border:1px solid #A7F3D0; }}
  .badge.warn {{ background:#FFF7ED; color:#B45309; border:1px solid #FED7AA; }}
  footer {{ margin-top:48px; padding-top:16px; border-top:1px solid var(--line); color:var(--muted); font-size:12px; }}
  @media print {{ nav{{display:none}} .page{{box-shadow:none; padding:0 8mm}} h2{{page-break-after:avoid}} figure{{page-break-inside:avoid}} }}
</style>
</head>
<body><div class="page">
<header class="hero">
  <div class="kicker">MARS · 多 Agent 研究系统 · 已审核研究报告</div>
  <h1>{html.escape(story.report_title)}</h1>
  {f'<p class="sub">{html.escape(subtitle)}</p>' if subtitle else ''}
  <div class="meta">任务：{html.escape(story.run_id)} · 生成：{html.escape(story.created[:19].replace('T', ' '))} · 数据来源：真实作业回执（evaluation.json / metrics.json）</div>
</header>
<nav>
  <a href="#plain">白话摘要</a><a href="#purpose">1 研究目的</a><a href="#principle">2 关键原理</a><a href="#path">3 方案路径</a>
  <a href="#design">4 实验设计</a><a href="#results">5 实验结果</a><a href="#verdict">6 结论</a><a href="#limit">7 边界与证据</a>
</nav>
{kpi}
{_plain_card(story)}

<section id="purpose"><h2>1 研究目的</h2>
  {_render_section(story.purpose, intro="先说清楚这轮研究要回答的问题。")}
  {f'<div class="card"><h3>本轮可证伪假设</h3>{_para(story.hypothesis)}</div>' if story.hypothesis else ''}
</section>

<section id="principle"><h2>2 关键原理</h2>
  {_formula_block()}
  <figure><img src="{chart_imgs[0]}" alt="模型结构框图"/><figcaption>PP 先验对消模型结构框图 · 族基项由报告方法节提取</figcaption></figure>
  {_render_section(story.principle_text, intro="模型为什么这样设计：公式和框图说的是同一件事——发射信号按已知频移规则变换后，去预测接收端的互调干扰。")}
</section>

<section id="path"><h2>3 尝试的方案路径</h2>
  {f'<figure><img src="{timeline_img}" alt="方案路径"/></figure>' if timeline_img else ''}
  <div class="exp-grid">{_experiment_cards(story)}</div>
</section>

<section id="design"><h2>4 实验设计</h2>
  {_render_section(story.design_text, intro="怎么保证比较是公平的：所有实验只改方案本身，数据、种子、预算完全相同。")}
  {_metric_table(metrics)}
</section>

<section id="results"><h2>5 实验结果</h2>
  {''.join(result_charts)}
  {_render_section(story.results_text, intro="数字怎么读：柱状图里灰的是基线、橙色是本轮候选，柱顶括号是和基线的差值。")}
</section>

<section id="verdict" class="verdict"><h2>6 结论</h2>
  {_render_section(story.verdict_text, intro="按任务开始时定好的标准逐条核对。")}
</section>

<section id="limit"><h2>7 边界与证据</h2>
  {_render_section(story.limitations_text, intro="哪些结论能下、哪些还不能，如实列出。")}
  <div class="card"><h3>来源与完整性</h3>
    <p class="muted">已审核报告：{html.escape(str(data_pack.get('report_source', '')))}</p>
    <p class="muted">报告 SHA-256：{html.escape(str(data_pack.get('report_source_sha256', ''))[:32])}…</p>
    <p class="muted">证据文件 {len(data_pack.get('source_refs', []))} 项均已记录哈希；本 HTML 仅呈现已记录测量，不产生新的科学判定。</p>
  </div>
</section>
<footer>由 MARS 从已审核报告与真实实验回执自动生成 · 单种子描述性结果，不构成统计显著性结论</footer>
</div></body></html>"""
    path.write_text(document, encoding="utf-8", newline="\n")
