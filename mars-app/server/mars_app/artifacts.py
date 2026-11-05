"""Read-only views over mars-core run directories (runs/<id>/), following the contract in the MARS plan §4.3.

Events and the coarse phase come from `mars.api`; these pure functions add the details the dashboard needs.
Missing or partial artifacts (a run in progress) are normal.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

import yaml

STEP1_STAGES = [("analysis", "问题分析"), ("sources", "检索"), ("cards", "证据卡"), ("proposals", "提案"),
                ("critiques", "质询"), ("debate", "答辩修订"), ("ranking", "评审排序"), ("handoff", "交接包")]
ROUTE_STARTED = re.compile(r"^(\S+) 第 (\d+) 轮：开始实现")
ROUTE_FINISHED = re.compile(r"^(\S+) 第 (\d+) 轮完成：(\S+)")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
PLOT_NAME = re.compile(r"^(baseline_)?(screen|verify)_[A-Za-z0-9_.-]+\.png$")  # {stage}_{dataset}_{file}.png


def _json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def is_run_dir(path: Path) -> bool:
    return path.is_dir() and RUN_ID.match(path.name) is not None and (path / "contract.yaml").exists()


def contract(run_dir: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load((run_dir / "contract.yaml").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _round_dirs(run_dir: Path) -> list[Path]:
    rounds = [p for p in (run_dir / "step1").glob("r*") if p.is_dir() and p.name[1:].isdigit()]
    return sorted(rounds, key=lambda p: int(p.name[1:]))


# A coding turn can take ~40 min and a full-budget verification ~20 min, so silence below an hour is normal.
STALL_SECONDS = 2 * 3600
# A model failure counts as the current cause while it is within this distance of the run's last event.
FAILURE_FRESH_SECONDS = 3600
MODEL_FAILURES = {"model_quota": "模型余额不足", "model_api": "模型服务异常"}


def _epoch(ts: str | None) -> float | None:
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")) if ts else None
    except ValueError:
        return None


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} 小时" if seconds >= 3600 else f"{int(seconds // 60)} 分钟"


def alerts(state: str, health: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Things a researcher must notice, from api.status()["health"]: a model failure that stalled the run, a running
    process that has gone silent, a run that stopped before finishing, an approval left waiting."""
    if state == "finished" or not health:
        return []
    found: list[dict[str, Any]] = []
    idle = health.get("seconds_since_last_event")
    failure = health.get("last_failure") or {}
    last_at, failed_at = _epoch(health.get("last_event_ts")), _epoch(failure.get("ts"))
    if failure.get("kind") in MODEL_FAILURES and last_at is not None and failed_at is not None \
            and last_at - failed_at <= FAILURE_FRESH_SECONDS:
        found.append({"level": "error", "code": failure["kind"],
                      "title": f"{MODEL_FAILURES[failure['kind']]}，研究已停滞", "params": {},
                      "detail": f"{failure.get('ts')} {failure.get('message', '')}",
                      "error": str(failure.get("error", ""))[:600], "since": failure.get("ts")})
    if state == "running" and isinstance(idle, (int, float)) and idle > STALL_SECONDS:
        found.append({"level": "warn", "code": "stalled", "title": f"可能停滞：{_hours(idle)}没有新事件", "params": {"hours": round(idle / 3600, 1)},
                      "detail": "进程还在，但很久没有进展。常见原因：本机睡眠、GPU 远程任务卡住、模型调用挂起。",
                      "since": health.get("last_event_ts")})
    if state == "stopped":
        found.append({"level": "warn", "code": "stopped", "title": "进程已退出，研究没有在推进",
                      "params": {"last_event_ts": health.get("last_event_ts")},
                      "detail": f"最后一条事件在 {health.get('last_event_ts')}"
                                + (f"（{_hours(idle)}前）" if isinstance(idle, (int, float)) else "")
                                + "。处理完原因后可以从检查点续跑。",
                      "since": health.get("last_event_ts")})
    if state == "waiting_approval" and isinstance(idle, (int, float)) and idle > 600:
        found.append({"level": "info", "code": "awaiting_approval", "title": f"等待你确认进入 Step2 的方案（已等 {_hours(idle)}）",
                      "params": {"hours": round(idle / 3600, 1)},
                      "detail": "运行停在 Step1 → Step2 的确认点，确认前不会继续。", "since": health.get("last_event_ts")})
    return found


