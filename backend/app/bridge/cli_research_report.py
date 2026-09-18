"""Host-generated evidence reports; measurements remain distinct from agent prose.

Only persisted receipts are summarized. Unknown usage is not zero, budget
reservations are not measured tokens, and neither is a provider invoice.
"""
from __future__ import annotations

import csv
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.harness.agent_loop.trace import atomic_json
from app.harness.schema.frontmatter_parser import dumps
from app.harness.schema.validator import validate_document


TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
COUNT_EVENTS = {"model_requests": "model_request", "model_responses": "model_response",
                "tool_dispatches": "tool_dispatch", "sdk_attempts": "sdk_attempt_started"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _record(path: Path, warnings: list[str], *, optional: bool = True) -> dict[str, Any]:
    if optional and not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value
    except (OSError, ValueError) as exc:
        warnings.append(f"Unreadable evidence {path}: {exc}")
        return {}


def _rows(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        warnings.append(f"Unreadable evidence {path}: {exc}")
        return []
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("expected JSON object")
            result.append(row)
        except ValueError as exc:
            warnings.append(f"Incomplete/corrupt evidence {path}:{index}: {exc}")
    return result


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _timestamp(value: Any) -> float | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return _number(value)


def _duration(start: Any, end: Any) -> float | None:
    a, b = _timestamp(start), _timestamp(end)
    return b-a if a is not None and b is not None and b >= a else None


def _usage(value: Any) -> dict[str, int | None]:
    data = value if isinstance(value, dict) else {}
    return {key: data[key] if type(data.get(key)) is int and data[key] >= 0 else None for key in TOKEN_KEYS}


def _collect_traces(root: Path, warnings: list[str], *, scan_root: Path | None = None,
                    origin: str = "current_run", source_run_root: str | None = None
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    stages: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    directories = {p.parent for name in ("facts.json", "events.jsonl") for p in (scan_root or root / "stages").rglob(name)
                   if "agent_traces" in p.parts}
    for directory in sorted(directories):
        facts = _record(directory / "facts.json", warnings)
        events = _rows(directory / "events.jsonl", warnings)
        ref = _relative(root, directory)
        provenance = {"trace": ref, "origin": origin, "source_run_root": source_run_root or str(root.resolve())}
        counts = {key: sum(row.get("kind") == event for row in events) if events else facts.get("counts", {}).get(key)
                  for key, event in COUNT_EVENTS.items()}
        if events and any(facts.get("counts", {}).get(key, counts[key]) != counts[key] for key in COUNT_EVENTS):
            warnings.append(f"Trace/facts counters differ: {ref}; report uses recorded events")
        requests: dict[Any, dict[str, Any]] = {}
        dispatches: dict[Any, dict[str, Any]] = {}
        for event in events:
            kind = event.get("kind")
            if kind == "model_request":
                call = {**provenance, "request": event.get("request"), "provider": event.get("provider"),
                        "model": event.get("model"), "phase": event.get("phase"), "started_at": event.get("time"),
                        "status": "unresolved", "sdk_failures": 0, **_usage(None)}
                requests[event.get("request")] = call
                calls.append(call)
            elif kind == "model_response" and event.get("request") in requests:
                call = requests[event["request"]]
                call.update(**_usage(event.get("usage")), status="rejected" if event.get("rejected") else "completed",
                            finished_at=event.get("time"), elapsed_seconds=_duration(call["started_at"], event.get("time")))
            elif kind == "sdk_attempt_failed" and event.get("request") in requests:
                requests[event["request"]]["sdk_failures"] += 1
            elif kind in {"model_error", "resource_budget_exhausted"} and requests:
                call = next(reversed(requests.values()))
                if call["status"] == "unresolved":
                    call.update(status="not_sent_budget" if kind == "resource_budget_exhausted" else "failed",
                                finished_at=event.get("time"), elapsed_seconds=_duration(call["started_at"], event.get("time")))
            elif kind == "tool_dispatch":
                tool = {**provenance, "step": event.get("step"), "tool": event.get("tool"),
                        "started_at": event.get("time"), "ok": None}
                dispatches[event.get("step")] = tool
                tools.append(tool)
            elif kind == "observation" and event.get("step") in dispatches:
                tool = dispatches[event["step"]]
                tool.update(ok=event.get("ok"), elapsed_seconds=_duration(tool["started_at"], event.get("time")))
        token_usage = {key: sum(row[key] or 0 for row in requests.values()) if events else _usage(facts.get("usage"))[key]
                       for key in TOKEN_KEYS}
        complete = (facts.get("usage_complete") is True and all(row.get(key) is not None for row in requests.values() for key in TOKEN_KEYS)
                    and not any(row["sdk_failures"] or row["status"] in {"unresolved", "failed"} for row in requests.values()))
        times = [t for e in events if (t := _timestamp(e.get("time"))) is not None]
        stages.append({**provenance, "receipt": ref + ("/facts.json" if facts else "/events.jsonl"),
                       "status": facts.get("status", "unknown"), **counts, **token_usage,
                       "usage_complete": complete, "sdk_failures": sum(e.get("kind") == "sdk_attempt_failed" for e in events),
                       "model_errors": sum(e.get("kind") == "model_error" for e in events),
                       "tool_failures": sum(e.get("kind") == "observation" and e.get("ok") is False for e in events),
                       "elapsed_seconds": max(times)-min(times) if len(times) > 1 else None})
    return stages, calls, tools


def _collect_budgets(root: Path, warnings: list[str], *, inherited_stage: Path | None = None,
                     source_run_root: str | None = None) -> list[dict[str, Any]]:
    ledgers: list[dict[str, Any]] = []
    for path in sorted(root.rglob("model_budget.v1.json")):
        if path.parent.name != "resources":
            continue
        raw = _record(path, warnings)
        requests = raw.get("requests", {})
        rows = [{"request_id": identifier, **{key: row.get(key) for key in
                 ("provider", "model", "status", "started_at", "finished_at", "usage_complete", "charged_tokens",
                  "reserved_tokens", "charged_cost", "reserved_cost", "price", "usage", "correlation")}}
                for identifier, row in requests.items() if isinstance(row, dict)] if isinstance(requests, dict) else []
        inherited = inherited_stage is not None and path.resolve().is_relative_to(inherited_stage)
        origin = "inherited_research" if inherited else "current_run"
        if not inherited and path.resolve().is_relative_to((root / "reused_research").resolve()):
            origin = "unclassified_inherited"
        ledgers.append({"path": _relative(root, path), "origin": origin,
                        "source_run_root": source_run_root if inherited else str(root.resolve()) if origin == "current_run" else None,
                        "limits": raw.get("configuration", {}).get("limits", {}),
                        "requests": rows, "accounting": "Reservations and charges are quota accounting, not invoices."})
    return ledgers


def _trace_usage(stages: list[dict[str, Any]], calls: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: sum(row[key] or 0 for row in stages)
              for key in (*COUNT_EVENTS, *TOKEN_KEYS, "sdk_failures", "model_errors", "tool_failures")}
    totals["usage_complete"] = bool(stages) and all(row["usage_complete"] for row in stages)
    totals["model_call_seconds"] = sum(row.get("elapsed_seconds") or 0 for row in calls)
    return totals


def collect_evidence(root: Path, manifest: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Read current evidence without calling models, loading tensors, or changing state."""
    warnings: list[str] = []
    protocol = _record(root / "experiment/protocol.json", warnings)
    reuse = _record(root / "context/research_reuse.json", warnings)
    inherited_stage: Path | None = None
    if reuse:
        relative = reuse.get("copied_stage_root")
        if reuse.get("schema") == "research.reuse.v1" and isinstance(relative, str) and not Path(relative).is_absolute():
            candidate = (root / relative).resolve()
            if (candidate.is_relative_to(root.resolve()) and candidate.is_relative_to((root / "reused_research").resolve())
                    and candidate.is_dir()):
                inherited_stage = candidate
        if inherited_stage is None:
            warnings.append("Research reuse receipt has no valid archived stage inside reused_research; inherited calls are not classified as new calls")
    trials: list[dict[str, Any]] = []
    channels: list[dict[str, Any]] = []
    for name, trial in state.get("trials", {}).items():
        item = {"trial": name, **{key: trial.get(key) for key in
                ("status", "operation", "real_parameters", "logical_parameters", "optimizer_steps", "selected_optimizer_steps",
                 "selected_epoch", "epochs_run", "stop_reason", "elapsed_seconds", "checkpoint_sha256", "candidate_sha256", "error")}}
        item["output"] = _relative(root, Path(trial["output"])) if trial.get("output") else None
        for split in ("validation", "test"):
            measurement = trial.get(split, {})
            for metric in ("RES_db", "channel_mean_db", "worst_channel_db"):
                item[f"{split}_{metric}"] = _number(measurement.get(metric))
            values = measurement.get("per_channel_db", [])
            for channel, value in enumerate(values if isinstance(values, list) else [], 1):
                channels.append({"trial": name, "split": split, "channel": channel, "RES_db": _number(value)})
        trials.append(item)
    attempts: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    for directory in sorted((root / "execution").glob("*/attempt_*")):
        if not directory.is_dir():
            continue
        result = _record(directory / "result.json", warnings)
        prefix = {"trial": directory.parent.name, "attempt": directory.name, "path": _relative(root, directory)}
        attempts.append({**prefix, **{key: result.get(key) for key in ("status", "operation", "error", "exit_code", "elapsed_seconds")}})
        for row in _rows(directory / "history.jsonl", warnings):
            history.append({**prefix, "epoch": row.get("epoch"), "optimizer_steps": row.get("optimizer_steps"),
                            "validation_RES_db": _number(row.get("validation", {}).get("RES_db"))})
        for row in _rows(directory / "steps.jsonl", warnings):
            steps.append({**prefix, **{key: row.get(key) for key in
                          ("optimizer_step", "epoch", "training_loss", "gradient_norm", "learning_rate", "elapsed_seconds")}})
    stages, calls, tools = _collect_traces(root, warnings)
    if inherited_stage is not None:
        inherited_stages, inherited_calls, inherited_tools = _collect_traces(
            root, warnings, scan_root=inherited_stage, origin="inherited_research", source_run_root=reuse.get("source_run_root"))
        stages.extend(inherited_stages)
        calls.extend(inherited_calls)
        tools.extend(inherited_tools)
    by_origin = {origin: _trace_usage([row for row in stages if row["origin"] == origin],
                                     [row for row in calls if row["origin"] == origin])
                 for origin in ("current_run", "inherited_research")}
    totals = _trace_usage(stages, calls)
    totals["worker_training_seconds"] = sum(row["elapsed_seconds"] or 0 for row in attempts)
    totals["worker_timing_complete"] = bool(attempts) and all(row["elapsed_seconds"] is not None for row in attempts)
    totals["run_wall_seconds"] = _duration(manifest.get("created_at"), state.get("completed_at"))
    budgets = _collect_budgets(root, warnings, inherited_stage=inherited_stage, source_run_root=reuse.get("source_run_root"))
    commits = [{"receipt": _relative(root, p), **_record(p, warnings)} for p in sorted((root / "coding").glob("*.receipt.json"))]
    diagnostics = [{"trial": name, **trial["data_diagnostics"]} for name, trial in state.get("trials", {}).items()
                   if isinstance(trial.get("data_diagnostics"), dict)]
    for diagnostic in diagnostics:
        for key in ("path", "plot_path"):
            if diagnostic.get(key):
                diagnostic[key] = _relative(root, Path(diagnostic[key]))
    refs = sorted({_relative(root, p) for parent in ("input", "context", "idea", "experiment", "coding", "writing", "resources")
                   for p in (root / parent).rglob("*") if p.is_file() and p.suffix in {".md", ".json", ".patch", ".diff"}})
    required = ("idea/proposal.md", "experiment/plan.md", "writing/final.md")
    missing = [name for name in required if not (root / name).is_file()]
    measured_test = all(state.get("trials", {}).get(name, {}).get("status") == "completed"
                        and _number(state["trials"][name].get("test", {}).get("RES_db")) is not None
                        for name in ("final_baseline", "final_candidate"))
    researched = any(str(row.get("tool", "")).startswith("search.") and row["ok"] is True for row in tools)
    engineering = (state.get("status") in {"goal_met_within_budget", "goal_not_met"} and measured_test
                   and not missing and bool(commits) and totals["model_responses"] > 0 and researched and bool(diagnostics))
    scientific = bool(state.get("final_comparison", {}).get("passed")) if measured_test and state.get("final_comparison") else None
    return {"schema": "cli_research.evidence.v1", "status": state.get("status", "unknown"), "error": state.get("error"),
            "identity": {key: manifest.get(key) for key in ("project", "task", "created_at", "repo", "data", "model", "source_commit",
                         "frozen_source_commit", "source_tracked_dirty", "source_snapshot", "protocol_sha256", "environment")},
            "protocol": protocol, "budget": manifest.get("budget", {}), "trials": trials, "channels": channels,
            "execution_attempts": attempts, "training_history": history, "training_steps": steps, "stages": stages,
            "model_calls": calls, "tools": tools, "resource_usage": totals, "model_budgets": budgets,
            "resource_usage_by_origin": by_origin, "research_reuse": reuse or None,
            "source_receipts": commits, "selected": state.get("selected"), "final_comparison": state.get("final_comparison"),
            "data_diagnostics": diagnostics,
            "stage_failures": state.get("stage_failures", {}), "evidence_links": refs, "warnings": warnings,
            "acceptance": {"engineering_complete": engineering, "scientific_goal_met": scientific,
                           "independent_test_available": measured_test, "missing_stage_artifacts": missing,
                           "successful_research_tool_observed": researched, "data_analysis_available": bool(diagnostics),
                           "generalization_established": False}}


def _csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _chart(path: Path, title: str, ylabel: str, series: list[tuple[str, list[tuple[float, float]]]],
           *, xlabel: str, labels: list[str] | None = None) -> bool:
    series = [(name, values) for name, values in series if values]
    if not series:
        return False
    width, height, left, right, top, bottom = 1120, max(580, 150+25*len(series)), 85, 790, 75, 450
    xs, ys = [x for _, values in series for x, _ in values], [y for _, values in series for _, y in values]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if xmin == xmax:
        xmin, xmax = xmin-.5, xmax+.5
    margin = max((ymax-ymin)*.1, .1)
    ymin, ymax = ymin-margin, ymax+margin
    def xmap(x: float) -> float:
        return left+(x-xmin)/(xmax-xmin)*(right-left)
    def ymap(y: float) -> float:
        return bottom-(y-ymin)/(ymax-ymin)*(bottom-top)
    colors = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be185d", "#475569")
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
           f'<title>{escape(title)}</title><rect width="100%" height="100%" fill="#ffffff"/>',
           '<g font-family="Arial, sans-serif" fill="#172033">',
           f'<text x="{left}" y="32" font-size="23" font-weight="bold">{escape(title)}</text>',
           f'<text x="{left}" y="55" font-size="13" fill="#64748b">Recorded observations only; lower RES is better.</text>']
    for index in range(6):
        value = ymin+(ymax-ymin)*index/5
        y = ymap(value)
        svg += [f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="#e2e8f0"/>',
                f'<text x="{left-12}" y="{y+5:.2f}" text-anchor="end" font-size="13">{value:.3g}</text>']
    ticks = [(float(i), label) for i, label in enumerate(labels)] if labels else [(xmin+(xmax-xmin)*i/5, f"{xmin+(xmax-xmin)*i/5:.4g}") for i in range(6)]
    for x, label in ticks:
        svg.append(f'<text transform="translate({xmap(x):.2f},{bottom+22}) rotate({-25 if labels else 0})" text-anchor="{"end" if labels else "middle"}" font-size="12">{escape(label)}</text>')
    for index, (name, values) in enumerate(series):
        color = colors[index % len(colors)]
        if labels is None:
            points = " ".join(f"{xmap(x):.2f},{ymap(y):.2f}" for x, y in values)
            svg.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y in values:
            svg.append(f'<circle cx="{xmap(x):.2f}" cy="{ymap(y):.2f}" r="{4 if labels else 2}" fill="{color}"><title>{escape(name)}: x={x:g}, y={y:.6g}</title></circle>')
        svg += [f'<line x1="820" y1="{90+index*25}" x2="844" y2="{90+index*25}" stroke="{color}" stroke-width="3"/>',
                f'<text x="852" y="{95+index*25}" font-size="12">{escape(name)}</text>']
    svg += [f'<text x="{(left+right)/2}" y="555" font-size="14" text-anchor="middle">{escape(xlabel)}</text>',
            f'<text transform="translate(20,{(top+bottom)/2}) rotate(-90)" font-size="14" text-anchor="middle">{escape(ylabel)}</text>', '</g></svg>']
    path.write_text("\n".join(svg), encoding="utf-8")
    return True


def _charts(directory: Path, evidence: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for split in ("validation", "test"):
        rows = [row for row in evidence["trials"] if row[f"{split}_RES_db"] is not None]
        if _chart(directory / f"{split}_comparison.svg", f"{split.title()} RES comparison", "RES (dB)",
                  [(split, [(float(i), row[f"{split}_RES_db"]) for i, row in enumerate(rows)])],
                  xlabel="Method / trial", labels=[row["trial"] for row in rows]):
            paths.append(f"{split}_comparison.svg")
        names = sorted({row["trial"] for row in evidence["channels"] if row["split"] == split})
        series = [(name, [(float(row["channel"]), row["RES_db"]) for row in evidence["channels"]
                          if row["trial"] == name and row["split"] == split and row["RES_db"] is not None]) for name in names]
        if _chart(directory / f"{split}_channels.svg", f"{split.title()} per-channel RES", "RES (dB)", series, xlabel="Channel (1-based)"):
            paths.append(f"{split}_channels.svg")
    for source, xkey, ykey, filename, title in (
        ("training_history", "optimizer_steps", "validation_RES_db", "validation_history.svg", "Validation during training"),
        ("training_steps", "optimizer_step", "training_loss", "training_loss.svg", "Recorded optimizer loss")):
        series = []
        names = sorted({(row["trial"], row["attempt"]) for row in evidence[source]})
        for trial, attempt in names:
            points = [(float(row[xkey]), float(row[ykey])) for row in evidence[source]
                      if row["trial"] == trial and row["attempt"] == attempt and _number(row.get(xkey)) is not None and _number(row.get(ykey)) is not None]
            series.append((f"{trial}/{attempt}", points))
        if _chart(directory / filename, title, "RES (dB)" if source == "training_history" else "Training loss", series, xlabel="Actual optimizer updates"):
            paths.append(filename)
    return paths


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _link(relative: str) -> str:
    return f"[{relative}]({quote(relative, safe='/')})"


def write_report(root: Path, manifest: dict[str, Any], state: dict[str, Any]) -> None:
    """Write an auditable partial or completed report and machine-readable exports."""
    evidence = collect_evidence(root, manifest, state)
    directory = root / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    charts = _charts(directory, evidence)
    evidence["charts"] = [f"evidence/{name}" for name in charts]
    atomic_json(directory / "summary.json", evidence)
    exports = {
        "trials": ["trial", "status", "operation", "real_parameters", "validation_RES_db", "test_RES_db", "optimizer_steps", "selected_optimizer_steps", "stop_reason", "elapsed_seconds", "output", "error"],
        "channels": ["trial", "split", "channel", "RES_db"],
        "training_history": ["trial", "attempt", "epoch", "optimizer_steps", "validation_RES_db", "path"],
        "training_steps": ["trial", "attempt", "optimizer_step", "epoch", "training_loss", "gradient_norm", "learning_rate", "elapsed_seconds", "path"],
        "model_calls": ["origin", "source_run_root", "trace", "request", "provider", "model", "phase", "status", "prompt_tokens", "completion_tokens", "total_tokens", "sdk_failures", "elapsed_seconds"],
        "tools": ["origin", "source_run_root", "trace", "step", "tool", "ok", "elapsed_seconds"],
        "stages": ["origin", "source_run_root", "trace", "status", *COUNT_EVENTS, *TOKEN_KEYS, "usage_complete", "sdk_failures", "model_errors", "tool_failures", "elapsed_seconds"],
    }
    for name, fields in exports.items():
        _csv(directory / f"{name}.csv", evidence[name], fields)
    acceptance, usage, protocol, budget = evidence["acceptance"], evidence["resource_usage"], evidence["protocol"], evidence["budget"]
    body = [f"# MARS 真实运行与仿真报告\n\n状态：**{_display(evidence['status'])}**\n\n任务：{manifest.get('task', '')}",
            "本文件由宿主依据已归档证据生成；大模型最终解释见 " + (_link("writing/final.md") if (root / "writing/final.md").is_file() else "尚未生成的 writing/final.md") + "。",
            "## 验收结论",
            f"工程端到端闭环：**{'完成' if acceptance['engineering_complete'] else '尚未具备完整证据'}**。包括调研、实验设计、候选 Git 收据、独立测试与最终报告。",
            f"当前预算内的科研目标：**{'达标' if acceptance['scientific_goal_met'] else '未达标' if acceptance['scientific_goal_met'] is False else '尚未判定'}**。",
            "模型收敛、多种子稳定性及跨采集泛化：**本次运行不能证明**。工程跑通与科研指标达标分别判定。"]
    if acceptance["missing_stage_artifacts"]:
        body.append("缺少阶段产物：" + "、".join(acceptance["missing_stage_artifacts"]) + "。")
    if evidence["error"]:
        body.append("当前错误或限制：" + _display(evidence["error"]))
    if evidence["research_reuse"]:
        reuse = evidence["research_reuse"]
        body += ["## 复用的已审查研究",
                 "本运行采用先前真实调用生成并通过审查的研究提案。继承 trace 保留原调用时间与原路径，复制证据不会重新发送这些 API 请求。实验设计、代码生成与仿真等新阶段按本运行记录单独计数。",
                 f"原运行：`{reuse.get('source_run_root', '—')}`；原研究阶段：`{reuse.get('source_stage_root', '—')}`。",
                 f"复用时间：{reuse.get('reused_at', '—')}；提案 SHA-256：`{reuse.get('source_artifact_sha256', '—')}`。",
                 f"复用收据：{_link('context/research_reuse.json')}；继承阶段副本：{_link(str(reuse.get('copied_stage_root', 'reused_research/stage')))}。",
                 "副本中的历史正文收据和上下文可能仍包含原运行绝对路径；应结合复用收据中的 stage_files 哈希核查。历史失败、拒绝和已知用量一并保留，不当作本运行新发生的调用。"]
    body += ["## 数据、协议与代码身份", f"数据：`{manifest.get('data', '—')}`\n\n冻结数据 SHA-256：`{protocol.get('data_sha256', '—')}`。",
             f"源仓库：`{manifest.get('repo', '—')}`；源 HEAD：`{manifest.get('source_commit', '—')}`；冻结源码提交：`{manifest.get('frozen_source_commit', '—')}`；源受跟踪文件有改动：{manifest.get('source_tracked_dirty', '—')}。",
             f"协议：{_link('experiment/protocol.json')}；输入：{_link('input/manifest.json')}。",
             "```json\n" + json.dumps({key: protocol.get(key) for key in ("seed", "scale", "fs", "band", "split_guard", "train_fraction", "validation_fraction", "metric", "initialization", "selection")}, ensure_ascii=False, indent=2) + "\n```",
             "基线与候选按冻结协议从头训练。候选仅依据验证集选择，选定后使用独立测试集；测试分数不能反向用于本次候选修改。",
             "| 候选收据 | Git 提交 | 父提交 | Gate 5 |\n|---|---|---|---|"]
    for receipt in evidence["source_receipts"]:
        body.append(f"| {_link(receipt['receipt'])} | `{receipt.get('source_commit', '—')}` | `{receipt.get('parent_source_commit', '—')}` | {_display(receipt.get('gate5'))} |")
    for relative in evidence["evidence_links"]:
        if relative.endswith((".patch", ".diff")):
            body.append("候选与基线差异：" + _link(relative))
    body.append("## 真实数据分析")
    if not evidence["data_diagnostics"]:
        body.append("尚无训练数据统计证据；不补写信号形状、功率、频谱或相关性数值。")
    for diagnostic in evidence["data_diagnostics"]:
        summary = diagnostic.get("summary", {})
        body.append(f"来源：{_link(diagnostic['path'])}；SHA-256：`{diagnostic.get('sha256', '—')}`。")
        body.append("```json\n" + json.dumps({key: summary.get(key) for key in
                    ("statistics_split", "held_out_statistics_included", "raw_arrays", "raw_layout", "model_layout",
                     "model_dtype", "training_samples", "channels", "scale_divisor", "frequency_unit",
                     "sampling_frequency", "objective_band", "spectrum_frame_count", "spectrum_method")},
                     ensure_ascii=False, indent=2) + "\n```")
        body.append("| 信号 | 平均功率（缩放后） | 峰值幅度 | 通道间平均相关幅度 | 带内功率占比 |\n|---|---:|---:|---:|---:|")
        for name, values in summary.get("arrays", {}).items():
            body.append("| " + " | ".join([name, *[_display(values.get(key)) for key in
                         ("mean_power_scaled", "peak_amplitude_scaled", "mean_abs_off_diagonal_correlation",
                          "spectrum_objective_band_power_fraction")]]) + " |")
        if diagnostic.get("plot_path"):
            body.append(f"![Training data diagnostics]({quote(diagnostic['plot_path'], safe='/')})")
    body += ["## 实验结果", "RES 定义为 10log10(mean_channel(Perr/Pnf))，越低越好；它不等于逐通道 dB 的算术均值。未测量项保留为空。",
             "| 实验 | 状态 | 实参数 | 验证 RES/dB | 测试 RES/dB | 实际更新 | 选中更新 | 耗时/秒 |\n|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in evidence["trials"]:
        body.append("| " + " | ".join(_display(row.get(key)) for key in ("trial", "status", "real_parameters", "validation_RES_db", "test_RES_db", "optimizer_steps", "selected_optimizer_steps", "elapsed_seconds")) + " |")
    if evidence["final_comparison"]:
        body.append("最终比较（仅依据宿主测量）：\n```json\n" + json.dumps(evidence["final_comparison"], ensure_ascii=False, indent=2) + "\n```")
    for chart in evidence["charts"]:
        body.append(f"![{Path(chart).stem}]({chart})")
    body += ["## 资源预算、模型与工具调用", "冻结计算预算：\n```json\n" + json.dumps(budget, ensure_ascii=False, indent=2) + "\n```",
             "更新数表示 optimizer.step 次数，不表示 epoch 数。以下 Token 来自 trace 的实际响应 usage；失败请求未返回的用量不能视为零。",
             "| 调用来源 | 模型请求 | 响应 | SDK 尝试 | 工具派发 | 输入 Token | 输出 Token | 总 Token |\n|---|---:|---:|---:|---:|---:|---:|---:|"]
    origin_labels = {"current_run": "本运行新发生", "inherited_research": "继承的历史研究", "unclassified_inherited": "未分类的历史副本"}
    for origin, values in evidence["resource_usage_by_origin"].items():
        body.append("| " + " | ".join([origin_labels[origin], *[_display(values[key]) for key in
                    ("model_requests", "model_responses", "sdk_attempts", "tool_dispatches", *TOKEN_KEYS)]]) + " |")
    body += [f"证据总计（本运行新调用与继承历史之和）：模型请求 {usage['model_requests']}；响应 {usage['model_responses']}；SDK 尝试 {usage['sdk_attempts']}；工具派发 {usage['tool_dispatches']}。总计不能称作本运行新发送的 API 次数。",
             f"证据总计的已知输入 Token：{usage['prompt_tokens']}；已知输出 Token：{usage['completion_tokens']}；已知总 Token：{usage['total_tokens']}。完整用量：{'是' if usage['usage_complete'] else '否，属于已知下界或尚无调用证据'}。",
             f"SDK 失败：{usage['sdk_failures']}；模型错误事件：{usage['model_errors']}；工具失败：{usage['tool_failures']}。这些计数不可相加为独立失败请求数。",
             f"已观测模型调用耗时合计：{usage['model_call_seconds']:.3f} 秒；已记录 worker 训练耗时：{usage['worker_training_seconds']:.3f} 秒。二者不是端到端墙钟耗时；并发、未结算调用与无计时 worker 不据此补算。",
             f"从运行创建到完成的墙钟耗时：{_display(usage['run_wall_seconds'])} 秒（包含等待与恢复间隔；仅在完成时间已归档时计算）。",
             "费用：未进行价格推算。资源账本的 charged/reserved 数值仅作为配额记账证据，不能当作服务商账单。",
             "| 阶段 trace | 调用来源 | 状态 | 请求 | 输入 Token | 输出 Token | 工具 | 耗时/秒 |\n|---|---|---|---:|---:|---:|---:|---:|"]
    for stage in evidence["stages"]:
        body.append("| " + " | ".join([_link(stage["receipt"]), origin_labels[stage["origin"]], *[_display(stage.get(key)) for key in ("status", "model_requests", "prompt_tokens", "completion_tokens", "tool_dispatches", "elapsed_seconds")]]) + " |")
    for ledger in evidence["model_budgets"]:
        body.append(f"模型配额与逐请求保留记录：{_link(ledger['path'])}；来源：{origin_labels[ledger['origin']]}；共 {len(ledger['requests'])} 条，不与 trace 用量重复累加。历史账本不计为本运行新发生的配额消耗。")
        body.append("冻结模型配额：\n```json\n" + json.dumps(ledger["limits"], ensure_ascii=False, indent=2) + "\n```")
        charged = sum(row["charged_tokens"] for row in ledger["requests"] if type(row.get("charged_tokens")) is int)
        unresolved = sum(row.get("usage_complete") is not True for row in ledger["requests"])
        body.append(f"配额已记账 Token（包含未知调用保留量）：{charged}；用量不完整请求：{unresolved}。此数值不作为实际 Token 消耗。")
    failures = [row for row in evidence["execution_attempts"] if row["status"] != "completed"]
    if failures or evidence["stage_failures"]:
        body += ["## 失败与恢复证据", "```json\n" + json.dumps({"workers": failures, "stages": evidence["stage_failures"]}, ensure_ascii=False, indent=2) + "\n```"]
    body += ["## 限制与可复查证据", "单个采集、固定划分、固定种子和有限更新仅支持本次预算下的比较。未执行的消融、充分收敛验证和多采集评测不得写成已完成结果。CPU 训练耗时不能外推部署吞吐或 GPU 性能。",
             "候选代码检查和子进程隔离不是针对恶意 Python 的操作系统安全沙箱。",
             "- " + _link("evidence/summary.json"), *["- " + _link(f"evidence/{name}.csv") for name in exports],
             *["- " + _link(relative) for relative in evidence["evidence_links"]]]
    for trial in evidence["trials"]:
        if trial["output"]:
            body.append("- " + _link(trial["output"] + "/result.json") + " · " + _link(trial["output"] + "/worker.log"))
    if evidence["warnings"]:
        body += ["证据读取警告（保留不完整状态）：", *["- " + _display(item) for item in evidence["warnings"]]]
    metadata = {"schema": "report.v1", "project": manifest.get("project", "research"), "agent": "writing",
                "generated_by": "host_evidence_renderer", "deliverable_type": "research_report", "target_audience": "研究者",
                "chain_refs": {"proposal": "idea/proposal.md", "plan": "experiment/plan.md",
                               "runs": list(state.get("trials", {}))}}
    document = dumps(metadata, "\n\n".join(body).replace("|\n\n|", "|\n|"))
    if not validate_document(document, expected_schema="report.v1").valid:
        raise ValueError("Host evidence report schema validation failed")
    (root / "report.md").write_text(document, encoding="utf-8")
