"""Human-readable run report assembled only from saved artifacts (no model calls)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mars.run import Run


def _fmt(value: Any, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else ("—" if value is None else str(value))


def write_report(run: Run, routes: list[dict[str, Any]], outcome: str) -> Path:
    lines = [f"# MARS 运行报告：{run.contract['title']}", "",
             f"- 运行目录：`{run.run_dir}`", f"- 结果：**{'达到目标与约束' if outcome == 'success' else '未达到目标'}**", ""]
    rounds = sorted(p.name for p in (run.run_dir / "step1").glob("r*") if p.is_dir())
    for name in rounds:
        base = f"step1/{name}"
        lines += [f"## Step1 · {name}", ""]
        if run.has(f"{base}/analysis.json"):
            analysis = run.load(f"{base}/analysis.json")
            lines += ["### 问题分析", analysis["baseline_understanding"], "", "**历史结论（知识库引用）**"]
            lines += [f"- {c['claim']}（{', '.join(c['kb_ids'])}）" for c in analysis["history_lessons"]]
            lines += ["", "**方向**"] + [f"- {d['id']} {d['name']}：{d['hypothesis_seed']}" for d in analysis["directions"]] + [""]
        if run.has(f"{base}/cards.json"):
            cards = run.load(f"{base}/cards.json")
            sources = run.load(f"{base}/sources.json") if run.has(f"{base}/sources.json") else []
            verified = [c for c in cards if c.get("verified")]
            lines += [f"### 调研：检索 {len(sources)} 个来源，精读 {len(cards)} 个，{len(verified)} 张证据卡原文引用核验通过", ""]
            lines += [f"- {c['id']} [{'全文' if c.get('full_text') else '摘要'}] [{c.get('title', '')[:90]}]({c.get('url', '')})：{c.get('mechanism', '')[:160]}"
                      for c in verified] + [""]
        if run.has(f"{base}/debate.json"):
            debate = run.load(f"{base}/debate.json")
            lines += ["### 辩论与修订", ""]
            for item in debate:
                severities = [i["severity"] for c in item["critiques"] for i in c["issues"]]
                lines.append(f"- {item['proposal']['id']} {item['proposal']['title']}：收到 {len(item['critiques'])} 份质询"
                             f"（致命 {severities.count('fatal')}、重要 {severities.count('major')}），答辩 {len(item['rebuttals'])} 条；"
                             f"预检 {'通过' if not item['precheck'] else '；'.join(item['precheck'])}")
            lines.append("")
        if run.has(f"{base}/ranking.json"):
            lines += ["### 评审排序", "", "| 方案 | 评分 | 预检 |", "|---|---|---|"]
            lines += [f"| {r['id']} {r['title'][:40]} | {r['score']} | {'通过' if not r['precheck'] else '未通过'} |"
                      for r in run.load(f"{base}/ranking.json")] + [""]
    lines += ["## Step2 · 进化", ""]
    primary, datasets = run.kernel.metrics["primary"], list(run.kernel.cfg["datasets"])
    label = run.kernel.metrics["fields"][primary].get("label", primary)
    for route in routes:
        lines += [f"### 路线 {route['id']}：{route['proposal']['title']}（状态：{route['status']}，共 {route['iteration']} 轮）", "",
                  f"| 轮次 | 改动 | 拦截于 | 检查门槛 | 筛选 {label} 差于基线 {'/'.join(datasets)} | 验收 | 决策 |",
                  "|---|---|---|---|---|---|---|"]
        for item in route["history"]:
            e = item.get("evidence", {})
            scr = e.get("screen", {})
            gates = e.get("check_gates") or {}
            lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                item["iteration"], item["task"].get("summary", "")[:60].replace("|", "/"), e.get("stopped_at") or "—",
                {True: "通过", False: "未通过"}.get(gates.get("all"), "—"),
                "/".join(_fmt((scr.get(d, {}).get("worse_by") or {}).get(primary)) for d in datasets),
                {True: "通过", False: "未通过"}.get(e.get("verify", {}).get("passed"), "—"),
                item.get("decision", {}).get("verdict", "—")))
        lines.append("")
        if route.get("success"):
            verify = route["success"]["evidence"]["verify"]
            names = list(run.kernel.metrics["fields"])
            lines += ["**完整预算验收（与完整基线对比）**", "", "| 数据 | " + " | ".join(f"{n} 候选/基线" for n in names) + " |",
                      "|---|" + "---|" * len(names)]
            for d in datasets:
                v = verify[d]
                lines.append(f"| {d} | " + " | ".join(f"{_fmt(v['metrics'].get(n))}/{_fmt(v['baseline_metrics'].get(n))}" for n in names) + " |")
            lines += ["", f"- 候选分支：`{route['success']['branch']}`，提交 `{route['success']['commit'][:10]}`", ""]
    lines += ["## 说明", "- 所有指标来自项目评测命令的真实执行；模型只负责提出与解释，判定由程序按研究约定执行。",
              "- 筛选比较使用多种子基线的中位数与噪声门槛；验收为单种子完整预算，跨种子稳定性需另行验证。", ""]
    path = run.path("report.md")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