def derive_state(phase: str, running: bool, events: list[dict[str, Any]]) -> str:
    """Dashboard state from api.status(): running | finished | waiting_approval | stopped (process gone) | new."""
    if phase == "finished":
        return "finished"
    if running:
        return "running"
    if phase == "awaiting_approval":
        return "waiting_approval"
    return "stopped" if events else "new"


def _proposal_items(round_dir: Path) -> list[dict[str, Any]]:
    """Judged proposals (prechecks re-derived by the judge) when present, else the debate output."""
    items = _json(round_dir / "judged_proposals.json")
    if not isinstance(items, list):
        items = _json(round_dir / "debate.json", []) or []
    return [item for item in items if isinstance(item, dict)]


def step1_rounds(run_dir: Path) -> list[dict[str, Any]]:
    rounds = []
    for directory in _round_dirs(run_dir):
        stages = [{"key": key, "label": label, "done": (directory / f"{key}.json").exists()} for key, label in STEP1_STAGES]
        ranking = _json(directory / "ranking.json", []) or []
        proposals = {item.get("proposal", {}).get("id"): item.get("proposal", {}) for item in _proposal_items(directory)}
        handoff = [p.get("id") for p in _json(directory / "handoff.json", []) or []]
        cards = _json(directory / "cards.json", []) or []
        analysis = _json(directory / "analysis.json", {}) or {}
        rounds.append({
            "round": directory.name,
            "stages": stages,
            "directions": [{"id": d.get("id"), "name": d.get("name"), "hypothesis": d.get("hypothesis_seed")}
                           for d in analysis.get("directions", [])],
            "cards": {"total": len(cards), "verified": sum(1 for c in cards if c.get("verified"))},
            "ranking": [{"id": r.get("id"), "title": r.get("title"), "score": r.get("score"),
                         "precheck": r.get("precheck", []), "duplicate_of": r.get("duplicate_of"),
                         "one_sentence": proposals.get(r.get("id"), {}).get("one_sentence", ""),
                         "handed_off": r.get("id") in handoff} for r in ranking],
            "handoff": handoff,
        })
    return rounds


