"""Everything an export shows about one run, gathered from mars.api and the read-only run artifacts.

No model calls and no new judgements: every number is copied from the core's records, and the only derived facts
are orderings (e.g. which iteration was least worse than the baseline on the primary metric).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from mars_app import artifacts, core

BUDGET_LABELS = {"proposers": "独立提案数", "routes": "进入 Step2 的路线数", "max_iterations_per_route": "每条路线最多轮数",
                 "max_step1_rounds": "最多回到 Step1 次数", "research_sources": "精读文献上限"}
STATE_LABELS = {"running": "运行中", "finished": "已结束", "waiting_approval": "等待人工确认", "stopped": "已停止", "new": "未开始"}
ROUTE_LABELS = {"active": "进化中", "success": "达标", "refuted": "机制被否定", "stopped": "已停止",
                "budget_exhausted": "预算用尽", "interrupted": "已中断"}
KIND_LABELS = {"rsi": "自主研究", "refine": "方案完善", "implement": "仿真验证"}
PROPOSAL_FIELDS = [("one_sentence", "一句话"), ("mechanism_hypothesis", "机制假设"), ("removed_or_shrunk", "改动的基线部分"),
                   ("added_capability", "新增的能力"), ("resource_estimate", "资源估计"), ("constraints_argument", "约束论证"),
                   ("history_relation", "与历史的区别"), ("falsification", "否定条件")]


def _json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _plots(run_dir: Path, route_id: str, iteration: int | None, keys: Any, prefix: str) -> list[Path]:
    """The images the core copied into runs/ for one stage and dataset (keys like screen_<dataset>_<file>.png)."""
    found = [artifacts.plot_file(run_dir, route_id, iteration, key) for key in sorted(keys or []) if str(key).startswith(prefix)]
    return [p for p in found if p]


def _order(dataset: str) -> tuple[int, str]:
    return artifacts.dataset_order(dataset)


def describe_targets(targets: dict[str, Any], metrics: dict[str, Any]) -> list[tuple[str, str]]:
    """Contract gates as (label, value) rows."""
    fields, rows = metrics.get("fields") or {}, []
    for gate in targets.get("gates") or []:
        meta = fields.get(gate.get("metric"), {})
        limit = float(gate.get("max_worse", 0.0))
        rows.append((f"验收：{meta.get('label', gate.get('metric'))}", "不劣于基线" if limit == 0 else
                     f"至少改善 {-limit:g}{meta.get('unit', '')}" if limit < 0 else f"最多劣化 {limit:g}{meta.get('unit', '')}"))
    for gate in targets.get("checks") or []:
        bounds = "，".join(f"{sign} {gate[k]}" for k, sign in (("max", "≤"), ("min", "≥")) if k in gate)
        rows.append((f"检查：{gate.get('check')}.{gate.get('field')}", bounds + ("（不满足不训练）" if gate.get("stop") else "")))
    return rows


def _mean(values: list[Any]) -> float | None:
    numbers = [v for v in values if isinstance(v, (int, float))]
    return round(sum(numbers) / len(numbers), 3) if numbers else None


def _iteration(run_dir: Path, route_id: str, row: dict[str, Any]) -> dict[str, Any]:
    k = int(row["iteration"] or 0)
    record = artifacts.record(run_dir, route_id, k) or {}
    evidence = record.get("evidence") or {}
    decision = record.get("decision") or {}
    keys = (evidence.get("plots") or {}).keys()
    screen = {}
    for dataset, item in sorted((evidence.get("screen") or {}).items(), key=lambda kv: _order(kv[0])):
        screen[dataset] = {**{x: item.get(x) for x in ("metrics", "baseline_metrics", "worse_by", "primary", "margin", "passed", "error")},
                           "ok": item.get("ok", True), "gap": (item.get("worse_by") or {}).get(item.get("primary")),
                           "plots": _plots(run_dir, route_id, k, keys, f"screen_{dataset}_")}
    verify = {d: {**{x: v.get(x) for x in ("metrics", "baseline_metrics", "worse_by", "gates", "passed")},
                  "plots": _plots(run_dir, route_id, k, keys, f"verify_{d}_")}
              for d, v in sorted((evidence.get("verify") or {}).items(), key=lambda kv: _order(kv[0])) if isinstance(v, dict)}
    return {**row, "screen": screen, "screen_mean_gap": _mean([x["gap"] for x in screen.values()]),
            "verify": verify, "verify_passed": (evidence.get("verify") or {}).get("passed"),
            "evidence_for": decision.get("evidence_for") or [], "evidence_against": decision.get("evidence_against") or [],
            "rationale": decision.get("rationale") or ""}


def _handoff(round_dir: Path) -> list[dict[str, Any]]:
    packages = _json(round_dir / "handoff.json", []) or []
    return [{"id": p.get("id"), "title": (p.get("proposal") or {}).get("title", ""), "score": p.get("score"),
             "fields": [(label, str((p.get("proposal") or {}).get(key) or "")) for key, label in PROPOSAL_FIELDS]}
            for p in packages if isinstance(p, dict)]


def gather(run_dir: Path, api: ModuleType) -> dict[str, Any]:
    run_id = run_dir.name
    status = dict(api.status(run_id))
    events = core.events(api, run_id)
    detail = artifacts.detail(run_dir, status, events)
    metrics = artifacts.metrics_of(run_dir)
    routes = []
    for route in detail["routes"]:
        iterations = [_iteration(run_dir, route["id"], row) for row in route["iterations"]]
        measured = [it for it in iterations if it["screen"] or it["verify"]]
        best = min(measured, key=lambda it: artifacts.iteration_score(it, detail["contract"].get("targets"),
                                                                    detail["datasets"])) if measured else None
        routes.append({**route, "status_label": ROUTE_LABELS.get(route["status"], route["status"]),
                       "iterations": iterations, "best": best})
    rounds = []
    for round_ in detail["step1"]:
        rounds.append({**round_, "handoff_details": _handoff(run_dir / "step1" / round_["round"])})
    baseline_screen = _json(run_dir / "step2/baseline_screen.json", {}) or {}
    stored = [p.name for p in (run_dir / "step2/plots").glob("baseline_screen_*.png")]
    baseline_plots = {d: _plots(run_dir, "plots", None, stored, f"baseline_screen_{d}_")
                      for d, x in sorted(baseline_screen.items(), key=lambda kv: _order(kv[0])) if isinstance(x, dict)}
    report = run_dir / "report.md"
    best_overall = min((r["best"] for r in routes if r["best"]),
                       key=lambda it: artifacts.iteration_score(it, detail["contract"].get("targets"), detail["datasets"]), default=None)
    best_route = next((r for r in routes if r["best"] is best_overall), None) if best_overall else None
    return {
        "run_id": run_id, "run_dir": str(run_dir), "title": detail["title"] or run_id,
        "kind": detail.get("kind", "rsi"), "kind_label": KIND_LABELS.get(detail.get("kind", "rsi"), detail.get("kind")),
        "state": detail["state"], "state_label": STATE_LABELS.get(detail["state"], detail["state"]),
        "phase": detail["phase"], "started": detail["started"], "last_event": detail["last_event"],
        "exported_at": time.strftime("%Y-%m-%d %H:%M"), "alerts": detail.get("alerts") or [],
        "contract": detail["contract"], "metrics": metrics, "targets": describe_targets(detail["contract"].get("targets") or {}, metrics),
        "budget_labels": BUDGET_LABELS,
        "rounds": rounds, "routes": routes, "baseline_screen": baseline_screen, "baseline_plots": baseline_plots,
        "datasets": detail.get("datasets") or [],
        "best": {"route": best_route["id"], "title": best_route["title"], "iteration": best_overall} if best_route else None,
        "verified": [(r["id"], it["iteration"]) for r in routes for it in r["iterations"] if it["verify_passed"]],
        "user_proposal": detail.get("user_proposal"), "events": events,
        "report_markdown": report.read_text(encoding="utf-8", errors="replace") if report.exists() else None,
        "sources": sorted(str(p.relative_to(run_dir)) for p in run_dir.rglob("*.json")
                          if not {"codingagent", "zcode"}.intersection(p.parts) and p.is_file()) + (["report.md"] if report.exists() else []),
    }


def overview_lines(ctx: dict[str, Any]) -> list[str]:
    """Plain facts for the summary section, shared by the three formats."""
    lines = [f"运行 {ctx['run_id']}（{ctx['kind_label']}）：{ctx['state_label']}，内核阶段 {ctx['phase']}。"]
    if ctx["started"]:
        last = (ctx["last_event"] or {}).get("ts")
        lines.append(f"开始于 {ctx['started']}，最后一条事件 {last}。")
    lines.append(f"Step1 共 {len(ctx['rounds'])} 轮；Step2 共 {len(ctx['routes'])} 条路线、"
                 f"{sum(len(r['iterations']) for r in ctx['routes'])} 轮迭代。")
    for route in ctx["routes"]:
        best = route["best"]
        label = (ctx["metrics"]["fields"].get(ctx["metrics"].get("primary")) or {}).get("label", ctx["metrics"].get("primary"))
        gap = best["screen_mean_gap"] if best else None
        tail = (f"，最好一轮是第 {best['iteration']} 轮（筛选{label}平均比基线差 {gap:+.4g}，负数为更好）" if best and gap is not None
                else f"，最好一轮是第 {best['iteration']} 轮" if best else "，没有进入筛选的轮次")
        lines.append(f"路线 {route['id']}「{route['title']}」：{route['status_label']}，{len(route['iterations'])} 轮{tail}。")
    if ctx["verified"]:
        lines.append("完整预算验收通过：" + "、".join(f"{r} 第 {i} 轮" for r, i in ctx["verified"]) + "。")
    elif ctx["routes"]:
        lines.append("目前没有候选通过完整预算验收。")
    for alert in ctx["alerts"]:
        lines.append(f"提醒：{alert['title']}。")
    return lines