def approval(run_dir: Path, round_name: str, ranking: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    """What a reviewer needs to pick the proposals that enter Step2: api.status()'s pending_approval (the ranking)
    joined with the judged proposals and their debate, in the judges' order."""
    round_dir = run_dir / "step1" / round_name
    items = {item.get("proposal", {}).get("id"): item for item in _proposal_items(round_dir)}
    routes_budget = int((spec.get("budget") or {}).get("routes", 2))
    candidates = []
    cards = _json(round_dir / "cards.json", []) or []
    for entry in ranking:
        pid = entry.get("id")
        item = items.get(pid, {})
        proposal = item.get("proposal", {})
        severities = [issue.get("severity") for critique in item.get("critiques", []) for issue in critique.get("issues", [])]
        candidates.append({
            "id": pid, "title": entry.get("title") or proposal.get("title", ""), "score": entry.get("score"),
            "judge_scores": entry.get("judge_scores", []), "precheck": entry.get("precheck", []),
            "duplicate_of": entry.get("duplicate_of"),
            "one_sentence": proposal.get("one_sentence", ""),
            "mechanism_hypothesis": proposal.get("mechanism_hypothesis", ""),
            "removed_or_shrunk": proposal.get("removed_or_shrunk", ""),
            "added_capability": proposal.get("added_capability", ""),
            "resource_estimate": proposal.get("resource_estimate", ""),
            "constraints_argument": proposal.get("constraints_argument", ""),
            "history_relation": proposal.get("history_relation", ""),
            "falsification": proposal.get("falsification", ""),
            "evidence": [e.get("id") for e in proposal.get("evidence", [])],
            "critiques": {"fatal": severities.count("fatal"), "major": severities.count("major"),
                          "minor": severities.count("minor"), "verdicts": [c.get("verdict") for c in item.get("critiques", [])]},
            "rebuttals": len(item.get("rebuttals", [])),
            "proposal": proposal,
            "critique_details": item.get("critiques", []),
            "rebuttal_details": item.get("rebuttals", []),
            "evidence_cards": [card for card in cards if card.get("id") in {e.get("id") for e in proposal.get("evidence", [])}],
        })
    eligible = [c["id"] for c in candidates if not c["precheck"] and not c["duplicate_of"]]
    return {"round": round_dir.name, "routes_budget": routes_budget, "candidates": candidates,
            "default_selection": eligible[:routes_budget]}


def validate_selection(selection: list[str], pending: dict[str, Any]) -> list[str]:
    """Approved ids must come from this round's ranking (the core's handoff fails on unknown ids)."""
    known = {c["id"] for c in pending["candidates"]}
    problems = []
    if not selection:
        problems.append("至少选择一个方案")
    unknown = [s for s in selection if s not in known]
    if unknown:
        problems.append(f"不在本轮排序中的方案：{unknown}")
    if len(set(selection)) != len(selection):
        problems.append("方案重复")
    return problems


def metrics_of(run_dir: Path) -> dict[str, Any]:
    """The metric declaration a run was bound to (execution.json): labels, units, directions and the primary metric."""
    binding = (_json(run_dir / "execution.json", {}) or {}).get("binding") or {}
    return (binding.get("config") or {}).get("metrics") or {"primary": None, "fields": {}}


def dataset_order(name: str) -> tuple[int, str]:
    """Numeric names in numeric order, then the others alphabetically (project datasets are free names)."""
    digits = "".join(ch for ch in str(name) if ch.isdigit())
    return (int(digits), str(name)) if digits and str(name).rstrip("MmKkGg").isdigit() else (10**9, str(name))


def datasets_of(*groups: dict[str, Any]) -> list[str]:
    """The datasets an iteration was measured on: every key holding a per-dataset entry (not flags like "passed")."""
    names = {d for group in groups for d, value in group.items() if isinstance(value, dict)}
    return sorted(names, key=dataset_order)


def _per_dataset(entries: dict[str, Any], key: str, datasets: list[str]) -> dict[str, Any]:
    return {d: (entries.get(d) or {}).get(key) for d in datasets}


def _primary_gap(row: dict[str, Any]) -> Any:
    return (row.get("worse_by") or {}).get(row.get("primary")) if isinstance(row, dict) else None


def iteration_score(item: dict[str, Any], targets: dict[str, Any] | None = None,
                    datasets: list[str] | None = None) -> tuple[int, float, float]:
    """Prefer complete verification, then complete screening; compare the worst dataset over the contract's gates.

    This selects a record to inspect. It never changes the recorded gate result or promotes a candidate.
    Missing or non-finite measurements cannot look better than a complete evaluation.
    """
    targets = targets or {}
    verify, screen = item.get("verify") or {}, item.get("screen") or {}
    wanted = set(datasets or item.get("datasets") or datasets_of(verify, screen))
    finite = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    gates = targets.get("gates") or []
    gaps = []
    complete = set()
    for dataset, row in verify.items():
        if not isinstance(row, dict) or dataset not in wanted:
            continue
        worse = row.get("worse_by") or {}
        names = [g.get("metric") for g in gates] or list(worse)[:1]
        if names and all(finite(worse.get(n)) for n in names):
            complete.add(dataset)
            limits = {g.get("metric"): float(g.get("max_worse", 0.0)) for g in gates}
            gaps.extend(worse[n] - limits.get(n, 0.0) for n in names)
    if wanted and complete == wanted:
        return 0, max(gaps), sum(gaps) / len(gaps)
    deltas = [_primary_gap(row) for d, row in screen.items() if d in wanted and finite(_primary_gap(row))]
    if wanted and len(deltas) == len(wanted):
        return 1, max(deltas), sum(deltas) / len(deltas)
    if gaps:
        return 2, float(len(wanted - complete)), max(gaps)
    return (3, max(deltas), sum(deltas) / len(deltas)) if deltas else (4, math.inf, math.inf)


def recommended_iteration(routes: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any] | None:
    datasets = sorted({d for r in routes for it in r["iterations"] for d in it.get("datasets", [])})
    choices = [(r["id"], it) for r in routes for it in r["iterations"]]
    if not choices:
        return None
    route, chosen = min(choices, key=lambda pair: iteration_score(pair[1], spec.get("targets"), datasets))
    return {"route": route, "iteration": chosen["iteration"]}


def followups(reports: Path, run_id: str) -> list[dict[str, Any]]:
    """Application-owned, explicitly imported supplemental evaluations; copied demos need no workspace."""
    path = reports / run_id / "followups.json"
    if not path.resolve().is_relative_to(reports.resolve()) or not path.is_file() or path.stat().st_size > 1_000_000:
        return []
    data = _json(path, {})
    groups = data.get("experiments", []) if isinstance(data, dict) and data.get("run_id") == run_id else []
    return [g for g in groups if isinstance(g, dict) and isinstance(g.get("rows"), list)] if isinstance(groups, list) else []


def iteration_row(record: dict[str, Any]) -> dict[str, Any]:
    """One table row per Step2 iteration: the gates and metrics the core recorded, as the project declared them."""
    evidence, decision = record.get("evidence") or {}, record.get("decision") or {}
    screen = evidence.get("screen") or {}
    verify = evidence.get("verify") or {}
    gates = evidence.get("check_gates") or {}
    assessment = decision.get("mechanism_assessment", "") or ""
    datasets = datasets_of(screen, verify)
    metric_keys = ("metrics", "baseline_metrics", "worse_by", "primary", "margin", "passed", "ok", "error")
    return {
        "iteration": record.get("iteration"),
        "datasets": datasets,
        "summary": (record.get("task") or {}).get("summary", ""),
        "branch": record.get("branch"), "commit": record.get("commit"),
        "implemented": (record.get("implementation") or {}).get("ok"),
        "stopped_at": evidence.get("stopped_at"),
        "check_gates": {"all": gates.get("all"), "stop": gates.get("stop"), "gates": gates.get("gates") or []},
        "screen_gap": {d: _primary_gap(screen.get(d)) for d in datasets},
        "screen_passed": evidence.get("screen_passed"),
        "verify_passed": verify.get("passed"),
        "verdict": decision.get("verdict"),
        "assessment": assessment,
        "decision_error": bool(decision.get("failed")) or assessment.startswith("决策调用失败"),
        "next_change": (decision.get("next_change") or {}).get("summary"),
        "operator_notes": [n for n in decision.get("operator_notes") or [] if isinstance(n, dict)],
        # Workbench step log: the measured numbers behind each gate, and the images the core copied into runs/.
        "implementation": {"ok": (record.get("implementation") or {}).get("ok"),
                           "turns": len((record.get("implementation") or {}).get("turns") or []),
                           "files": record.get("diff_files") or []},
        "prequick": {k: (evidence.get("prequick") or {}).get(k) for k in ("passed", "metrics", "gates", "error")},
        "screen": {d: {k: (screen.get(d) or {}).get(k) for k in metric_keys} for d in datasets if d in screen},
        "verify": {d: {k: (verify.get(d) or {}).get(k) for k in metric_keys + ("gates",)}
                   for d in datasets if isinstance(verify.get(d), dict)},
        "diagnostics": [x for x in (evidence.get("diagnostics") or []) + (evidence.get("auto_diagnostics") or []) if isinstance(x, dict)],
        "plots": {k: v for k, v in (evidence.get("plots") or {}).items() if PLOT_NAME.match(k)},
        "parent_iteration": None,
        "base_commit": None,
    }


def route_notes(run_dir: Path, route_id: str) -> list[dict[str, Any]]:
    """Notes left for a route (api.add_route_note) and, once a decision has read them, whether each was adopted:
    the decision records {note, adopted, reason} in decision["operator_notes"]."""
    if not RUN_ID.match(route_id):
        return []
    path = run_dir / f"step2/{route_id}/notes.jsonl"
    notes = []
    for line in (path.read_text(encoding="utf-8").splitlines() if path.is_file() else []):
        try:
            notes.append(json.loads(line))
        except ValueError:
            continue  # a line caught mid-write
    notes = [{"ts": n.get("ts"), "text": n.get("text", "")} for n in notes if isinstance(n, dict)]
    # Each decision answers the notes still open, in order; the model may quote a note loosely, so an answer goes to
    # the note with the same text, else to the oldest open one.
    for item in sorted((run_dir / f"step2/{route_id}").glob("i*/record.json"), key=lambda p: int(p.parent.name[1:])):
        record = _json(item, {}) or {}
        for answer in (record.get("decision") or {}).get("operator_notes") or []:
            open_ = [n for n in notes if "answer" not in n]
            if isinstance(answer, dict) and open_:
                note = next((n for n in open_ if n["text"].strip() == str(answer.get("note", "")).strip()), open_[0])
                note["answer"] = {**answer, "iteration": record.get("iteration")}
    return notes


def _route_event(event: dict[str, Any]) -> tuple[str | None, int, str]:
    """(route, iteration, status) of a Step2 iteration start ("implementing") or finish (the route's status).
    The core's structured fields come first; runs recorded before they existed fall back to the message text."""
    route_id, k, state = event.get("route"), event.get("iteration"), event.get("status")
    if isinstance(route_id, str) and isinstance(k, int) and isinstance(state, str) and event.get("stage") == "step2":
        return route_id, k, state
    if match := ROUTE_STARTED.match(event.get("message", "")):
        return match[1], int(match[2]), "implementing"
    if match := ROUTE_FINISHED.match(event.get("message", "")):
        return match[1], int(match[2]), match[3]
    return None, 0, ""


def routes(run_dir: Path, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    final = {r["id"]: r for r in _json(run_dir / "step2/routes_final.json", []) or [] if isinstance(r, dict)}
    status: dict[str, str] = {}
    in_progress: dict[str, int] = {}
    for event in events:
        route_id, k, state = _route_event(event)
        if route_id is None:
            continue
        if state == "implementing":
            in_progress[route_id] = k
        else:
            status[route_id] = state
            if in_progress.get(route_id) == k:
                in_progress.pop(route_id)
    found = []
    step2 = run_dir / "step2"
    # Route directories are proposal ids (P31, U00); step2/plots holds the baseline images, not a route.
    for directory in sorted(p for p in step2.glob("[A-Z]*") if p.is_dir()) if step2.exists() else []:
        records = []
        for item in directory.glob("i*/record.json"):
            record = _json(item)
            if isinstance(record, dict):
                records.append(iteration_row(record))
        records.sort(key=lambda r: r["iteration"] or 0)
        rid = directory.name
        route_final = final.get(rid, {})
        found.append({"id": rid, "title": (route_final.get("proposal") or {}).get("title") or _title_from_handoff(run_dir, rid),
                      "status": route_final.get("status") or status.get(rid, "active"),
                      "running_iteration": in_progress.get(rid), "best": route_final.get("best"),
                      "iterations": records})
    return found


def _title_from_handoff(run_dir: Path, route_id: str) -> str:
    for path in sorted((run_dir / "step1").glob("r*/handoff.json"), reverse=True):
        for package in _json(path, []) or []:
            if package.get("id") == route_id:
                return (package.get("proposal") or {}).get("title", "")
    return ""


def record(run_dir: Path, route_id: str, iteration: int) -> dict[str, Any] | None:
    """Full iteration record; CodingAgent's per-turn final text is kept, raw session logs are not included."""
    if not RUN_ID.match(route_id):
        return None
    return _json(run_dir / f"step2/{route_id}/i{iteration}/record.json")


def add_lineage(found: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> None:
    """Which iteration each one was built on, from api.route_summaries (the core back-fills old runs too)."""
    lineage = {(r.get("route"), it.get("iteration")): it for r in summaries for it in r.get("iterations", [])}
    for route in found:
        for row in route["iterations"]:
            source = lineage.get((route["id"], row["iteration"])) or {}
            row["parent_iteration"], row["base_commit"] = source.get("parent_iteration"), source.get("base_commit")


def plot_file(run_dir: Path, route_id: str, iteration: int | None, name: str) -> Path | None:
    """An image the core copied into runs/: step2/<route>/i<n>/plots/<name>, or step2/plots/baseline_<...>."""
    if not PLOT_NAME.match(name) or not RUN_ID.match(route_id):
        return None
    base = run_dir / "step2/plots" if name.startswith("baseline_") else run_dir / f"step2/{route_id}/i{iteration}/plots"
    path = base / name
    return path if path.is_file() and path.resolve().is_relative_to(run_dir.resolve()) else None


def _routes_for_state(run_dir: Path, events: list[dict[str, Any]], state: str) -> list[dict[str, Any]]:
    """An 'active' route of a run whose process is gone is not evolving: show it as interrupted."""
    found = routes(run_dir, events)
    if state != "running":
        for route in found:
            if route["status"] == "active":
                route["status"], route["running_iteration"] = "interrupted", None
    return found


def summary(run_dir: Path, status: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """`status` is api.status(run_id); `events` is api.events(run_id) with line indices."""
    spec = contract(run_dir)
    last = events[-1] if events else None
    phase = str(status.get("phase", "unknown"))
    state = derive_state(phase, bool(status.get("running")), events)
    return {"id": run_dir.name, "title": spec.get("title", ""), "task_id": spec.get("id", ""), "state": state,
            "kind": status.get("kind", "rsi"), "alerts": alerts(state, status.get("health")),
            "phase": phase, "pid": status.get("pid"), "step1_round": status.get("step1_round"),
            "started": events[0]["ts"] if events else None,
            "last_event": {"ts": last["ts"], "stage": last["stage"], "message": last["message"]} if last else None,
            "events": len(events), "has_report": (run_dir / "report.md").exists(),
            "routes": [{"id": r["id"], "status": r["status"], "iterations": len(r["iterations"])}
                       for r in _routes_for_state(run_dir, events, state)]}


def detail(run_dir: Path, status: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    head = summary(run_dir, status, events)
    proposal = run_dir / "step1/r0/user_proposal.md"  # refine runs: the user's own design, as submitted
    found = _routes_for_state(run_dir, events, head["state"])
    spec = contract(run_dir)
    for route in found:
        selected = recommended_iteration([route], spec)
        route["recommended_iteration"] = selected["iteration"] if selected else None
    return {**head, "contract": spec, "metrics": metrics_of(run_dir), "step1": step1_rounds(run_dir), "routes": found,
            "recommended": recommended_iteration(found, spec),
            "datasets": sorted({d for r in found for it in r["iterations"] for d in it["datasets"]}, key=dataset_order),
            "user_proposal": proposal.read_text(encoding="utf-8", errors="replace") if proposal.exists() else None,
            "implementation": status.get("implementation")}
